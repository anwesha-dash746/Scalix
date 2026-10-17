"""Integration with the REAL Java engine (Part 1). Skipped, with the reason shown, if Java or the jar is missing."""

import dataclasses

import pytest

from scalix_dash import POLICIES, WORKLOADS, catalog
from scalix_dash.analysis import aggregate, best_policies, config_consistency
from scalix_dash.params import ExperimentParams
from scalix_dash.runner import FAILED, SUCCESS, SimulationRunner
from scalix_dash.settings import load_settings

pytestmark = pytest.mark.engine


@pytest.fixture
def engine_settings(tmp_path):
    s = dataclasses.replace(load_settings(), results_dir=tmp_path / "results")
    problems = SimulationRunner(s).preflight()
    if problems:
        pytest.skip("Java engine unavailable: " + " ".join(problems))
    return s


def test_all_workloads_and_policies_with_repetitions(engine_settings):
    params = ExperimentParams(workloads=list(WORKLOADS), policies=list(POLICIES), duration_s=120, repetitions=2,
                              base_seed=7)
    res = SimulationRunner(engine_settings).run_batch(params)
    assert res.status == SUCCESS, [r.message for r in res.reps]
    exp = catalog.load(next(e for e in catalog.discover(engine_settings.results_dir) if e.id == res.batch_id))
    assert exp.n_reps == 2 and len(exp.summary) == 2 * 7 * 3 and not exp.skipped
    assert set(exp.summary["seed"]) == {7, 8}
    # Same seed => identical request counts for every policy (fair comparison).
    per_seed = exp.summary.groupby(["seed", "workload_type"])["requests_total"].nunique()
    assert (per_seed == 1).all()
    assert config_consistency(exp.configs).empty
    # Dashboard parameters reached the engine.
    assert (exp.summary["workload_duration_s"] == 120).all()
    static = exp.summary[exp.summary["policy"] == "STATIC"]
    assert (static["total_scaling_actions"] == 0).all() and (static["avg_billable_vms"] == 8).all()
    agg = aggregate(exp.summary)
    assert not agg.empty and (agg["n"] == 2).all()
    assert not best_policies(agg).empty


def test_parameters_change_engine_behaviour(engine_settings):
    params = ExperimentParams(workloads=["HIGH"], policies=["THRESHOLD"], duration_s=120, initial_vms=2, min_vms=2,
                              max_vms=5, repetitions=1)
    res = SimulationRunner(engine_settings).run_batch(params)
    assert res.status == SUCCESS
    exp = catalog.load(next(e for e in catalog.discover(engine_settings.results_dir) if e.id == res.batch_id))
    row = exp.summary.iloc[0]
    assert row["peak_billable_vms"] <= 5 and row["min_provisioned_vms"] >= 2
    assert "maxVms=5" in row["policy_params"] and "initialVms=2" in row["policy_params"]


def test_engine_rejection_is_reported(engine_settings, monkeypatch):
    # Bypass Python validation to prove the engine's own validation error is captured, not hidden.
    params = ExperimentParams(workloads=["LOW"], policies=["SCALIX"], duration_s=120)
    monkeypatch.setattr(ExperimentParams, "validate", lambda self: [])
    monkeypatch.setattr(ExperimentParams, "to_engine_properties",
                        lambda self, seed: {"simulation.seed": str(seed), "policy.scalix.lower": "0.9",
                                            "experiment.workloads": "LOW", "experiment.policies": "SCALIX"})
    res = SimulationRunner(engine_settings).run_batch(params)
    assert res.status == FAILED
    assert res.reps[0].returncode == 2 and "rejected the configuration" in res.reps[0].message
