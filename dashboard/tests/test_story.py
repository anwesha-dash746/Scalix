"""The plain-language verdicts must say exactly what the numbers say."""

import pandas as pd

from scalix_dash.analysis import aggregate
from scalix_dash.story import confidence_phrase, fmt, policy_name, scenario_name, tradeoff_sentences, verdicts


def summary(spec):
    """spec: {policy: (avg_rt list, cost, p95 list)}"""
    rows = []
    for p, (rts, cost, p95s) in spec.items():
        for i, (rt, p95) in enumerate(zip(rts, p95s)):
            rows.append({"workload_type": "BURST", "policy": p, "rep": f"rep-{i + 1:02d}", "avg_response_time_s": rt,
                         "p95_response_time_s": p95, "cpu_utilization": {"STATIC": .7, "THRESHOLD": .5, "SCALIX": .4}[p],
                         "avg_billable_vms": 8.0, "total_cost_usd": cost, "total_scaling_actions": 0,
                         "requests_completed": 10, "drain_timed_out": False})
    return pd.DataFrame(rows)


def test_verdicts_name_the_actual_winners():
    agg = aggregate(summary({"STATIC": ([40, 41, 39], 0.20, [115, 116, 114]),
                             "THRESHOLD": ([17, 18, 16], 0.29, [60, 61, 59]),
                             "SCALIX": ([9, 10, 8], 0.38, [29, 30, 28])}))
    v = {x.title: x for x in verdicts(agg, "BURST")}
    assert v["Fastest for users"].policy == "SCALIX" and v["Fastest for users"].value == "9.0 s"
    assert v["Cheapest to run"].policy == "Fixed fleet" and v["Cheapest to run"].value == "$0.20"
    assert v["Least idle capacity"].policy == "Fixed fleet" and v["Least idle capacity"].value == "70%"
    assert v["Fastest for users"].note == "A clear lead across 3 runs."


def test_tradeoff_sentence_matches_numbers():
    agg = aggregate(summary({"STATIC": ([40], 0.20, [116]), "SCALIX": ([10], 0.30, [20])}))
    s = tradeoff_sentences(agg, "BURST")
    assert s[0].startswith("SCALIX answered requests 75% faster on average than Fixed fleet, but cost 50% more.")
    assert "Fixed fleet fell behind" in s[1] and "116 s" in s[1] and "20.0 s" in s[1]


def test_no_tradeoff_when_one_policy_wins_both():
    agg = aggregate(summary({"STATIC": ([4], 0.30, [5]), "SCALIX": ([2], 0.20, [4])}))
    assert tradeoff_sentences(agg, "BURST") == ["SCALIX was both the fastest and the cheapest here. No trade-off this time."]


def test_single_policy_gives_no_verdict():
    agg = aggregate(summary({"SCALIX": ([2], 0.2, [3])}))
    assert verdicts(agg, "BURST") == [] and tradeoff_sentences(agg, "BURST") == []


def test_wording_helpers():
    assert confidence_phrase("single run (no variability estimate)", 1).startswith("Based on a single run")
    assert confidence_phrase("not clear (95% CIs overlap)", 3).startswith("Too close to call")
    assert confidence_phrase("tie", 2) == "A tie."
    assert fmt("cpu_utilization", 0.554) == "55%" and fmt("total_cost_usd", 0.04) == "$0.040"
    assert fmt("p95_response_time_s", 246.4) == "246 s" and fmt("avg_response_time_s", float("nan")) == "n/a"
    assert policy_name("THRESHOLD") == "Rule-based" and scenario_name("BURST") == "Sudden rush"
    assert policy_name("OTHER") == "Other"
