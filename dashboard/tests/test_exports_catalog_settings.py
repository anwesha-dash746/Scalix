import io
import zipfile

import pandas as pd
import pytest
import yaml

from fixtures_util import write_experiment
from scalix_dash import catalog
from scalix_dash.exports import aggregated_summary, csv_bytes, export_files, zip_bundle
from scalix_dash.settings import SettingsError, load_settings


@pytest.fixture
def cli_experiment(tmp_path):
    results = tmp_path / "results"
    write_experiment(results / "exp-a", exp_id="exp-a", workloads=("LOW", "BURST"))
    entry = next(e for e in catalog.discover(results) if e.id == "exp-a")
    return catalog.load(entry)


def test_catalog_lists_cli_and_dashboard_experiments(tmp_path):
    results = tmp_path / "results"
    write_experiment(results / "exp-a", exp_id="exp-a")
    (results / "not-an-experiment").mkdir(parents=True)
    batch = results / "dashboard" / "dash-1"
    write_experiment(batch / "rep-01", exp_id="rep-01", seed=1)
    write_experiment(batch / "rep-02", exp_id="rep-02", seed=2)
    (batch / "batch.yaml").write_text(yaml.safe_dump({
        "status": "PARTIAL", "started_at": "2030-01-01T00:00:00",
        "repetitions": [{"rep": "rep-01", "seed": 1, "status": "SUCCESS"},
                        {"rep": "rep-02", "seed": 2, "status": "FAILED"}]}))
    entries = {e.id: e for e in catalog.discover(results)}
    assert set(entries) == {"exp-a", "dash-1"}
    assert entries["exp-a"].kind == "cli"
    dash = entries["dash-1"]
    assert dash.kind == "dashboard" and [d.name for d in dash.rep_dirs] == ["rep-01"]
    exp = catalog.load(dash)
    assert exp.n_reps == 1 and exp.skipped == ["rep-02: status FAILED"]
    assert set(exp.summary["seed"]) == {1}


def test_catalog_skips_invalid_repetition_output(tmp_path):
    results = tmp_path / "results"
    batch = results / "dashboard" / "dash-2"
    write_experiment(batch / "rep-01", exp_id="rep-01")
    (batch / "rep-01" / "timeseries.csv").unlink()      # corrupted after the run
    (batch / "batch.yaml").write_text(yaml.safe_dump({"status": "SUCCESS", "repetitions": [
        {"rep": "rep-01", "seed": 42, "status": "SUCCESS"}]}))
    exp = catalog.load(catalog.discover(results)[0])
    assert exp.summary.empty and "invalid output" in exp.skipped[0]


def test_catalog_handles_missing_dir_and_bad_manifest(tmp_path):
    assert catalog.discover(tmp_path / "none") == []
    batch = tmp_path / "results" / "dashboard" / "broken"
    batch.mkdir(parents=True)
    (batch / "batch.yaml").write_text("{ not: valid: yaml")
    [entry] = catalog.discover(tmp_path / "results")
    assert entry.status == "UNREADABLE MANIFEST" and entry.rep_dirs == []


def test_csv_bytes_roundtrip():
    df = pd.DataFrame({"a": [1.5, float("nan")], "b": ["x,y", "z"]})
    back = pd.read_csv(io.BytesIO(csv_bytes(df)))
    assert back["a"].iloc[0] == 1.5 and pd.isna(back["a"].iloc[1]) and back["b"].iloc[0] == "x,y"


def test_export_files_contain_real_loaded_rows(cli_experiment):
    files = export_files(cli_experiment)
    assert set(files) == {"runs_summary.csv", "aggregated_summary.csv", "timeseries.csv",
                          "scaling_events.csv", "workloads.csv"}
    runs = pd.read_csv(io.BytesIO(files["runs_summary.csv"]))
    assert len(runs) == 6 and {"rep", "seed", "run_id", "total_cost_usd"} <= set(runs.columns)
    ts = pd.read_csv(io.BytesIO(files["timeseries.csv"]))
    assert len(ts) == len(cli_experiment.timeseries)
    agg = pd.read_csv(io.BytesIO(files["aggregated_summary.csv"]))
    assert {"experiment", "metric", "mean", "std", "ci95", "kind", "better"} <= set(agg.columns)
    assert set(agg.loc[agg["metric"] == "total_cost_usd", "kind"]) == {"CALCULATED"}


def test_aggregated_summary_empty_when_no_valid_runs(cli_experiment):
    cli_experiment.summary["drain_timed_out"] = True
    assert aggregated_summary(cli_experiment).empty


def test_zip_bundle_contains_derived_and_raw_files(cli_experiment):
    data = zip_bundle(cli_experiment)
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert "exp-a/runs_summary.csv" in names
    assert "exp-a/raw/exp-a/summary.csv" in names and "exp-a/raw/exp-a/config_used.properties" in names


def test_settings_load_and_resolve(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    f = cfg_dir / "dashboard.yaml"
    f.write_text("engine:\n  command: [java, -jar, target/x.jar]\n  jar: target/x.jar\nresults_dir: out\n")
    s = load_settings(f)
    assert s.project_root == tmp_path
    assert s.engine_command[0] == "java" and s.engine_command[2] == str(tmp_path / "target" / "x.jar")
    assert s.results_dir == tmp_path / "out" and s.dashboard_runs_dir == tmp_path / "out" / "dashboard"


@pytest.mark.parametrize("text", ["engine: {command: []}\n", "engine: {command: [java], timeout_seconds: -1}\n",
                                  "- just\n- a list\n", "engine: [unclosed\n"])
def test_invalid_settings_rejected(tmp_path, text):
    f = tmp_path / "s.yaml"
    f.write_text(text)
    with pytest.raises(SettingsError):
        load_settings(f)


def test_missing_settings_file(tmp_path):
    with pytest.raises(SettingsError):
        load_settings(tmp_path / "nope.yaml")


def test_project_settings_file_is_valid():
    s = load_settings()
    assert s.cloud["cloudstack"]["enabled"] is False          # safe by default
    assert s.cloud["provider"] == "mock"
