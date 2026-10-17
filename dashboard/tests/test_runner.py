import dataclasses
import sys

import pytest
import yaml

from scalix_dash import catalog
from scalix_dash.params import ExperimentParams
from scalix_dash.runner import ERROR, FAILED, SUCCESS, TIMEOUT, SimulationRunner, new_batch_id

PARAMS = ExperimentParams(workloads=["BURST", "LOW"], duration_s=120, repetitions=2)


def run(settings, monkeypatch, mode, params=PARAMS):
    monkeypatch.setenv("FAKE_ENGINE_MODE", mode)
    return SimulationRunner(settings).run_batch(params)


def test_success_writes_manifest_configs_and_logs(fake_settings, monkeypatch):
    progress = []
    monkeypatch.setenv("FAKE_ENGINE_MODE", "ok")
    res = SimulationRunner(fake_settings).run_batch(PARAMS, progress=lambda d, t, m: progress.append((d, t)))
    assert res.status == SUCCESS
    assert [r.status for r in res.reps] == [SUCCESS, SUCCESS]
    assert [r.seed for r in res.reps] == [42, 43]
    assert progress[-1] == (2, 2)
    manifest = yaml.safe_load((res.batch_dir / "batch.yaml").read_text())
    assert manifest["status"] == SUCCESS and manifest["params"]["repetitions"] == 2
    cfg = (res.batch_dir / "rep-02.properties").read_text()
    assert "simulation.seed=43" in cfg and "experiment.workloads=BURST,LOW" in cfg
    assert "exit code: 0" in (res.batch_dir / "rep-01.log").read_text()

    entry = next(e for e in catalog.discover(fake_settings.results_dir) if e.id == res.batch_id)
    exp = catalog.load(entry)
    assert exp.n_reps == 2 and len(exp.summary) == 12 and not exp.skipped


@pytest.mark.parametrize("mode, status, fragment", [
    ("fail", FAILED, "exit code 1"),
    ("config", FAILED, "rejected the configuration"),
    ("malformed", FAILED, "output is invalid"),
    ("incomplete", FAILED, "incomplete"),
    ("nooutput", FAILED, "output is invalid"),
])
def test_process_failures_are_reported_not_hidden(fake_settings, monkeypatch, mode, status, fragment):
    res = run(fake_settings, monkeypatch, mode)
    assert res.status == FAILED
    assert all(r.status == status for r in res.reps)
    assert fragment in res.reps[0].message
    manifest = yaml.safe_load((res.batch_dir / "batch.yaml").read_text())
    assert manifest["status"] == FAILED
    # A failed batch contributes no data to the dashboard.
    entry = next(e for e in catalog.discover(fake_settings.results_dir) if e.id == res.batch_id)
    assert entry.rep_dirs == []


def test_timeout(fake_settings, monkeypatch):
    settings = dataclasses.replace(fake_settings, timeout_seconds=1)
    res = run(settings, monkeypatch, "sleep", ExperimentParams(workloads=["LOW"], duration_s=120, repetitions=1))
    assert res.status == FAILED and res.reps[0].status == TIMEOUT


def test_partial_success_keeps_only_good_repetitions(fake_settings, monkeypatch, tmp_path):
    # Wrap the fake engine so the second repetition fails.
    wrapper = tmp_path / "flaky.py"
    wrapper.write_text(
        "import os, subprocess, sys\n"
        "mode = 'fail' if 'rep-02' in sys.argv else 'ok'\n"
        f"sys.exit(subprocess.call([sys.executable, r'{fake_settings.engine_command[1]}'] + sys.argv[1:],"
        " env=dict(os.environ, FAKE_ENGINE_MODE=mode)))\n", encoding="utf-8")
    settings = dataclasses.replace(fake_settings, engine_command=[sys.executable, str(wrapper)])
    res = SimulationRunner(settings).run_batch(PARAMS)
    assert res.status == "PARTIAL"
    entry = next(e for e in catalog.discover(settings.results_dir) if e.id == res.batch_id)
    exp = catalog.load(entry)
    assert exp.n_reps == 1 and any("rep-02" in s for s in exp.skipped)


def test_invalid_params_never_reach_the_engine(fake_settings, monkeypatch):
    res = run(fake_settings, monkeypatch, "ok", ExperimentParams(lower_threshold=0.9))
    assert res.status == FAILED and res.problems and not res.reps
    assert not res.batch_dir.exists()


def test_missing_executable_and_jar(fake_settings, tmp_path):
    settings = dataclasses.replace(fake_settings, engine_command=["definitely-not-a-java-binary"],
                                   engine_jar=tmp_path / "missing.jar")
    runner = SimulationRunner(settings)
    problems = runner.preflight()
    assert any("not found" in p for p in problems) and any("jar" in p for p in problems)
    res = runner.run_batch(PARAMS)
    assert res.status == FAILED and res.problems and not res.batch_dir.exists()


def test_start_failure_is_an_error_status(fake_settings, tmp_path, monkeypatch):
    # Executable passes preflight (it exists) but cannot be started.
    not_exe = tmp_path / "not-executable.txt"
    not_exe.write_text("hello")
    settings = dataclasses.replace(fake_settings, engine_command=[str(not_exe)])
    res = SimulationRunner(settings).run_batch(ExperimentParams(workloads=["LOW"], repetitions=1))
    assert res.reps[0].status == ERROR and "could not start engine" in res.reps[0].message


def test_cloud_credentials_are_not_passed_to_engine(fake_settings, monkeypatch, tmp_path):
    monkeypatch.setenv("CLOUDSTACK_SECRET_KEY", "super-secret")
    dump = tmp_path / "env.txt"
    script = tmp_path / "envdump.py"
    script.write_text(f"import os; open(r'{dump}', 'w').write('\\n'.join(os.environ))\n", encoding="utf-8")
    settings = dataclasses.replace(fake_settings, engine_command=[sys.executable, str(script)])
    SimulationRunner(settings).run_batch(ExperimentParams(workloads=["LOW"], repetitions=1))
    assert "CLOUDSTACK_SECRET_KEY" not in dump.read_text()


def test_batch_ids_are_unique(tmp_path):
    a = new_batch_id(tmp_path)
    (tmp_path / a).mkdir()
    assert new_batch_id(tmp_path) != a
