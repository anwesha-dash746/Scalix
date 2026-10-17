"""Plain-language names and verdicts for the dashboard.

Everything here is derived from aggregated measurements (``analysis.aggregate``); the wording
never claims more than the numbers support (e.g. one run is called "one run", overlapping
confidence intervals are called "too close to call").
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import pandas as pd

from .analysis import METRICS, best_policies

POLICY_NAMES = {"STATIC": "Fixed fleet", "THRESHOLD": "Rule-based", "SCALIX": "SCALIX"}

POLICY_BLURBS = {
    "STATIC": "Rents the same number of servers all the time, like a shop with a fixed rota. "
              "Predictable, but it can't react to a rush or save money when things are quiet.",
    "THRESHOLD": "The common textbook rule: add a server when the ones you have are more than 70% busy, "
                 "remove one when they drop under 30%. Simple, but it only reacts after the fact.",
    "SCALIX": "Watches how busy the servers are and which way demand is heading. It adds capacity early "
              "when traffic is climbing, and waits a little before scaling down so it doesn't flip-flop.",
}

# (friendly name, one-line description)
SCENARIOS = {
    "LOW": ("Quiet day", "Light, steady traffic (about 2 requests a second)."),
    "MEDIUM": ("Normal day", "Steady, moderate traffic (about 5 requests a second)."),
    "HIGH": ("Busy day", "Heavy, steady traffic (about 10 requests a second)."),
    "BURST": ("Sudden rush", "Calm traffic, then a sudden 4x spike for a couple of minutes."),
    "INCREASING": ("Growing demand", "Traffic climbs steadily from 1 to 12 requests a second."),
    "DECREASING": ("Winding down", "Traffic falls steadily from 12 to 1 request a second."),
    "FLUCTUATING": ("Up and down", "Traffic rises and falls in waves."),
}

# Plain names for the metrics shown on the results page.
PLAIN_METRICS = {
    "avg_response_time_s": "Average response time",
    "p95_response_time_s": "Slowest 5% of requests",
    "total_cost_usd": "Cost of the run",
    "cpu_utilization": "Servers kept busy",
    "avg_billable_vms": "Servers on average",
    "total_scaling_actions": "Times it resized",
}

PRECISION = {"Quick look": 1, "Balanced": 3, "Thorough": 5}


def policy_name(code: str) -> str:
    return POLICY_NAMES.get(code, code.title())


def scenario_name(code: str) -> str:
    return SCENARIOS.get(code, (code.title(), ""))[0]


def scenario_description(code: str) -> str:
    return SCENARIOS.get(code, ("", ""))[1]


def fmt(metric: str, value: float) -> str:
    """Human formatting of one metric value."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "n/a"
    if metric.endswith("_time_s"):
        return f"{value:.1f} s" if value < 100 else f"{value:.0f} s"
    if metric == "total_cost_usd":
        return f"${value:.2f}" if value >= 0.1 else f"${value:.3f}"
    if metric in ("cpu_utilization", "mean_active_vm_utilization"):
        return f"{value * 100:.0f}%"
    if metric == "avg_billable_vms":
        return f"{value:.1f}"
    if metric in ("total_scaling_actions", "scale_up_actions", "scale_down_actions"):
        return f"{value:.0f}" if float(value).is_integer() else f"{value:.1f}"
    return f"{value:.3g}"


def confidence_phrase(evidence: str, n: int) -> str:
    if evidence == "tie":
        return "A tie."
    if evidence.startswith("clear"):
        return f"A clear lead across {n} runs."
    if evidence.startswith("not clear"):
        return f"Too close to call across {n} runs; the results overlap."
    if evidence.startswith("single run"):
        return "Based on a single run. Run more to be sure."
    return ""


@dataclass(frozen=True)
class Verdict:
    title: str
    metric: str
    policy: str          # friendly policy name, or "A = B" for ties
    value: str
    note: str


VERDICT_SPECS = [
    ("Fastest for users", "avg_response_time_s"),
    ("Cheapest to run", "total_cost_usd"),
    ("Least idle capacity", "cpu_utilization"),
]


def verdicts(agg: pd.DataFrame, workload: str) -> list[Verdict]:
    sub = agg[agg["workload_type"] == workload]
    if sub.empty or sub["policy"].nunique() < 2:
        return []
    best = best_policies(sub, [m for _, m in VERDICT_SPECS]).set_index("metric")
    n = int(sub["n"].min())
    out = []
    for title, metric in VERDICT_SPECS:
        if metric not in best.index:
            continue
        row = best.loc[metric]
        winners = " = ".join(policy_name(p) for p in str(row["best_policy"]).split(" = "))
        out.append(Verdict(title, metric, winners, fmt(metric, row["best_mean"]),
                           confidence_phrase(str(row["evidence"]), n)))
    return out


def tradeoff_sentences(agg: pd.DataFrame, workload: str) -> list[str]:
    """One or two plain sentences on the speed/cost trade-off, computed from the means."""
    sub = agg[agg["workload_type"] == workload]
    if sub["policy"].nunique() < 2:
        return []
    mean = sub.pivot(index="policy", columns="metric", values="mean")
    out = []
    if {"avg_response_time_s", "total_cost_usd"} <= set(mean.columns):
        fastest = mean["avg_response_time_s"].idxmin()
        cheapest = mean["total_cost_usd"].idxmin()
        if fastest == cheapest:
            out.append(f"{policy_name(fastest)} was both the fastest and the cheapest here. No trade-off this time.")
        else:
            f, c = mean.loc[fastest], mean.loc[cheapest]
            faster = (1 - f["avg_response_time_s"] / c["avg_response_time_s"]) * 100
            pricier = (f["total_cost_usd"] / c["total_cost_usd"] - 1) * 100 if c["total_cost_usd"] > 0 else float("nan")
            out.append(f"{policy_name(fastest)} answered requests {faster:.0f}% faster on average than "
                       f"{policy_name(cheapest)}, but cost {pricier:.0f}% more. Which one is better depends "
                       f"on whether speed or budget matters more to you.")
    if "p95_response_time_s" in mean.columns and len(mean) > 1:
        p95 = mean["p95_response_time_s"]
        worst, best = p95.idxmax(), p95.idxmin()
        if p95[best] > 0 and p95[worst] >= 5 * p95[best]:
            out.append(f"{policy_name(worst)} fell behind: its slowest 5% of requests waited "
                       f"{fmt('p95_response_time_s', p95[worst])}, versus {fmt('p95_response_time_s', p95[best])} "
                       f"with {policy_name(best)}.")
    return out


def metric_definition(metric: str) -> str:
    return METRICS[metric].description if metric in METRICS else ""
