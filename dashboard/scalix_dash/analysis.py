"""Aggregation and comparison of measured results.

All statistics are computed from the engine's per-run summary rows. Runs that hit the drain limit
(``drain_timed_out``) are excluded from aggregates because their response-time statistics cover
only the requests that finished; they are counted and reported instead of hidden.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd

from . import POLICIES, WORKLOADS


@dataclass(frozen=True)
class Metric:
    column: str
    label: str
    unit: str
    better: str          # "lower" or "higher"
    kind: str            # "MEASURED" or "CALCULATED"
    description: str


METRICS: dict[str, Metric] = {m.column: m for m in [
    Metric("avg_response_time_s", "Avg response time", "s", "lower", "MEASURED",
           "Mean of (finish - arrival) over completed requests"),
    Metric("p95_response_time_s", "p95 response time", "s", "lower", "MEASURED",
           "95th percentile of response time (linear interpolation)"),
    Metric("cpu_utilization", "CPU utilization", "fraction", "higher", "MEASURED",
           "Busy PE-seconds / provisioned PE-seconds (higher = less idle paid capacity)"),
    Metric("avg_billable_vms", "Avg VM count", "VMs", "lower", "MEASURED",
           "Mean of active + booting + draining VMs over time"),
    Metric("peak_billable_vms", "Peak VM count", "VMs", "lower", "MEASURED",
           "Maximum active + booting + draining VMs"),
    Metric("total_cost_usd", "Simulated cost", "USD", "lower", "CALCULATED",
           "Billed VM-seconds x assumed price per VM-hour"),
    Metric("total_scaling_actions", "Scaling actions", "count", "lower", "MEASURED",
           "Decisions that added or removed VMs (lower = more stable fleet)"),
    Metric("scale_up_actions", "Scale-up actions", "count", "lower", "MEASURED",
           "Decisions that added VMs"),
    Metric("scale_down_actions", "Scale-down actions", "count", "lower", "MEASURED",
           "Decisions that removed VMs"),
    Metric("requests_unfinished", "Unfinished requests", "count", "lower", "MEASURED",
           "Requests not finished when the run stopped"),
]}

KEY_METRICS = ["avg_response_time_s", "p95_response_time_s", "cpu_utilization", "avg_billable_vms",
               "total_cost_usd", "total_scaling_actions"]

# Two-sided 95% Student-t critical values, df = 1..30; beyond that the normal value is used.
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228, 2.201, 2.179, 2.160,
        2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086, 2.080, 2.074, 2.069, 2.064, 2.060, 2.056,
        2.052, 2.048, 2.045, 2.042]


def t_critical_95(df: int) -> float:
    if df < 1:
        return float("nan")
    return _T95[df - 1] if df <= len(_T95) else 1.960


def valid_runs(summary: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Splits runs into (valid, excluded). Excluded = drain timed out or no completed requests."""
    if summary.empty:
        return summary, summary
    bad = summary["drain_timed_out"].astype(bool) | (summary["requests_completed"] <= 0)
    return summary[~bad].copy(), summary[bad].copy()


def aggregate(summary: pd.DataFrame, metrics: list[str] | None = None) -> pd.DataFrame:
    """Per workload x policy x metric: n, mean, std, min, max and 95% CI half-width.

    ``std`` uses n-1 (sample standard deviation); with a single repetition std and CI are NaN,
    which the dashboard shows as "n/a" rather than zero.
    """
    metrics = metrics or KEY_METRICS
    valid, _ = valid_runs(summary)
    rows = []
    if valid.empty:
        return pd.DataFrame(columns=["workload_type", "policy", "metric", "n", "mean", "std", "min", "max", "ci95"])
    for (w, p), g in valid.groupby(["workload_type", "policy"], sort=False):
        for m in metrics:
            vals = g[m].astype(float).dropna().to_numpy()
            n = len(vals)
            mean = float(np.mean(vals)) if n else float("nan")
            std = float(np.std(vals, ddof=1)) if n > 1 else float("nan")
            ci = t_critical_95(n - 1) * std / math.sqrt(n) if n > 1 else float("nan")
            rows.append({"workload_type": w, "policy": p, "metric": m, "n": n, "mean": mean, "std": std,
                         "min": float(np.min(vals)) if n else float("nan"),
                         "max": float(np.max(vals)) if n else float("nan"), "ci95": ci})
    out = pd.DataFrame(rows)
    return _order(out)


def comparison_table(agg: pd.DataFrame, metric: str) -> pd.DataFrame:
    """Wide table for one metric: rows = workloads, columns = policies, cells = 'mean ± std'."""
    sub = agg[agg["metric"] == metric]
    if sub.empty:
        return pd.DataFrame()
    cell = sub.apply(lambda r: format_mean_std(r["mean"], r["std"], r["n"]), axis=1)
    wide = sub.assign(cell=cell).pivot(index="workload_type", columns="policy", values="cell")
    return wide.reindex(columns=[p for p in POLICIES if p in wide.columns])


def best_policies(agg: pd.DataFrame, metrics: list[str] | None = None) -> pd.DataFrame:
    """For each workload and metric, the policy with the best mean, and whether that lead is clear.

    The lead is reported as "clear" only when every repetition-based 95% CI is available and the
    best policy's interval does not overlap the runner-up's; with one repetition it is "single run".
    Ties on the mean are reported as ties. Nothing here assumes any policy should win.
    """
    metrics = metrics or KEY_METRICS
    rows = []
    for (w, m), g in agg[agg["metric"].isin(metrics)].groupby(["workload_type", "metric"], sort=False):
        g = g.dropna(subset=["mean"])
        if g.empty:
            continue
        lower = METRICS[m].better == "lower"
        g = g.sort_values("mean", ascending=lower)
        best = g.iloc[0]
        tied = g[np.isclose(g["mean"], best["mean"], rtol=1e-9, atol=1e-12)]
        if len(tied) > 1:
            rows.append({"workload_type": w, "metric": m, "best_policy": " = ".join(tied["policy"]),
                         "best_mean": best["mean"], "runner_up": "", "runner_up_mean": float("nan"),
                         "evidence": "tie"})
            continue
        second = g.iloc[1] if len(g) > 1 else None
        if second is None:
            evidence = "only one policy"
        elif best["n"] < 2 or second["n"] < 2 or np.isnan(best["ci95"]) or np.isnan(second["ci95"]):
            evidence = "single run (no variability estimate)"
        else:
            if lower:
                separated = best["mean"] + best["ci95"] < second["mean"] - second["ci95"]
            else:
                separated = best["mean"] - best["ci95"] > second["mean"] + second["ci95"]
            evidence = "clear (95% CIs do not overlap)" if separated else "not clear (95% CIs overlap)"
        rows.append({"workload_type": w, "metric": m, "best_policy": best["policy"], "best_mean": best["mean"],
                     "runner_up": "" if second is None else second["policy"],
                     "runner_up_mean": float("nan") if second is None else second["mean"], "evidence": evidence})
    out = pd.DataFrame(rows)
    if not out.empty:
        out["metric_label"] = out["metric"].map(lambda m: METRICS[m].label)
    return out


def win_counts(best: pd.DataFrame) -> pd.DataFrame:
    """How often each policy had the best mean, per metric (ties not counted)."""
    if best.empty:
        return pd.DataFrame()
    single = best[best["evidence"] != "tie"]
    counts = single.groupby(["metric", "best_policy"]).size().unstack(fill_value=0)
    counts.index = [METRICS[m].label for m in counts.index]
    return counts.reindex(columns=[p for p in POLICIES if p in counts.columns], fill_value=0)


def relative_to_baseline(agg: pd.DataFrame, baseline: str = "STATIC", metrics: list[str] | None = None) -> pd.DataFrame:
    """Percentage change of each policy's mean versus ``baseline`` (same workload).

    Positive means a larger value than the baseline; whether that is good depends on the metric.
    """
    metrics = metrics or KEY_METRICS
    sub = agg[agg["metric"].isin(metrics)]
    base = sub[sub["policy"] == baseline][["workload_type", "metric", "mean"]].rename(columns={"mean": "base"})
    merged = sub.merge(base, on=["workload_type", "metric"], how="inner")
    merged = merged[merged["policy"] != baseline]
    with np.errstate(divide="ignore", invalid="ignore"):
        merged["pct_change"] = np.where(merged["base"] != 0, (merged["mean"] - merged["base"]) / merged["base"] * 100, np.nan)
    return merged[["workload_type", "policy", "metric", "base", "mean", "pct_change"]]


def config_consistency(configs: dict[str, dict[str, str]]) -> pd.DataFrame:
    """Keys whose values differ between repetitions (other than the seed).

    An empty result means every repetition used the same configuration apart from its seed,
    which is the fairness condition for pooling repetitions.
    """
    if len(configs) < 2:
        return pd.DataFrame(columns=["key", "values"])
    keys = sorted(set().union(*[c.keys() for c in configs.values()]) - {"simulation.seed", "export.outputDir"})
    rows = []
    for k in keys:
        vals = {rep: c.get(k, "<missing>") for rep, c in configs.items()}
        if len(set(vals.values())) > 1:
            rows.append({"key": k, "values": "; ".join(f"{r}={v}" for r, v in sorted(vals.items()))})
    return pd.DataFrame(rows, columns=["key", "values"])


def resample_timeseries(ts: pd.DataFrame, window_s: float) -> pd.DataFrame:
    """Aggregates per-tick rows into windows: arrival rate (req/s), mean VM counts and utilization.

    Repetitions are averaged per window, so the curves show the mean behaviour over seeds.
    """
    if window_s <= 0:
        raise ValueError("window_s must be > 0")
    if ts.empty:
        return pd.DataFrame(columns=["workload_type", "policy", "window_start_s", "arrival_rate",
                                     "billable_vms", "provisioned_vms", "active_vm_utilization",
                                     "requests_in_system"])
    df = ts.copy()
    df["window_start_s"] = (df["time_s"] // window_s) * window_s
    group = ["workload_type", "policy", "rep", "window_start_s"] if "rep" in df.columns else \
        ["workload_type", "policy", "window_start_s"]
    per_rep = df.groupby(group, sort=True).agg(
        arrivals=("arrivals_in_tick", "sum"),
        ticks=("time_s", "count"),
        billable_vms=("billable_vms", "mean"),
        provisioned_vms=("provisioned_vms", "mean"),
        active_vm_utilization=("active_vm_utilization", "mean"),
        requests_in_system=("requests_in_system", "mean"),
    ).reset_index()
    tick = _tick_seconds(ts)
    per_rep["arrival_rate"] = per_rep["arrivals"] / (per_rep["ticks"] * tick)
    out = per_rep.groupby(["workload_type", "policy", "window_start_s"], sort=True)[
        ["arrival_rate", "billable_vms", "provisioned_vms", "active_vm_utilization", "requests_in_system"]
    ].mean().reset_index()
    return out


def scaling_actions(events: pd.DataFrame, include_non_actions: bool = False) -> pd.DataFrame:
    """Scaling events with a direction column; by default only decisions that changed the fleet."""
    if events.empty:
        return events
    df = events if include_non_actions else events[events["applied_delta"] != 0]
    df = df.copy()
    df["direction"] = np.select([df["applied_delta"] > 0, df["applied_delta"] < 0], ["UP", "DOWN"], default="NONE")
    df.loc[(df["direction"] == "NONE") & df["cooldown_blocked"], "direction"] = "BLOCKED (cooldown)"
    return df


def format_mean_std(mean: float, std: float, n: int, digits: int = 3) -> str:
    if mean is None or (isinstance(mean, float) and math.isnan(mean)):
        return "n/a"
    s = f"{mean:.{digits}g}" if abs(mean) >= 1000 else f"{mean:.{digits}f}"
    if n and n > 1 and not math.isnan(std):
        s += f" ± {std:.{digits}f}"
    return s


def _tick_seconds(ts: pd.DataFrame) -> float:
    times = np.sort(ts["time_s"].unique())
    if len(times) < 2:
        return 1.0
    return float(np.min(np.diff(times)))


def _order(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    pol_rank = {p: i for i, p in enumerate(POLICIES)}
    wl_rank = {w: i for i, w in enumerate(WORKLOADS)}
    return df.assign(_w=df["workload_type"].map(wl_rank).fillna(99), _p=df["policy"].map(pol_rank).fillna(99)).sort_values(
        ["_w", "_p"], kind="stable").drop(columns=["_w", "_p"]).reset_index(drop=True)
