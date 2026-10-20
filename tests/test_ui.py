"""The Streamlit dashboard (ui/): every page renders, values are labelled by source, the AWS page
only reads (and only on a button press), failures never crash it, and there is no path to AWS writes.

Pages are rendered headlessly with streamlit.testing.v1.AppTest. AWS is replaced by fakes;
nothing here contacts AWS.
"""

from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

import scalix.aws.audit as audit_module
import scalix.aws.check_access as check_access_module
import scalix.aws.controller as controller_module
import scalix.aws.monitoring as monitoring_module
import scalix.aws.writer as writer_module
import scalix.config as config_module
from scalix.aws.check_access import AccessReport, CheckResult
from scalix.aws.monitoring import GroupState, InstanceInfo, MetricReading, MetricStatus, MonitoringSnapshot
from scalix.sources import DataSource
from ui import charts

ROOT = Path(__file__).resolve().parent.parent
PAGES = ["ui/pages/overview.py", "ui/pages/simulation.py", "ui/pages/comparison.py", "ui/pages/aws.py",
         "ui/pages/history.py"]


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    """Safe defaults for every test: no local config, private audit files, AWS entry points counted."""
    monkeypatch.setattr(config_module, "LOCAL_CONFIG_PATH", tmp_path / "scalix.local.yaml")
    monkeypatch.setattr(audit_module, "DEFAULT_AUDIT_PATH", tmp_path / "audit.jsonl")
    monkeypatch.setattr(audit_module, "MOCK_AUDIT_PATH", tmp_path / "audit-mock.jsonl")
    monkeypatch.setattr(check_access_module, "REPORTS_DIR", tmp_path / "aws-reports")
    calls = {"monitor": 0, "access": 0}

    def no_monitor(*a, **k):
        calls["monitor"] += 1
        raise AssertionError("CloudWatchMonitor used without a button press")

    def no_access(*a, **k):
        calls["access"] += 1
        raise AssertionError("access check run without a button press")

    def no_writer(*a, **k):
        raise AssertionError("the dashboard tried to create a capacity writer")

    real_tick = controller_module.Controller.tick

    def dry_only_tick(self, apply=False, confirm_group=None):
        assert apply is False and confirm_group is None, "the dashboard asked for a real change"
        return real_tick(self)

    monkeypatch.setattr(monitoring_module.CloudWatchMonitor, "from_config", classmethod(no_monitor))
    monkeypatch.setattr(check_access_module, "run_access_check", no_access)
    monkeypatch.setattr(writer_module.CapacityWriter, "__init__", no_writer)
    monkeypatch.setattr(controller_module.Controller, "tick", dry_only_tick)
    return calls


def app() -> AppTest:
    return AppTest.from_file(str(ROOT / "app.py"), default_timeout=120).run()


def markdown_text(at: AppTest) -> str:
    return "\n".join(m.value for m in at.markdown) + "\n".join(c.value for c in at.caption)


def read_only_config(tmp_path):
    (tmp_path / "scalix.local.yaml").write_text(
        "mode: READ_ONLY\naws:\n  profile: scalix-test\n  region: us-east-1\n  auto_scaling_group: scalix-workers\n")


# --------------------------------------------------------------------------- every page

def test_every_page_renders_and_labels_its_data(isolated):
    at = app()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.value for t in at.title] == ["Overview"]
    assert "SIMULATED" in markdown_text(at)
    for page in PAGES[1:]:
        at.switch_page(page).run()
        assert not at.exception, (page, [e.value for e in at.exception])
        text = markdown_text(at)
        assert any(src.value in text for src in DataSource), page
    assert isolated == {"monitor": 0, "access": 0}  # navigation never touched AWS


def test_sidebar_shows_simulation_mode_and_source_legend():
    at = app()
    sidebar = "\n".join(e.value for e in at.sidebar.success) + "\n".join(m.value for m in at.sidebar.markdown)
    assert "Mode: SIMULATION" in sidebar and "AWS is never contacted" in sidebar
    for src in DataSource:
        assert src.value in sidebar


def test_overview_metrics_and_default_run():
    at = app()
    labels = {m.label for m in at.metric}
    for expected in ("Workers at the end", "Workload pattern", "Average utilization", "Peak utilization",
                     "Scaling events", "Worker-minutes", "Estimated cost (USD)"):
        assert expected in labels
    assert at.session_state["is_default_run"] is True
    result = at.session_state["result"]
    assert set(result.summary["policy"]) == {"STATIC", "THRESHOLD", "SCALIX"}
    assert all(m.help for m in at.metric)  # every tile has a tooltip


# --------------------------------------------------------------------------- simulation form

def _number(at, label):
    return next(n for n in at.number_input if n.label == label)


def test_running_a_new_simulation_from_the_form():
    at = app().switch_page("ui/pages/simulation.py").run()
    _number(at, "Base demand (requests/s)").set_value(3.0)
    _number(at, "Runs (seeds)").set_value(1)
    at.button[0].click().run()  # the form's submit button
    assert not at.exception
    assert any("Done" in s.value for s in at.success)
    assert at.session_state["form"]["base_rate"] == 3.0
    assert at.session_state["result"].spec.repetitions == 1
    assert at.session_state["is_default_run"] is False


def test_invalid_settings_are_reported_not_run():
    at = app().switch_page("ui/pages/simulation.py").run()
    before = at.session_state["result"]
    _number(at, "Minimum workers").set_value(10)
    _number(at, "Starting workers").set_value(5)
    at.button[0].click().run()
    assert not at.exception
    assert any("Not run" in e.value for e in at.error)
    assert at.session_state["result"] is before


# --------------------------------------------------------------------------- comparison

def test_comparison_table_is_honest_about_trade_offs():
    at = app().switch_page("ui/pages/comparison.py").run()
    table = at.dataframe[0].value
    assert {"Static (fixed)", "Threshold (70/30)", "SCALIX (workload-aware)", "Best (95% CI)"} <= set(table.columns)
    scale_rows = table[table["Metric"].isin(["Scale-ups", "Scale-downs"])]
    assert scale_rows["Best (95% CI)"].str.startswith("context only").all()
    verdicts = " ".join(table["Best (95% CI)"])
    assert "clear lead" in verdicts or "too close to call" in verdicts
    log = at.dataframe[1].value
    assert list(log["minute"]) == sorted(log["minute"])  # decisions in time order
    assert log["reason"].str.len().gt(0).all()


# --------------------------------------------------------------------------- AWS page

def test_aws_page_in_simulation_mode_never_contacts_aws(isolated):
    at = app().switch_page("ui/pages/aws.py").run()
    assert not at.exception
    text = markdown_text(at)
    assert "AWS reading is switched off" in text and "MOCKED" in text
    assert not [b for b in at.button if b.key == "live_read"]  # only the mocked tab has buttons
    assert isolated == {"monitor": 0, "access": 0}


class FakeMonitor:
    def __init__(self, snapshot=None, error=None):
        self._snapshot, self._error = snapshot, error

    def snapshot(self):
        if self._error:
            raise self._error
        return self._snapshot


def _snapshot(status=MetricStatus.ERROR, value=None, errors=("cloudwatch:GetMetricStatistics: AccessDenied: no",)):
    now = datetime.now(timezone.utc)
    cpu = MetricReading(status, value, value, now, 300.0, "test reading", DataSource.LIVE_AWS)
    group = GroupState("scalix-workers", 1, 2, 1, (InstanceInfo("i-1", "InService", "Healthy"),),
                       DataSource.LIVE_AWS, now)
    return MonitoringSnapshot(now, DataSource.LIVE_AWS, group, cpu, tuple(errors))


def _use_monitor(monkeypatch, monitor):
    counter = {"n": 0}

    def factory(cls, config):
        counter["n"] += 1
        return monitor

    monkeypatch.setattr(monitoring_module.CloudWatchMonitor, "from_config", classmethod(factory))
    return counter


def test_aws_page_reads_only_when_the_button_is_pressed(monkeypatch, tmp_path, isolated):
    read_only_config(tmp_path)
    counter = _use_monitor(monkeypatch, FakeMonitor(_snapshot(MetricStatus.OK, 0.42, errors=())))
    at = app().switch_page("ui/pages/aws.py").run()
    assert not at.exception and counter["n"] == 0  # opening the page made no AWS call
    assert isolated["access"] == 0 and not at.error  # nor an access check
    next(b for b in at.button if b.label == "Read group and CPU now").click().run()
    assert not at.exception and counter["n"] == 1
    assert "LIVE_AWS" in markdown_text(at)
    assert next(m for m in at.metric if m.label == "Average CPU").value == "42%"
    at.run()  # an ordinary refresh does not read again
    assert counter["n"] == 1 and isolated["access"] == 0


def test_aws_errors_are_explained_not_crashed(monkeypatch, tmp_path):
    read_only_config(tmp_path)
    _use_monitor(monkeypatch, FakeMonitor(_snapshot()))
    at = app().switch_page("ui/pages/aws.py").run()
    next(b for b in at.button if b.label == "Read group and CPU now").click().run()
    assert not at.exception
    errors = " ".join(e.value for e in at.error)
    assert "AccessDenied" in errors and "HOLD" in errors
    live_cpu = next(m for m in at.metric if m.label == "Average CPU")  # live tab renders before the mock tab
    assert live_cpu.value == "–"  # an errored reading is shown as unknown, never as 0%


def test_unexpected_aws_exception_does_not_crash(monkeypatch, tmp_path):
    read_only_config(tmp_path)

    class EndpointConnectionError(Exception):
        pass

    _use_monitor(monkeypatch, FakeMonitor(error=EndpointConnectionError("no network")))
    at = app().switch_page("ui/pages/aws.py").run()
    next(b for b in at.button if b.label == "Read group and CPU now").click().run()
    assert not at.exception
    assert any("EndpointConnectionError" in e.value for e in at.error)


def test_access_check_button_shows_denials(monkeypatch, tmp_path):
    read_only_config(tmp_path)
    report = AccessReport(generated_utc="t", environment={},
                          checks=[CheckResult("Describe EC2 instances", "DENIED", "UnauthorizedOperation: no")],
                          calls_made=["ec2:DescribeInstances"])
    monkeypatch.setattr(check_access_module, "run_access_check", lambda settings: report)
    at = app().switch_page("ui/pages/aws.py").run()
    next(b for b in at.button if b.label == "Run access check").click().run()
    assert not at.exception
    table = at.dataframe[0].value
    assert list(table["result"]) == ["DENIED"]


def test_aws_page_never_shows_credentials(tmp_path, monkeypatch):
    read_only_config(tmp_path)
    at = app().switch_page("ui/pages/aws.py").run()
    text = markdown_text(at)
    assert "scalix-test" in text  # the profile *name* is fine
    for secret_word in ("aws_access_key_id", "aws_secret_access_key", "session_token"):
        assert secret_word not in text


# --------------------------------------------------------------------------- history

def test_history_marks_actual_and_simulated_actions(tmp_path):
    entries = [
        {"epoch": 1, "time_utc": "2026-10-20T10:00:00+00:00", "group": "g", "outcome": "DRY_RUN",
         "data_source": "LIVE_AWS", "desired_before": 1, "desired_after": 2, "reason": "would change"},
        {"epoch": 2, "time_utc": "2026-10-20T10:10:00+00:00", "group": "g", "outcome": "APPLIED",
         "data_source": "LIVE_AWS", "desired_before": 1, "desired_after": 2, "reason": "set desired 1 -> 2"},
    ]
    (tmp_path / "audit.jsonl").write_text("\n".join(json.dumps(e) for e in entries) + "\n")
    at = app().switch_page("ui/pages/history.py").run()
    assert not at.exception
    sim, live = at.dataframe[0].value, at.dataframe[1].value
    assert (sim["actual AWS change?"] == "no (simulated)").all()
    assert list(live["actual AWS change?"]) == ["YES", "no"]  # newest first
    assert any("Actual AWS changes recorded: 1" in c.value for c in at.caption)


def test_corrupt_audit_log_is_reported(tmp_path):
    (tmp_path / "audit.jsonl").write_text("not json\n")
    at = app().switch_page("ui/pages/history.py").run()
    assert not at.exception
    assert any("could not be read" in e.value for e in at.error)


def test_mocked_dry_run_from_the_dashboard_is_recorded_and_never_applies(tmp_path):
    at = app().switch_page("ui/pages/history.py").run()
    next(b for b in at.button if b.label == "Run one mocked dry run").click().run()
    assert not at.exception
    assert any("No AWS change was made." in c.value for c in at.code)
    lines = (tmp_path / "audit-mock.jsonl").read_text().splitlines()
    assert len(lines) == 1 and json.loads(lines[0])["outcome"] in {"HOLD", "DRY_RUN", "REFUSED"}
    assert json.loads(lines[0])["data_source"] == "MOCKED"
    assert not (tmp_path / "audit.jsonl").exists()  # the real audit log is untouched


# --------------------------------------------------------------------------- static safety scan

def test_ui_code_has_no_path_to_aws_writes_or_secrets():
    forbidden = ("set_desired_capacity", "CapacityWriter", "scalix.aws.writer", "apply=True", "--apply",
                 "writer_factory", "confirm_group", "LIVE_DISABLED", "get_credentials", "secret_access_key",
                 "write_text", "boto3")
    offenders = []
    for py in (ROOT / "ui").rglob("*.py"):
        text = py.read_text(encoding="utf-8")
        offenders += [f"{py.relative_to(ROOT)}: {w}" for w in forbidden if w in text]
    assert offenders == []


# --------------------------------------------------------------------------- charts

def test_charts_one_axis_fixed_colours_and_legends():
    import pandas as pd

    ts = pd.DataFrame({"time_s": [0, 5] * 3, "policy": ["STATIC"] * 2 + ["THRESHOLD"] * 2 + ["SCALIX"] * 2,
                       "provisioned": [8, 8, 8, 9, 8, 10], "utilization": [0.5] * 6, "queue": [0.0] * 6})
    fig = charts.workers_chart(ts)
    assert [t.name for t in fig.data] == [charts.POLICY_LABELS[p] for p in charts.POLICY_ORDER]
    assert [t.line.color for t in fig.data] == [charts.POLICY_COLORS[p] for p in charts.POLICY_ORDER]
    assert fig.layout.showlegend is True and "yaxis2" not in fig.layout
    one = charts.workers_chart(ts, policies=("SCALIX",))
    assert one.layout.showlegend is False and one.data[0].line.color == charts.POLICY_COLORS["SCALIX"]
    util = charts.utilization_chart(ts[ts["policy"] != "STATIC"])  # colours follow the entity, not the rank
    assert [t.line.color for t in util.data] == [charts.POLICY_COLORS["THRESHOLD"], charts.POLICY_COLORS["SCALIX"]]


def test_metric_bars_show_intervals_when_available():
    import pandas as pd

    agg = pd.DataFrame({"policy": list(charts.POLICY_ORDER), "metric": ["m"] * 3, "n": [3] * 3,
                        "mean": [1.0, 2.0, 3.0], "ci95_low": [0.9, 1.8, 2.5], "ci95_high": [1.1, 2.2, 3.5]})
    fig = charts.metric_bars(agg, "m", "M")
    assert fig.data[0].error_y.visible and fig.data[0].text is None
    single = agg.assign(ci95_low=math.nan, ci95_high=math.nan, n=1)
    fig1 = charts.metric_bars(single, "m", "M")
    assert not fig1.data[0].error_y.visible and list(fig1.data[0].text) == ["1.00", "2.00", "3.00"]


# --------------------------------------------------------------------------- budget and saved connection check

def _saved_report(tmp_path, identity_status="OK"):
    folder = tmp_path / "aws-reports"
    folder.mkdir(exist_ok=True)
    report = {"generated_utc": "2026-10-20T22:14:07+00:00",
              "environment": {"selected_profile": "scalix-academy", "temporary_credentials": True},
              "checks": [{"check": "Credentials", "status": "OK", "detail": "found"},
                         {"check": "Caller identity", "status": identity_status,
                          "detail": "account ********7325, identity arn:aws:sts::********7325:assumed-role/voclabs/use…"},
                         {"check": "Describe Auto Scaling groups", "status": "OK", "detail": "permitted; 0 group(s)"}],
              "calls_made": ["sts:GetCallerIdentity"]}
    (folder / "access-report-20261021-034412.json").write_text(json.dumps(report))
    (folder / "access-report-20261020-000000.json").write_text("not json")


def test_aws_page_states_the_strict_budget_in_every_mode(tmp_path, isolated):
    for setup in (lambda: None, lambda: read_only_config(tmp_path)):
        setup()
        at = app().switch_page("ui/pages/aws.py").run()
        assert not at.exception
        warnings = "\n".join(w.value for w in at.warning)
        assert "**Strict AWS budget: USD 20 only.**" in warnings
    assert isolated == {"monitor": 0, "access": 0}


def test_aws_page_shows_the_last_saved_check_without_contacting_aws(tmp_path, isolated):
    _saved_report(tmp_path)
    at = app().switch_page("ui/pages/aws.py").run()
    assert not at.exception
    ok = "\n".join(s.value for s in at.success)
    assert "connected (read-only)" in ok and "scalix-academy" in ok and "Oct 2026" in ok
    assert "SIMULATION" in markdown_text(at)
    assert isolated == {"monitor": 0, "access": 0}


def test_aws_page_reports_a_failed_saved_check(tmp_path, isolated):
    _saved_report(tmp_path, identity_status="ERROR")
    read_only_config(tmp_path)
    at = app().switch_page("ui/pages/aws.py").run()
    assert not at.exception
    assert any("not connected" in e.value for e in at.error)
    assert isolated == {"monitor": 0, "access": 0}


def test_latest_saved_report_skips_unreadable_files(tmp_path):
    assert check_access_module.latest_saved_report(tmp_path / "missing") is None
    _saved_report(tmp_path)
    (tmp_path / "aws-reports" / "access-report-20991231-000000.json").write_text("{broken")
    path, data = check_access_module.latest_saved_report(tmp_path / "aws-reports")
    assert path.name == "access-report-20261021-034412.json" and data["checks"][1]["status"] == "OK"


# --------------------------------------------------------------------------- mocked tab mirrors the live tab

def test_mocked_tab_has_the_live_tabs_controls_and_views_without_contacting_aws(tmp_path, isolated):
    read_only_config(tmp_path)
    at = app().switch_page("ui/pages/aws.py").run()
    labels = {b.key: b.label for b in at.button}
    assert labels["live_read"] == labels["mock_read"] == "Read group and CPU now"
    assert labels["live_check"] == labels["mock_check"] == "Run access check"
    next(b for b in at.button if b.key == "mock_read").click().run()
    next(b for b in at.button if b.key == "mock_check").click().run()
    assert not at.exception
    metrics = {m.label for m in at.metric}
    assert {"Desired workers", "In service", "Starting up", "Allowed range", "Average CPU"} <= metrics
    assert "Access check" in [h.value for h in at.subheader]
    table = at.dataframe[-1].value
    assert "Caller identity" in list(table["check"]) and set(table["result"]) <= {"OK", "INFO"}
    assert "MOCKED" in markdown_text(at)
    assert isolated == {"monitor": 0, "access": 0}  # the mocked buttons never touch AWS


# --------------------------------------------------------------------------- animated charts

def test_live_charts_carry_exactly_the_simulated_data_and_replay_in_under_30_seconds():
    import re

    from ui import common, live_chart

    result = common.run(common.DEFAULT_FORM)
    ts = result.timeseries[(result.timeseries["scenario"] == "MIXED") & (result.timeseries["seed"] == 42)]
    wl = result.workloads[(result.workloads["scenario"] == "MIXED") & (result.workloads["seed"] == 42)]
    specs = [charts.live_workers(ts), charts.live_queue(ts), charts.live_utilization(ts, 0.7), charts.live_demand(wl)]
    for spec in specs:
        assert spec.duration_s <= live_chart.MAX_DURATION_S
        html = live_chart.live_chart_html(spec)
        data = json.loads(re.search(r"const D = (\{.*?\});\n", html).group(1))
        assert data["durationMs"] <= 30_000
        for needle in ("IntersectionObserver", "prefers-reduced-motion", "visibilitychange", "requestAnimationFrame"):
            assert needle in html
        assert "<script src" not in html  # self-contained: works offline, no external library
    payload = live_chart.payload(charts.live_workers(ts))
    for s, p in zip(payload["series"], charts.POLICY_ORDER):
        d = ts[ts["policy"] == p]
        assert s["y"] == [float(v) for v in d["provisioned"]] and s["color"] == charts.POLICY_COLORS[p] and s["step"]
        assert s["x"] == [round(v / 60, 6) for v in d["time_s"]]


def test_live_chart_rejects_long_or_empty_animations():
    from ui.live_chart import ChartSpec, Series

    s = Series("a", [0, 1], [1, 2], "#000")
    with pytest.raises(ValueError, match="duration_s"):
        ChartSpec("t", "y", (s,), duration_s=31)
    with pytest.raises(ValueError, match="at least one series"):
        ChartSpec("t", "y", ())
    with pytest.raises(ValueError, match="matching"):
        ChartSpec("t", "y", (Series("b", [0, 1], [1], "#000"),))
