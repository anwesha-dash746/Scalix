"""Experiment runner, saved outputs and the command line."""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scalix.algorithms import CapacityLimits, ScalixPolicy, StaticPolicy, ThresholdPolicy
from scalix.simulation import ExperimentSpec, Scenario, WorkloadSpec, run_experiment, save_experiment
from scalix.simulation.workloads import SYNTHETIC_SCENARIOS
from scalix.simulation.__main__ import main as cli_main

SHORT = WorkloadSpec(duration_seconds=600)


@pytest.fixture(scope="module")
def result():
    return run_experiment(ExperimentSpec(repetitions=3, base_seed=10, workload=SHORT))


def test_every_combination_runs(result):
    s = result.summary
    assert len(s) == 4 * 4 * 3
    assert set(s["scenario"]) == {sc.value for sc in SYNTHETIC_SCENARIOS}
    assert set(s["policy"]) == {"STATIC", "THRESHOLD", "SCALIX", "TARGET_TRACKING"}
    assert sorted(s["seed"].unique()) == [10, 11, 12]


def test_all_policies_get_the_identical_workload(result):
    s = result.summary
    per_trace = s.groupby(["scenario", "seed"])["total_arrivals"].nunique()
    assert (per_trace == 1).all()
    assert len(result.workloads) == 4 * 3 * 120  # 600 s / 5 s per scenario x repetition


def test_experiment_is_reproducible(result):
    again = run_experiment(ExperimentSpec(repetitions=3, base_seed=10, workload=SHORT))
    for name in ("summary", "aggregate", "timeseries", "events", "workloads"):
        pd.testing.assert_frame_equal(getattr(result, name), getattr(again, name))


def test_aggregate_has_n_and_intervals(result):
    agg = result.aggregate
    assert (agg["n"] == 3).all()
    row = agg[(agg.scenario == "SPIKE") & (agg.policy == "STATIC") & (agg.metric == "estimated_cost")]
    assert len(row) == 1


def test_saved_files_and_labels(result, tmp_path):
    out = save_experiment(result, tmp_path / "exp")
    for name in ("summary.csv", "aggregate.csv", "timeseries.csv", "events.csv", "workloads.csv", "experiment.json"):
        assert (out / name).is_file()
    manifest = json.loads((out / "experiment.json").read_text())
    assert manifest["data_label"].startswith("SIMULATED")
    assert "not AWS" in manifest["data_label"] or "None of these are AWS" in manifest["data_label"]
    assert manifest["spec"]["seeds"] == [10, 11, 12]
    summary = pd.read_csv(out / "summary.csv")
    assert {"estimated_cost", "cost_cpu_credits", "response_time_model_avg_s", "response_time_model_p95_s",
            "response_time_proxy_avg_s"} <= set(summary.columns)
    assert not any("aws" in c.lower() or "actual" in c.lower() for c in summary.columns)
    events = pd.read_csv(out / "events.csv")
    assert events["reason"].notna().all()


def test_spec_validation():
    with pytest.raises(ValueError, match="same number of workers"):
        ExperimentSpec(policies=(StaticPolicy(worker_count=4), ThresholdPolicy()))
    with pytest.raises(ValueError, match="only once"):
        ExperimentSpec(policies=(ThresholdPolicy(), ThresholdPolicy()))
    with pytest.raises(ValueError):
        ExperimentSpec(repetitions=0)
    with pytest.raises(ValueError):
        ExperimentSpec(scenarios=())
    with pytest.raises(ValueError):
        ExperimentSpec(scenarios=("SPIKE", "spike"))
    with pytest.raises(ValueError):
        ExperimentSpec(scenarios=("HOLIDAY",))


def test_custom_policies_and_subset(tmp_path):
    cap = CapacityLimits(1, 3, 6)
    spec = ExperimentSpec(scenarios=("SPIKE",), repetitions=1, workload=SHORT,
                          policies=(ThresholdPolicy(capacity=cap, upper=0.8), ScalixPolicy(capacity=cap)))
    r = run_experiment(spec)
    assert list(r.summary["policy"]) == ["THRESHOLD", "SCALIX"]
    assert r.summary["peak_workers"].max() <= 6


def test_cli_runs_and_saves(tmp_path, capsys):
    out = tmp_path / "cli"
    code = cli_main(["--scenarios", "NORMAL,SPIKE", "--repetitions", "2", "--duration", "300", "--out", str(out)])
    assert code == 0
    printed = capsys.readouterr().out
    assert "SIMULATED" in printed and "SCALIX" in printed
    assert len(pd.read_csv(out / "summary.csv")) == 2 * 2 * 4


@pytest.mark.parametrize("args", [
    ["--scenarios", "WEEKEND"], ["--repetitions", "0"], ["--step", "4"], ["--initial-workers", "50"],
    ["--rate", "-3"],
])
def test_cli_rejects_invalid_settings(args, tmp_path, capsys):
    assert cli_main(args + ["--out", str(tmp_path / "x")]) == 2
    assert "Invalid setting" in capsys.readouterr().err
    assert not (tmp_path / "x").exists()


def test_same_start_rule_and_its_tuned_exception():
    from scalix.algorithms import CapacityLimits, ScalixPolicy, StaticPolicy, ThresholdPolicy

    auto = (ThresholdPolicy(capacity=CapacityLimits()), ScalixPolicy(capacity=CapacityLimits()))
    with pytest.raises(ValueError, match="same number of workers"):
        ExperimentSpec(policies=(StaticPolicy(worker_count=15), *auto))
    spec = ExperimentSpec(policies=(StaticPolicy(worker_count=15), *auto), same_start=False)
    assert spec.to_dict()["same_start"] is False
    with pytest.raises(ValueError, match="policies that can scale"):
        ExperimentSpec(policies=(ThresholdPolicy(capacity=CapacityLimits(1, 4, 20)), auto[1]), same_start=False)
    with pytest.raises(ValueError, match="same_start"):
        ExperimentSpec(same_start="no")
