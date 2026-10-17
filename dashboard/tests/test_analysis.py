import math

import numpy as np
import pandas as pd
import pytest

from scalix_dash.analysis import (aggregate, best_policies, comparison_table, config_consistency, format_mean_std,
                                  relative_to_baseline, resample_timeseries, scaling_actions, t_critical_95,
                                  valid_runs, win_counts)


def summary_rows(values: dict, workload="BURST", drain=None):
    """values: {policy: [metric value per rep]} for avg_response_time_s; other metrics derived."""
    rows = []
    for policy, vals in values.items():
        for i, v in enumerate(vals):
            rows.append({"workload_type": workload, "policy": policy, "rep": f"rep-{i + 1:02d}",
                         "avg_response_time_s": v, "p95_response_time_s": v * 2, "cpu_utilization": 0.5,
                         "avg_billable_vms": 8.0, "total_cost_usd": {"STATIC": 0.1, "THRESHOLD": 0.2, "SCALIX": 0.3}[policy],
                         "total_scaling_actions": 0, "requests_completed": 100,
                         "drain_timed_out": bool(drain and drain == (policy, i))})
    return pd.DataFrame(rows)


def test_aggregate_mean_std_ci():
    df = summary_rows({"SCALIX": [1.0, 2.0, 3.0]})
    agg = aggregate(df, ["avg_response_time_s"])
    row = agg.iloc[0]
    assert row["n"] == 3 and row["mean"] == pytest.approx(2.0)
    assert row["std"] == pytest.approx(1.0)                       # sample std (ddof=1)
    assert row["ci95"] == pytest.approx(4.303 * 1.0 / math.sqrt(3))
    assert (row["min"], row["max"]) == (1.0, 3.0)


def test_single_repetition_has_no_spread():
    agg = aggregate(summary_rows({"STATIC": [2.0]}), ["avg_response_time_s"])
    assert math.isnan(agg.iloc[0]["std"]) and math.isnan(agg.iloc[0]["ci95"])


def test_drain_timeouts_are_excluded():
    df = summary_rows({"STATIC": [1.0, 100.0]}, drain=("STATIC", 1))
    valid, excluded = valid_runs(df)
    assert len(valid) == 1 and len(excluded) == 1
    assert aggregate(df, ["avg_response_time_s"]).iloc[0]["mean"] == 1.0


def test_runs_without_completed_requests_are_excluded():
    df = summary_rows({"STATIC": [1.0, 2.0]})
    df.loc[1, "requests_completed"] = 0
    assert len(valid_runs(df)[0]) == 1


def test_t_critical_values():
    assert t_critical_95(1) == 12.706 and t_critical_95(30) == 2.042 and t_critical_95(100) == 1.96
    assert math.isnan(t_critical_95(0))


def test_best_policy_can_be_a_baseline_and_evidence_is_honest():
    # STATIC is cheapest; THRESHOLD has the best response time with well-separated repetitions.
    df = summary_rows({"STATIC": [5.0, 5.1, 4.9], "THRESHOLD": [1.0, 1.1, 0.9], "SCALIX": [3.0, 3.1, 2.9]})
    best = best_policies(aggregate(df)).set_index("metric")
    assert best.loc["total_cost_usd", "best_policy"] == "STATIC"
    assert best.loc["avg_response_time_s", "best_policy"] == "THRESHOLD"
    assert best.loc["avg_response_time_s", "evidence"].startswith("clear")
    assert best.loc["cpu_utilization", "evidence"] == "tie"
    wins = win_counts(best_policies(aggregate(df)))
    assert wins.loc["Simulated cost", "STATIC"] == 1


def test_overlapping_intervals_are_not_called_clear():
    df = summary_rows({"STATIC": [1.0, 3.0], "SCALIX": [1.5, 3.5]})
    best = best_policies(aggregate(df, ["avg_response_time_s"])).iloc[0]
    assert best["best_policy"] == "STATIC" and best["evidence"].startswith("not clear")


def test_single_run_evidence_label():
    best = best_policies(aggregate(summary_rows({"STATIC": [1.0], "SCALIX": [2.0]}), ["avg_response_time_s"]))
    assert best.iloc[0]["evidence"].startswith("single run")


def test_higher_is_better_metric():
    df = summary_rows({"STATIC": [1.0], "SCALIX": [1.0]})
    df.loc[df["policy"] == "SCALIX", "cpu_utilization"] = 0.9
    best = best_policies(aggregate(df, ["cpu_utilization"])).iloc[0]
    assert best["best_policy"] == "SCALIX"


def test_relative_to_baseline():
    df = summary_rows({"STATIC": [2.0], "SCALIX": [1.0]})
    rel = relative_to_baseline(aggregate(df, ["avg_response_time_s", "total_cost_usd"]))
    r = rel.set_index("metric")
    assert r.loc["avg_response_time_s", "pct_change"] == pytest.approx(-50.0)
    assert r.loc["total_cost_usd", "pct_change"] == pytest.approx(200.0)


def test_comparison_table_and_formatting():
    df = summary_rows({"STATIC": [2.0, 4.0], "SCALIX": [1.0, 1.0]})
    table = comparison_table(aggregate(df), "avg_response_time_s")
    assert list(table.columns) == ["STATIC", "SCALIX"]
    assert table.loc["BURST", "STATIC"] == "3.000 ± 1.414"
    assert format_mean_std(float("nan"), float("nan"), 0) == "n/a"
    assert format_mean_std(2.0, float("nan"), 1) == "2.000"


def test_empty_inputs():
    empty = summary_rows({"STATIC": [1.0]}).iloc[0:0]
    assert aggregate(empty).empty
    assert best_policies(aggregate(empty)).empty


def test_config_consistency():
    same = {"rep-01": {"a": "1", "simulation.seed": "1"}, "rep-02": {"a": "1", "simulation.seed": "2"}}
    assert config_consistency(same).empty
    diff = {"rep-01": {"a": "1"}, "rep-02": {"a": "2"}}
    out = config_consistency(diff)
    assert list(out["key"]) == ["a"] and "rep-02=2" in out["values"].iloc[0]


def test_resample_timeseries_rates_and_means():
    ts = pd.DataFrame({"workload_type": "LOW", "policy": "STATIC", "rep": "rep-01",
                       "time_s": np.arange(30.0), "arrivals_in_tick": [2] * 15 + [4] * 15,
                       "billable_vms": [8] * 30, "provisioned_vms": [8] * 30,
                       "active_vm_utilization": [np.nan] + [0.5] * 29, "requests_in_system": [1] * 30})
    rs = resample_timeseries(ts, 15)
    assert list(rs["window_start_s"]) == [0.0, 15.0]
    assert list(rs["arrival_rate"]) == [2.0, 4.0]
    assert rs["active_vm_utilization"].iloc[0] == pytest.approx(0.5)     # NaN ignored
    with pytest.raises(ValueError):
        resample_timeseries(ts, 0)


def test_resample_averages_repetitions():
    base = {"workload_type": "LOW", "policy": "STATIC", "time_s": [0.0, 1.0], "billable_vms": [8, 8],
            "provisioned_vms": [8, 8], "active_vm_utilization": [0.5, 0.5], "requests_in_system": [1, 1]}
    ts = pd.concat([pd.DataFrame({**base, "rep": "rep-01", "arrivals_in_tick": [1, 1]}),
                    pd.DataFrame({**base, "rep": "rep-02", "arrivals_in_tick": [3, 3]})])
    assert resample_timeseries(ts, 2)["arrival_rate"].iloc[0] == pytest.approx(2.0)


def test_scaling_actions_direction():
    ev = pd.DataFrame({"applied_delta": [2, 0, -1, 0], "cooldown_blocked": [False, True, False, False],
                       "time_s": [1, 2, 3, 4]})
    assert list(scaling_actions(ev)["direction"]) == ["UP", "DOWN"]
    assert list(scaling_actions(ev, include_non_actions=True)["direction"]) == ["UP", "BLOCKED (cooldown)", "DOWN", "NONE"]
