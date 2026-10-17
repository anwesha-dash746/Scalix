"""Streamlit app tests with streamlit.testing.v1.AppTest (headless; no browser needed)."""

import sys
from pathlib import Path

import pytest
import yaml
from streamlit.testing.v1 import AppTest

from conftest import FAKE_ENGINE
from fixtures_util import write_experiment

APP = str(Path(__file__).resolve().parents[1] / "app.py")


def settings_file(tmp_path, command):
    cfg = tmp_path / "config"
    cfg.mkdir()
    f = cfg / "dashboard.yaml"
    f.write_text(yaml.safe_dump({
        "engine": {"command": command, "timeout_seconds": 30},
        "results_dir": str(tmp_path / "results"),
        "defaults": {"workloads": ["LOW"], "duration_s": 120, "repetitions": 2},
        "cloud": {"provider": "mock", "cloudstack": {"enabled": False}},
    }))
    return f


def make_app(tmp_path, monkeypatch, command=None):
    monkeypatch.setenv("SCALIX_DASHBOARD_CONFIG", str(settings_file(tmp_path, command or [sys.executable, str(FAKE_ENGINE)])))
    at = AppTest.from_file(APP, default_timeout=60)
    return at


def test_app_starts_without_experiments(tmp_path, monkeypatch):
    at = make_app(tmp_path, monkeypatch).run()
    assert not at.exception
    assert any("No experiments found" in i.value for i in at.sidebar.info)


def test_app_renders_all_tabs_for_an_experiment(tmp_path, monkeypatch):
    write_experiment(tmp_path / "results" / "exp-a", exp_id="exp-a", workloads=("LOW", "BURST"))
    at = make_app(tmp_path, monkeypatch).run()
    assert not at.exception, at.exception
    assert len(at.tabs) == 7
    labels = [m.label for m in at.metric]
    assert "Avg response time" in labels and "Simulated cost (calc.)" in labels
    assert at.get("download_button")             # Export tab rendered download buttons


def test_run_form_rejects_invalid_parameters(tmp_path, monkeypatch):
    at = make_app(tmp_path, monkeypatch).run()
    at.number_input(key="f_lower").set_value(0.9)   # lower threshold above upper threshold
    at.button(key="f_submit").click().run()
    assert not at.exception
    assert any("nothing was run" in e.value for e in at.error)
    assert not (tmp_path / "results" / "dashboard").exists()


def test_run_form_executes_engine_and_selects_result(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    at = make_app(tmp_path, monkeypatch).run()
    at.button(key="f_submit").click().run()
    assert not at.exception, at.exception
    batches = list((tmp_path / "results" / "dashboard").iterdir())
    assert len(batches) == 1
    assert at.session_state["selected_experiment"] == batches[0].name


def test_failed_run_is_shown_as_failure(tmp_path, monkeypatch):
    monkeypatch.setenv("FAKE_ENGINE_MODE", "fail")
    at = make_app(tmp_path, monkeypatch).run()
    at.button(key="f_submit").click().run()
    assert not at.exception
    assert any("FAILED" in m.value for m in at.markdown)
    assert not any("completed" in s.value for s in at.success)


@pytest.mark.parametrize("command", [["definitely-not-a-java-binary"]])
def test_missing_engine_disables_running(tmp_path, monkeypatch, command):
    at = make_app(tmp_path, monkeypatch, command).run()
    assert any("was not found" in e.value for e in at.error)
