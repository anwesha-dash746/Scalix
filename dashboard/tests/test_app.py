"""Streamlit app tests with streamlit.testing.v1.AppTest (headless; no browser needed)."""

import sys
from pathlib import Path

import yaml
from streamlit.testing.v1 import AppTest

from conftest import FAKE_ENGINE
from fixtures_util import write_experiment

APP = str(Path(__file__).resolve().parents[1] / "app.py")
PAGES = ["Start", "Results", "Over time", "Decision log", "Download", "Under the hood"]


def settings_file(tmp_path, command):
    cfg = tmp_path / "config"
    cfg.mkdir()
    f = cfg / "dashboard.yaml"
    f.write_text(yaml.safe_dump({
        "engine": {"command": command, "timeout_seconds": 30},
        "results_dir": str(tmp_path / "results"),
        "defaults": {"workloads": ["LOW"], "duration_s": 120, "repetitions": 1},
        "cloud": {"provider": "mock", "cloudstack": {"enabled": False}},
    }))
    return f


def make_app(tmp_path, monkeypatch, command=None):
    monkeypatch.setenv("SCALIX_DASHBOARD_CONFIG", str(settings_file(tmp_path, command or [sys.executable, str(FAKE_ENGINE)])))
    return AppTest.from_file(APP, default_timeout=60)


def goto(at, page):
    at.session_state["pending_nav"] = page
    return at.run()


def texts(at):
    return " ".join(m.value for m in at.markdown)


def test_start_page_without_experiments(tmp_path, monkeypatch):
    at = make_app(tmp_path, monkeypatch).run()
    assert not at.exception
    assert any("Nothing here yet" in i.value for i in at.sidebar.info)
    assert "Rent the right number of servers" in texts(at)
    assert at.button(key="f_submit")
    # Results page explains what to do instead of failing.
    at = goto(at, "Results")
    assert any("no results to show yet" in i.value for i in at.info)


def test_every_page_renders_for_an_experiment(tmp_path, monkeypatch):
    write_experiment(tmp_path / "results" / "exp-a", exp_id="exp-a", workloads=("LOW", "BURST"))
    at = make_app(tmp_path, monkeypatch).run()
    for page in PAGES:
        at = goto(at, page)
        assert not at.exception, (page, at.exception)
        assert at.session_state["nav"] == page
    at = goto(at, "Results")
    body = texts(at)
    assert "Fastest for users" in body and "Cheapest to run" in body and "Strategy by strategy" in [s.value for s in at.subheader]
    at = goto(at, "Download")
    assert at.get("download_button")


def test_run_rejects_invalid_parameters(tmp_path, monkeypatch):
    at = make_app(tmp_path, monkeypatch).run()
    at.number_input(key="f_lower").set_value(0.9)   # lower threshold above upper threshold
    at.button(key="f_submit").click().run()
    assert not at.exception
    assert any("nothing was run" in e.value for e in at.error)
    assert not (tmp_path / "results" / "dashboard").exists()


def test_run_executes_engine_and_opens_results(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    at = make_app(tmp_path, monkeypatch).run()
    at.button(key="f_submit").click().run()
    assert not at.exception, at.exception
    batches = list((tmp_path / "results" / "dashboard").iterdir())
    assert len(batches) == 1
    assert at.session_state["selected_experiment"] == batches[0].name
    assert at.session_state["nav"] == "Results"


def test_precision_choice_sets_number_of_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    at = make_app(tmp_path, monkeypatch).run()
    at.segmented_control(key="f_precision").set_value("Balanced").run()
    at.button(key="f_submit").click().run()
    [batch] = (tmp_path / "results" / "dashboard").iterdir()
    assert yaml.safe_load((batch / "batch.yaml").read_text())["params"]["repetitions"] == 3


def test_failed_run_is_shown_as_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "fail")
    at = make_app(tmp_path, monkeypatch).run()
    at.button(key="f_submit").click().run()
    assert not at.exception
    assert at.session_state["nav"] == "Start"
    assert "FAILED" in texts(at)


def test_missing_engine_disables_running(tmp_path, monkeypatch):
    at = make_app(tmp_path, monkeypatch, ["definitely-not-a-java-binary"]).run()
    assert any("was not found" in e.value for e in at.error)
    assert at.button(key="f_submit").disabled
