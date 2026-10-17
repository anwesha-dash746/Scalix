import pytest

from fixtures_util import write_experiment
from scalix_dash.results import (SUMMARY_SCHEMA, ResultValidationError, load_csv, load_experiment,
                                 load_properties)


def test_valid_experiment_loads_with_types(tmp_path):
    d = write_experiment(tmp_path / "e")
    data = load_experiment(d)
    assert len(data.summary) == 3
    assert str(data.summary["avg_response_time_s"].dtype) == "float64"
    assert str(data.summary["requests_total"].dtype) == "int64"
    assert data.summary["drain_timed_out"].dtype == bool
    # 64-bit seeds keep every digit
    assert data.summary["workload_seed"].iloc[0] == -4767286540954276203
    # blank float -> NaN
    assert data.timeseries["active_vm_utilization"].isna().sum() == 3
    assert data.events["reason"].iloc[0] == "fixture reason, with comma"
    assert data.config["simulation.seed"] == "42"
    assert data.vms is not None


def test_missing_directory(tmp_path):
    with pytest.raises(ResultValidationError, match="not found"):
        load_experiment(tmp_path / "nope")


@pytest.mark.parametrize("name", ["summary.csv", "timeseries.csv", "scaling_events.csv", "workloads.csv"])
def test_missing_required_file(tmp_path, name):
    d = write_experiment(tmp_path / "e")
    (d / name).unlink()
    with pytest.raises(ResultValidationError, match=name):
        load_experiment(d)


def test_vms_file_is_optional(tmp_path):
    d = write_experiment(tmp_path / "e")
    (d / "vms.csv").unlink()
    assert load_experiment(d).vms is None


def test_empty_file(tmp_path):
    f = tmp_path / "summary.csv"
    f.write_text("", encoding="utf-8")
    with pytest.raises(ResultValidationError, match="empty"):
        load_csv(f, SUMMARY_SCHEMA)


def test_missing_column(tmp_path):
    d = write_experiment(tmp_path / "e")
    text = (d / "summary.csv").read_text(encoding="utf-8").replace("p95_response_time_s", "p95_typo", 1)
    (d / "summary.csv").write_text(text, encoding="utf-8")
    with pytest.raises(ResultValidationError, match="missing column.*p95_response_time_s"):
        load_experiment(d)


@pytest.mark.parametrize("column, bad", [("avg_response_time_s", "fast"), ("requests_total", "12.5"),
                                         ("requests_total", ""), ("drain_timed_out", "maybe")])
def test_malformed_values(tmp_path, column, bad):
    d = write_experiment(tmp_path / "e", metric_values={("BURST", "SCALIX"): {column: bad}})
    with pytest.raises(ResultValidationError, match=column):
        load_experiment(d)


def test_problems_from_several_files_are_all_reported(tmp_path):
    d = write_experiment(tmp_path / "e")
    (d / "timeseries.csv").unlink()
    (d / "workloads.csv").unlink()
    with pytest.raises(ResultValidationError) as e:
        load_experiment(d)
    assert len(e.value.problems) == 2


def test_orphan_rows_are_rejected(tmp_path):
    d = write_experiment(tmp_path / "e")
    with (d / "timeseries.csv").open("a", encoding="utf-8") as f:
        f.write("ghost-run,BURST,STATIC,0,1,1,0,0,1,0.5,1,1\n")
    with pytest.raises(ResultValidationError, match="absent from summary"):
        load_experiment(d)


def test_duplicate_run_ids_rejected(tmp_path):
    d = write_experiment(tmp_path / "e", policies=("STATIC",))
    lines = (d / "summary.csv").read_text(encoding="utf-8").splitlines()
    (d / "summary.csv").write_text("\n".join(lines + [lines[1]]) + "\n", encoding="utf-8")
    with pytest.raises(ResultValidationError, match="duplicate"):
        load_experiment(d)


def test_summary_without_runs(tmp_path):
    d = write_experiment(tmp_path / "e")
    header = (d / "summary.csv").read_text(encoding="utf-8").splitlines()[0]
    (d / "summary.csv").write_text(header + "\n", encoding="utf-8")
    (d / "timeseries.csv").write_text((d / "timeseries.csv").read_text().splitlines()[0] + "\n")
    (d / "scaling_events.csv").write_text((d / "scaling_events.csv").read_text().splitlines()[0] + "\n")
    with pytest.raises(ResultValidationError, match="no runs"):
        load_experiment(d)


def test_properties_reader(tmp_path):
    f = tmp_path / "c.properties"
    f.write_text("# comment\n\na=1\nb = two=2\n!bang\n", encoding="utf-8")
    assert load_properties(f) == {"a": "1", "b": "two=2"}
    assert load_properties(tmp_path / "missing") == {}
