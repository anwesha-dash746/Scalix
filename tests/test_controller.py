"""AWS controller safety rules, one test (or more) per rule. No AWS, no boto3, no credentials.

The monitors are scripted (MockMonitor, or a live-labelled test double for the apply path), and the
writer is a recording fake. The real CapacityWriter is tested with Boto3's Stubber in
test_controller_stubbed.py.
"""

from __future__ import annotations

import json
import math
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

import pytest

from scalix.aws.audit import AuditLog
from scalix.aws.controller import MOCK_SCENARIOS, Controller, Outcome, main
from scalix.aws.monitoring import (
    GroupState,
    InstanceInfo,
    MetricReading,
    MetricStatus,
    MockMonitor,
    MonitoringSnapshot,
)
from scalix.aws.writer import CapacityWriter, LiveActionBlocked
from scalix.config import (
    AwsSettings,
    ConfigError,
    ControllerSettings,
    LiveSettings,
    Mode,
    ScalixConfig,
)
from scalix.sources import DataSource

from tests_helpers_controller import AWS, GROUP, T0, LiveLikeMonitor, live_config, read_only_config


def monitor(cpu=85.0, desired=1, in_service=None, min_size=1, max_size=2, live=True, name=GROUP, **kw):
    cls = LiveLikeMonitor if live else MockMonitor
    return cls([cpu], desired=desired, in_service=desired if in_service is None else in_service,
               min_size=min_size, max_size=max_size, name=name,
               clock=lambda: datetime.fromtimestamp(T0, timezone.utc), **kw)


class FakeWriter:
    def __init__(self, error: Exception | None = None, audit: AuditLog | None = None):
        self.calls: list[tuple[str, int]] = []
        self.error = error
        self.audit = audit
        self.audit_seen_at_call: list[str] = []

    def set_desired_capacity(self, group: str, desired: int) -> None:
        if self.audit is not None:
            self.audit_seen_at_call = [e["outcome"] for e in self.audit.entries()]
        self.calls.append((group, desired))
        if self.error:
            raise self.error


class Factory:
    """Counts how often the controller asks for a writer (it should only when applying)."""

    def __init__(self, writer):
        self.writer, self.created = writer, 0

    def __call__(self):
        self.created += 1
        return self.writer


def controller(tmp_path, config=None, mon=None, writer=None, clock=T0, kill=None, on_ec2=False, audit=None):
    audit = audit or AuditLog(tmp_path / "audit.jsonl")
    factory = Factory(writer) if writer is not None else None
    c = Controller(config or live_config(), mon or monitor(), audit=audit, writer_factory=factory,
                   clock=(clock if callable(clock) else (lambda: clock)), kill_switch=lambda: kill,
                   on_ec2=lambda: on_ec2, lock_path=tmp_path / "controller.lock")
    return c, audit, factory


def apply(c):
    return c.tick(apply=True, confirm_group=GROUP)


def failed(result):
    return {ch.name for ch in result.checks + result.live_gates if not ch.ok}


# --------------------------------------------------------------------------- happy path (offline)

def test_all_gates_pass_one_write_is_made_and_audited(tmp_path):
    writer = FakeWriter()
    c, audit, factory = controller(tmp_path, writer=writer)
    writer.audit = audit
    r = apply(c)
    assert r.outcome is Outcome.APPLIED and (r.desired_before, r.desired_proposed) == (1, 2)
    assert writer.calls == [(GROUP, 2)] and factory.created == 1
    assert writer.audit_seen_at_call == ["APPLYING"]  # intent recorded before the call
    entries = audit.entries()
    assert [e["outcome"] for e in entries] == ["APPLYING", "APPLIED"]
    assert entries[0]["action_id"] == entries[1]["action_id"]


# --------------------------------------------------------------------------- 9. dry run by default

def test_dry_run_is_the_default_and_never_creates_a_writer(tmp_path):
    writer = FakeWriter()
    c, audit, factory = controller(tmp_path, writer=writer)
    r = c.tick()
    assert r.outcome is Outcome.DRY_RUN and r.dry_run
    assert "would change desired 1 -> 2" in r.reason
    assert writer.calls == [] and factory.created == 0
    assert "No AWS change was made." in r.render()
    assert [e["outcome"] for e in audit.entries()] == ["DRY_RUN"]


# --------------------------------------------------------------------------- 2. allowlist

def test_group_not_on_allowlist_is_refused_even_in_a_dry_run(tmp_path):
    c, _, _ = controller(tmp_path, config=read_only_config())  # empty allowlist
    r = c.tick()
    assert r.outcome is Outcome.REFUSED and "group on allowlist" in failed(r)


def test_live_config_cannot_target_a_group_outside_the_allowlist():
    with pytest.raises(ConfigError, match="allowed_groups"):
        ScalixConfig(mode=Mode.LIVE, aws=AWS, live=LiveSettings(enabled=True, allowed_groups=("other",)))


# --------------------------------------------------------------------------- 3. validate target and sizes

def test_monitored_group_must_be_the_configured_target(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=monitor(name="someone-elses-group"), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.HOLD and "group is the target" in failed(r) and writer.calls == []


def test_group_starting_from_zero_is_not_touched(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=monitor(desired=0, in_service=0, min_size=0), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.HOLD and "group has at least one worker" in failed(r) and writer.calls == []


def test_group_still_changing_is_not_touched(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=monitor(desired=2, in_service=1), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.HOLD and "group stable (no change in progress)" in failed(r)
    assert writer.calls == []


# --------------------------------------------------------------------------- 4. approved maximum

def test_desired_above_the_approved_maximum_is_never_touched(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=monitor(cpu=5.0, desired=3, max_size=5), writer=writer)
    r = apply(c)  # even a scale-DOWN is not made automatically: a person must look first
    assert r.outcome is Outcome.HOLD and "desired within approved maximum" in failed(r)
    assert writer.calls == []


def test_never_proposes_above_the_approved_maximum_even_if_the_group_allows_more(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=monitor(cpu=99.0, desired=2, max_size=10), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.HOLD and "max 2" in r.decision.reason  # policy limits = min(group 10, approved 2)
    assert writer.calls == []


def test_bigger_steps_are_reduced_to_max_step(tmp_path):
    cfg = replace(live_config(max_capacity=4), controller=ControllerSettings(policy="SCALIX"))
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, config=cfg, mon=monitor(cpu=100.0, desired=2, max_size=4), writer=writer)
    r = apply(c)
    assert r.decision.delta == 2  # SCALIX sized the step for 60%: 2 busy workers / 0.6 -> 4...
    assert r.outcome is Outcome.APPLIED and writer.calls == [(GROUP, 3)]  # ...but only +1 is applied
    assert "reduced to +1 (live.max_step)" in r.reason


def test_writer_rechecks_bounds_at_the_moment_of_use():
    w = CapacityWriter(live_config(), autoscaling_client=_NoNetworkClient(), kill_switch=lambda: None)
    for bad in (0, 3, -1, True, 1.5):
        with pytest.raises(LiveActionBlocked):
            w.set_desired_capacity(GROUP, bad)
    with pytest.raises(LiveActionBlocked):
        w.set_desired_capacity("other-group", 1)


# --------------------------------------------------------------------------- 5a. controller cooldown

def _past_change(audit: AuditLog, seconds_ago: float, before=1, after=2, outcome="APPLIED", action="a1"):
    audit.append({"epoch": T0 - seconds_ago, "group": GROUP, "outcome": outcome, "action_id": action,
                  "desired_before": before, "desired_after": after})


def test_controller_cooldown_blocks_a_second_change(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    _past_change(audit, 120, before=2, after=1)
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, audit=audit)
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "controller cooldown" in failed(r) and writer.calls == []


def test_controller_cooldown_expires(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    _past_change(audit, 300, before=2, after=1)
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, audit=audit)
    assert apply(c).outcome is Outcome.APPLIED


def test_immediate_repeat_after_an_applied_change_is_blocked(tmp_path):
    writer = FakeWriter()
    c, audit, _ = controller(tmp_path, writer=writer)
    assert apply(c).outcome is Outcome.APPLIED
    again = apply(c)  # same data, same moment: the policy's own cooldown already holds
    assert again.outcome is Outcome.HOLD and again.decision.cooldown_blocked
    assert writer.calls == [(GROUP, 2)]


def test_controller_cooldown_applies_even_if_the_policy_has_none(tmp_path):
    cfg = replace(live_config(), controller=ControllerSettings(policy_settings={"cooldown_seconds": 0}))
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, config=cfg, writer=writer)
    assert apply(c).outcome is Outcome.APPLIED
    again = apply(c)
    assert again.outcome is Outcome.REFUSED and "controller cooldown" in failed(again)
    assert writer.calls == [(GROUP, 2)]


def test_policy_cooldown_is_fed_from_the_audit_log(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    _past_change(audit, 30, before=2, after=1)  # a scale-down 30 s ago
    c, _, _ = controller(tmp_path, config=live_config(min_seconds_between_actions=60), audit=audit)
    r = c.tick()
    assert r.decision.cooldown_blocked and r.outcome is Outcome.HOLD  # THRESHOLD's 60 s cooldown


# --------------------------------------------------------------------------- 5b. duplicates

def test_unresolved_earlier_change_blocks_everything(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    _past_change(audit, 3600, outcome="APPLYING", action="crashed")  # no APPLIED/FAILED ever written
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, audit=audit)
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "not a duplicate / nothing unresolved" in failed(r)
    assert writer.calls == []


def test_failed_earlier_change_does_not_count_as_a_change(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    _past_change(audit, 30, outcome="APPLYING", action="x")
    _past_change(audit, 30, outcome="FAILED", action="x")
    assert audit.last_change(GROUP) is None


def test_concurrent_run_holding_the_lock_is_refused(tmp_path):
    (tmp_path / "controller.lock").write_text("pid 1\n")
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, clock=lambda: (tmp_path / "controller.lock").stat().st_mtime + 5)
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "controller.lock" in r.reason and writer.calls == []


def test_lock_is_released_after_a_run(tmp_path):
    c, _, _ = controller(tmp_path, writer=FakeWriter())
    apply(c)
    assert not (tmp_path / "controller.lock").exists()


# --------------------------------------------------------------------------- 5c. audit log

@pytest.mark.parametrize("mon_kwargs, expected", [
    (dict(cpu=45.0), "HOLD"), (dict(cpu=None), "HOLD"), (dict(cpu=85.0), "DRY_RUN"),
])
def test_every_run_is_audited(tmp_path, mon_kwargs, expected):
    c, audit, _ = controller(tmp_path, mon=monitor(**mon_kwargs))
    c.tick()
    (entry,) = audit.entries()
    assert entry["outcome"] == expected and entry["group"] == GROUP and entry["data_source"] == "LIVE_AWS"
    assert entry["checks"] and "reason" in entry


def test_corrupt_audit_log_means_no_change(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text('{"epoch": 1, "group": "scalix-workers"}\nnot json at all\n')
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, audit=AuditLog(path))
    with pytest.raises(Exception):
        AuditLog(path).entries()
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "audit log readable" in failed(r) and writer.calls == []


def test_audit_entries_are_redacted(tmp_path):
    audit = AuditLog(tmp_path / "audit.jsonl")
    audit.append({"epoch": T0, "group": GROUP, "reason": "arn:aws:sts::123456789012:assumed-role/x"})
    assert "123456789012" not in (tmp_path / "audit.jsonl").read_text()


# --------------------------------------------------------------------------- 5d. global live-action disable

def test_kill_switch_blocks_apply_but_dry_run_still_reports(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, kill="kill switch file LIVE_DISABLED exists")
    assert c.tick().outcome is Outcome.DRY_RUN
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "kill switch off" in failed(r) and writer.calls == []


def test_kill_switch_is_rechecked_inside_the_writer(tmp_path):
    w = CapacityWriter(live_config(), autoscaling_client=_NoNetworkClient(), kill_switch=lambda: "file exists")
    with pytest.raises(LiveActionBlocked, match="disabled"):
        w.set_desired_capacity(GROUP, 2)


def test_kill_switch_engaged_between_checks_and_write_fails_safely(tmp_path):
    """If the switch is flipped after the gates passed, the writer still refuses; nothing is sent."""
    client = _NoNetworkClient()
    state = {"kill": None}
    real = CapacityWriter(live_config(), autoscaling_client=client, kill_switch=lambda: state["kill"])

    class FlipThenWrite:
        def set_desired_capacity(self, group, desired):
            state["kill"] = "flipped at the last moment"
            real.set_desired_capacity(group, desired)

    c, audit, _ = controller(tmp_path, writer=FlipThenWrite())
    r = apply(c)
    assert r.outcome is Outcome.FAILED and "LiveActionBlocked" in r.reason and client.calls == []
    assert [e["outcome"] for e in audit.entries()] == ["APPLYING", "FAILED"]


# --------------------------------------------------------------------------- 6. untrustworthy data

@pytest.mark.parametrize("mon_kwargs, status", [
    (dict(cpu=None), "MISSING"),
    (dict(cpu=60.0, reading_age_seconds=2400), "STALE"),
    (dict(cpu=float("nan")), "MALFORMED"),
    (dict(cpu=250.0), "MALFORMED"),
])
def test_never_acts_on_bad_monitoring_data(tmp_path, mon_kwargs, status):
    writer = FakeWriter()
    c, _, factory = controller(tmp_path, mon=monitor(**mon_kwargs), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.HOLD and r.cpu_status == status and r.cpu_value is None
    assert "CPU reading usable" in failed(r)
    assert writer.calls == [] and factory.created == 0


class ErrorMonitor:
    source = DataSource.LIVE_AWS
    name = GROUP

    def __init__(self, group_ok=True):
        self.group_ok = group_ok

    def snapshot(self):
        now = datetime.fromtimestamp(T0, timezone.utc)
        cpu = MetricReading(MetricStatus.ERROR, None, None, None, None,
                            "cloudwatch:GetMetricStatistics: AccessDenied: not authorized", self.source)
        group = GroupState(GROUP, 1, 2, 1, (InstanceInfo("i-1", "InService", "Healthy"),), self.source, now) \
            if self.group_ok else None
        errors = (cpu.reason,) if self.group_ok else ("ExpiredToken: token expired", cpu.reason)
        return MonitoringSnapshot(now, self.source, group, cpu, errors)


@pytest.mark.parametrize("group_ok", [True, False])
def test_never_acts_when_monitoring_calls_fail(tmp_path, group_ok):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=ErrorMonitor(group_ok), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.HOLD and "monitoring errors" in failed(r) and writer.calls == []


# --------------------------------------------------------------------------- 7. READ_ONLY cannot write

def test_read_only_mode_cannot_apply(tmp_path):
    cfg = read_only_config(allowed_groups=(GROUP,))
    writer = FakeWriter()
    c, _, factory = controller(tmp_path, config=cfg, writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.REFUSED
    assert {"mode is LIVE", "live.enabled"} <= failed(r)
    assert writer.calls == [] and factory.created == 0


def test_writer_cannot_be_built_outside_live_mode():
    for cfg in (ScalixConfig(), read_only_config(allowed_groups=(GROUP,))):
        with pytest.raises(ConfigError, match="LIVE"):
            CapacityWriter(cfg, autoscaling_client=_NoNetworkClient())


# --------------------------------------------------------------------------- 8. LIVE off by default

def test_live_is_off_by_default():
    cfg = ScalixConfig()
    assert cfg.mode is Mode.SIMULATION and not cfg.live.enabled and cfg.live.allowed_groups == ()


def test_simulation_mode_never_monitors_aws(tmp_path):
    with pytest.raises(ValueError, match="SIMULATION"):
        Controller(ScalixConfig(), monitor(), audit=AuditLog(tmp_path / "a.jsonl"))


# --------------------------------------------------------------------------- operator confirmation, data source, host

def test_apply_needs_the_exact_group_name_confirmed(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer)
    for confirm in (None, "", "scalix-worker", "SCALIX-WORKERS"):
        r = c.tick(apply=True, confirm_group=confirm)
        assert r.outcome is Outcome.REFUSED and "group confirmed by operator" in failed(r)
    assert writer.calls == []


def test_mocked_data_can_never_be_applied(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, mon=monitor(live=False), writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "data is live AWS data" in failed(r) and writer.calls == []


def test_refuses_to_run_on_an_ec2_instance(tmp_path):
    writer = FakeWriter()
    c, _, _ = controller(tmp_path, writer=writer, on_ec2=True)
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "not running on an EC2 instance" in failed(r) and writer.calls == []


def test_no_writer_means_no_apply(tmp_path):
    c, _, _ = controller(tmp_path)  # LIVE config, but no writer factory supplied
    r = apply(c)
    assert r.outcome is Outcome.REFUSED and "writer available" in failed(r)


# --------------------------------------------------------------------------- 10. AWS errors on the write

class FakeClientError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.response = {"Error": {"Code": code, "Message": message}}


@pytest.mark.parametrize("code", ["ScalingActivityInProgress", "ValidationError", "AccessDenied",
                                  "ExpiredToken", "ResourceContention"])
def test_aws_errors_on_the_write_are_recorded_and_never_retried(tmp_path, code):
    writer = FakeWriter(error=FakeClientError(code, "arn:aws:iam::123456789012:x refused"))
    c, audit, _ = controller(tmp_path, writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.FAILED and code in r.reason and "not retried" in r.reason
    assert len(writer.calls) == 1
    assert [e["outcome"] for e in audit.entries()] == ["APPLYING", "FAILED"]
    assert "123456789012" not in r.render() and "123456789012" not in audit.path.read_text()
    assert audit.last_change(GROUP) is None  # nothing changed, so no cooldown starts


def test_network_error_on_the_write(tmp_path):
    class EndpointConnectionError(Exception):
        pass

    writer = FakeWriter(error=EndpointConnectionError("could not connect"))
    c, _, _ = controller(tmp_path, writer=writer)
    r = apply(c)
    assert r.outcome is Outcome.FAILED and "EndpointConnectionError" in r.reason and len(writer.calls) == 1


# --------------------------------------------------------------------------- policies on AWS data

def test_scalix_without_a_demand_signal_never_scales_down(tmp_path):
    """demand_signal NONE (the Phase 4 behaviour): the trend stays UNKNOWN, so SCALIX holds forever."""
    cfg = replace(live_config(), controller=ControllerSettings(policy="SCALIX", demand_signal="NONE"))
    times = iter(T0 + 600 * k for k in range(10))
    c, _, _ = controller(tmp_path, config=cfg, mon=monitor(cpu=5.0, desired=2), clock=lambda: next(times))
    for _ in range(5):
        r = c.tick()
        assert r.outcome is Outcome.HOLD and "UNKNOWN" in r.decision.reason


def test_scalix_with_the_cpu_demand_signal_can_scale_down(tmp_path):
    """Phase 7: CPU x in-service instances gives SCALIX a trend, so a steady quiet group scales down."""
    cfg = replace(live_config(), controller=ControllerSettings(policy="SCALIX"))
    assert cfg.controller.demand_signal == "CPU"
    times = iter(T0 + 600 * k for k in range(10))
    c, audit, _ = controller(tmp_path, config=cfg, mon=monitor(cpu=5.0, desired=2), clock=lambda: next(times))
    first = c.tick()
    assert first.outcome is Outcome.HOLD and "UNKNOWN" in first.decision.reason   # 1 sample: no trend yet
    second = c.tick()
    assert second.decision.trend.value == "STABLE"
    assert second.outcome is Outcome.DRY_RUN and second.desired_proposed == 1
    assert audit.busy_history(GROUP) == [0.10, 0.10]                              # 5% x 2 instances


def test_cpu_demand_signal_rises_with_load_and_skips_old_entries(tmp_path):
    from scalix.algorithms.trend import estimate_trend
    audit = AuditLog(tmp_path / "audit.jsonl")
    cfg = replace(live_config(), controller=ControllerSettings(policy="THRESHOLD"))
    times = iter(T0 + 600 * k for k in range(10))
    clock = lambda: next(times)  # noqa: E731
    for cpu in (30.0, 40.0, 50.0):
        c, _, _ = controller(tmp_path, config=cfg, mon=monitor(cpu=cpu, desired=1), clock=clock, audit=audit)
        c.tick()
    with audit.path.open("a") as f:   # a pre-Phase-9 entry without in_service is ignored
        f.write(json.dumps({"epoch": T0 + 9000, "group": GROUP, "outcome": "HOLD", "cpu_status": "OK", "cpu_value": 0.9}) + "\n")
    assert audit.busy_history(GROUP) == pytest.approx([0.30, 0.40, 0.50])
    assert estimate_trend(audit.busy_history(GROUP), 6, 0.03).trend.value == "RISING"


def test_static_policy_never_proposes_changes(tmp_path):
    cfg = replace(live_config(), controller=ControllerSettings(policy="STATIC"))
    c, _, _ = controller(tmp_path, config=cfg, mon=monitor(cpu=99.0))
    assert c.tick().outcome is Outcome.HOLD


def test_cpu_history_from_the_audit_feeds_scalix_caution(tmp_path):
    cfg = replace(live_config(), controller=ControllerSettings(policy="THRESHOLD"))
    c, audit, _ = controller(tmp_path, config=cfg, mon=monitor(cpu=45.0))
    for _ in range(3):
        c.tick()
    assert audit.cpu_history(GROUP) == [0.45, 0.45, 0.45]


# --------------------------------------------------------------------------- 11. dashboard cannot enable LIVE

DASHBOARD = Path(__file__).resolve().parent.parent / "dashboard"


def test_dashboard_code_has_no_path_to_aws_writes():
    forbidden = ("scalix.aws", "set_desired_capacity", "CapacityWriter", "apply=True", "--apply",
                 "scalix.local.yaml", "live_enabled", "LIVE_DISABLED", "allowed_groups")
    offenders = []
    for py in DASHBOARD.rglob("*.py"):
        if "tests" in py.parts:
            continue
        text = py.read_text(encoding="utf-8")
        offenders += [f"{py.relative_to(DASHBOARD)}: {word}" for word in forbidden if word in text]
    assert offenders == []


def test_dashboard_runs_without_touching_the_controller_or_writer(monkeypatch):
    from streamlit.testing.v1 import AppTest

    import scalix.aws.controller as controller_module
    import scalix.aws.writer as writer_module

    def boom(*a, **k):
        raise AssertionError("the dashboard tried to use the AWS controller or writer")

    monkeypatch.setattr(writer_module.CapacityWriter, "__init__", boom)
    monkeypatch.setattr(controller_module.Controller, "tick", boom)
    at = AppTest.from_file(str(DASHBOARD.parent / "app.py"), default_timeout=60).run()
    assert not at.exception


# --------------------------------------------------------------------------- command line

def test_cli_mock_dry_run(tmp_path, capsys):
    assert main(["--mock", "overload", "--audit", str(tmp_path / "a.jsonl")]) == 0
    out = capsys.readouterr().out
    assert "MOCKED" in out and "DRY_RUN" in out and "No AWS change was made." in out


@pytest.mark.parametrize("scenario", sorted(MOCK_SCENARIOS))
def test_cli_mock_apply_is_always_refused_or_held(tmp_path, capsys, scenario):
    assert main(["--mock", scenario, "--apply", "--confirm", "mock-scalix-workers",
                 "--audit", str(tmp_path / "a.jsonl")]) == 0
    out = capsys.readouterr().out
    assert "APPLIED" not in out and ("REFUSED" in out or "HOLD" in out)
    outcomes = [json.loads(l)["outcome"] for l in (tmp_path / "a.jsonl").read_text().splitlines()]
    assert set(outcomes) <= {"HOLD", "REFUSED"}


def test_cli_rejects_apply_with_watch_and_simulation_without_mock(tmp_path, capsys, monkeypatch):
    assert main(["--apply", "--watch", "2"]) == 2
    import scalix.config as config_module

    monkeypatch.setattr(config_module, "LOCAL_CONFIG_PATH", tmp_path / "none.yaml")
    assert main([]) == 2
    assert "SIMULATION" in capsys.readouterr().err


# --------------------------------------------------------------------------- helpers

class _NoNetworkClient:
    """Stands in for a boto3 client in writer unit tests; records calls, never sends anything."""

    def __init__(self):
        self.calls = []

        class _Meta:
            class service_model:  # noqa: N801
                operation_names = []

                class service_id:  # noqa: N801
                    @staticmethod
                    def hyphenize():
                        return "auto-scaling"

            class events:  # noqa: N801
                @staticmethod
                def register_first(*a, **k):
                    pass

        self.meta = _Meta

    def set_desired_capacity(self, **kwargs):
        self.calls.append(kwargs)
