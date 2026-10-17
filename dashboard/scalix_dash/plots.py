"""Matplotlib figures for the dashboard. Each function returns a Figure built from real data."""

from __future__ import annotations

import matplotlib

matplotlib.use("Agg")  # headless: Streamlit renders the figure itself

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
from matplotlib.figure import Figure

from . import POLICIES
from .analysis import METRICS

POLICY_COLORS = {"STATIC": "#6b7280", "THRESHOLD": "#d97706", "SCALIX": "#2563eb"}


def _color(policy: str) -> str:
    return POLICY_COLORS.get(policy, "#10b981")


def metric_bars(agg: pd.DataFrame, metric: str) -> Figure:
    """Grouped bars: workloads on x, one bar per policy, error bars = 95% CI when available."""
    sub = agg[agg["metric"] == metric]
    workloads = list(dict.fromkeys(sub["workload_type"]))
    policies = [p for p in POLICIES if p in set(sub["policy"])]
    fig, ax = plt.subplots(figsize=(9, 3.6))
    width = 0.8 / max(1, len(policies))
    x = np.arange(len(workloads))
    for i, p in enumerate(policies):
        rows = sub[sub["policy"] == p].set_index("workload_type").reindex(workloads)
        err = rows["ci95"].to_numpy(dtype=float)
        ax.bar(x + (i - (len(policies) - 1) / 2) * width, rows["mean"].to_numpy(dtype=float), width,
               yerr=None if np.all(np.isnan(err)) else np.nan_to_num(err), capsize=3, label=p, color=_color(p))
    m = METRICS[metric]
    ax.set_xticks(x, workloads, rotation=0, fontsize=8)
    ax.set_ylabel(f"{m.label} ({m.unit})")
    ax.set_title(f"{m.label} — {m.better} is better" + (" (calculated)" if m.kind == "CALCULATED" else ""), fontsize=10)
    ax.legend(fontsize=8, frameon=False)
    ax.grid(axis="y", alpha=0.3)
    fig.tight_layout()
    return fig


def tradeoff_scatter(agg: pd.DataFrame, x_metric: str, y_metric: str, workload: str | None = None) -> Figure:
    """Each point = one policy on one workload (mean); lower-left is better when both are 'lower'."""
    fig, ax = plt.subplots(figsize=(6.5, 4.2))
    sub = agg if workload is None else agg[agg["workload_type"] == workload]
    xs = sub[sub["metric"] == x_metric].set_index(["workload_type", "policy"])
    ys = sub[sub["metric"] == y_metric].set_index(["workload_type", "policy"])
    joined = xs[["mean", "ci95"]].join(ys[["mean", "ci95"]], lsuffix="_x", rsuffix="_y", how="inner").reset_index()
    for p in POLICIES:
        pts = joined[joined["policy"] == p]
        if pts.empty:
            continue
        ax.errorbar(pts["mean_x"], pts["mean_y"], xerr=np.nan_to_num(pts["ci95_x"].to_numpy(float)),
                    yerr=np.nan_to_num(pts["ci95_y"].to_numpy(float)), fmt="o", color=_color(p), label=p,
                    capsize=2, alpha=0.9)
        for _, r in pts.iterrows():
            ax.annotate(r["workload_type"][:4], (r["mean_x"], r["mean_y"]), fontsize=7,
                        xytext=(3, 3), textcoords="offset points")
    mx, my = METRICS[x_metric], METRICS[y_metric]
    ax.set_xlabel(f"{mx.label} ({mx.unit}) — {mx.better} is better")
    ax.set_ylabel(f"{my.label} ({my.unit}) — {my.better} is better")
    ax.grid(alpha=0.3)
    ax.legend(fontsize=8, frameon=False)
    fig.tight_layout()
    return fig


def timeseries_panels(rs: pd.DataFrame, workload: str, policies: list[str]) -> Figure:
    """Three stacked panels: workload intensity, VM count, utilization (windowed means)."""
    sub = rs[(rs["workload_type"] == workload) & (rs["policy"].isin(policies))]
    fig, axes = plt.subplots(3, 1, figsize=(9, 7.2), sharex=True)
    # Arrivals are identical for all policies (same request sequence), so plot them once.
    first = sub[sub["policy"] == policies[0]] if policies else sub
    axes[0].plot(first["window_start_s"], first["arrival_rate"], color="#111827", lw=1.4)
    axes[0].set_ylabel("Arrival rate\n(req/s)")
    axes[0].set_title(f"{workload}: workload intensity, VM count and utilization", fontsize=10)
    for p in policies:
        d = sub[sub["policy"] == p]
        axes[1].step(d["window_start_s"], d["billable_vms"], where="post", color=_color(p), label=p, lw=1.4)
        axes[2].plot(d["window_start_s"], d["active_vm_utilization"], color=_color(p), label=p, lw=1.2)
    axes[1].set_ylabel("VMs (billable)")
    axes[2].set_ylabel("Utilization of\nactive VMs")
    axes[2].set_ylim(-0.02, 1.05)
    axes[2].set_xlabel("Simulated time (s)")
    for ax in axes:
        ax.grid(alpha=0.3)
    axes[1].legend(fontsize=8, frameon=False, ncol=3)
    fig.tight_layout()
    return fig


def close(fig: Figure) -> None:
    plt.close(fig)
