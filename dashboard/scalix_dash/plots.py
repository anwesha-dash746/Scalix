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
from .story import policy_name, scenario_name

POLICY_COLORS = {"STATIC": "#7c8798", "THRESHOLD": "#d1803a", "SCALIX": "#0f766e"}
INK = "#1f2328"
MUTED = "#6b7280"
PAPER = "#fbfaf7"

plt.rcParams.update({
    "font.family": ["Segoe UI", "Helvetica Neue", "Arial", "DejaVu Sans"],
    "font.size": 9.5,
    "axes.edgecolor": "#d8d2c6",
    "axes.labelcolor": INK,
    "axes.titleweight": "semibold",
    "axes.titlesize": 11,
    "axes.titlelocation": "left",
    "axes.spines.top": False,
    "axes.spines.right": False,
    "xtick.color": MUTED,
    "ytick.color": MUTED,
    "grid.color": "#ebe6dc",
    "grid.linewidth": 0.8,
    "legend.frameon": False,
    "figure.facecolor": PAPER,
    "axes.facecolor": PAPER,
    "savefig.facecolor": PAPER,
})


def _color(policy: str) -> str:
    return POLICY_COLORS.get(policy, "#4b5563")


def metric_bars(agg: pd.DataFrame, metric: str) -> Figure:
    """Grouped bars: scenarios on x, one bar per policy, error bars = 95% CI when available."""
    sub = agg[agg["metric"] == metric]
    workloads = list(dict.fromkeys(sub["workload_type"]))
    policies = [p for p in POLICIES if p in set(sub["policy"])]
    fig, ax = plt.subplots(figsize=(9, 3.4))
    width = 0.8 / max(1, len(policies))
    x = np.arange(len(workloads))
    for i, p in enumerate(policies):
        rows = sub[sub["policy"] == p].set_index("workload_type").reindex(workloads)
        err = rows["ci95"].to_numpy(dtype=float)
        ax.bar(x + (i - (len(policies) - 1) / 2) * width, rows["mean"].to_numpy(dtype=float), width * 0.92,
               yerr=None if np.all(np.isnan(err)) else np.nan_to_num(err), capsize=3, label=policy_name(p),
               color=_color(p), error_kw={"elinewidth": 1, "ecolor": INK})
    m = METRICS[metric]
    ax.set_xticks(x, [scenario_name(w) for w in workloads])
    ax.set_ylabel(m.unit)
    ax.set_title(f"{m.label} ({m.better} is better)")
    ax.grid(axis="y")
    ax.set_axisbelow(True)
    ax.legend(ncol=len(policies), loc="upper right")
    fig.tight_layout()
    return fig


def tradeoff_scatter(agg: pd.DataFrame, x_metric: str, y_metric: str, workload: str | None = None) -> Figure:
    """Each point = one policy on one scenario (mean); bottom-left is best when both are 'lower is better'."""
    fig, ax = plt.subplots(figsize=(6.5, 4.0))
    sub = agg if workload is None else agg[agg["workload_type"] == workload]
    xs = sub[sub["metric"] == x_metric].set_index(["workload_type", "policy"])
    ys = sub[sub["metric"] == y_metric].set_index(["workload_type", "policy"])
    joined = xs[["mean", "ci95"]].join(ys[["mean", "ci95"]], lsuffix="_x", rsuffix="_y", how="inner").reset_index()
    for p in POLICIES:
        pts = joined[joined["policy"] == p]
        if pts.empty:
            continue
        ax.errorbar(pts["mean_x"], pts["mean_y"], xerr=np.nan_to_num(pts["ci95_x"].to_numpy(float)),
                    yerr=np.nan_to_num(pts["ci95_y"].to_numpy(float)), fmt="o", ms=7, color=_color(p),
                    label=policy_name(p), capsize=2, elinewidth=0.8)
        if workload is None:
            for _, r in pts.iterrows():
                ax.annotate(scenario_name(r["workload_type"]), (r["mean_x"], r["mean_y"]), fontsize=7.5,
                            color=MUTED, xytext=(4, 3), textcoords="offset points")
    mx, my = METRICS[x_metric], METRICS[y_metric]
    ax.set_xlabel(f"{mx.label} ({mx.unit})")
    ax.set_ylabel(f"{my.label} ({my.unit})")
    ax.grid()
    ax.set_axisbelow(True)
    ax.legend()
    fig.tight_layout()
    return fig


def timeseries_panels(rs: pd.DataFrame, workload: str, policies: list[str]) -> Figure:
    """Three stacked panels: incoming traffic, servers running, how busy they were."""
    sub = rs[(rs["workload_type"] == workload) & (rs["policy"].isin(policies))]
    fig, axes = plt.subplots(3, 1, figsize=(9, 6.6), sharex=True, gridspec_kw={"height_ratios": [1, 1.3, 1]})
    # Arrivals are identical for all policies (same request sequence), so plot them once.
    first = sub[sub["policy"] == policies[0]] if policies else sub
    axes[0].fill_between(first["window_start_s"], first["arrival_rate"], color="#cfc8ba", alpha=0.6, lw=0)
    axes[0].plot(first["window_start_s"], first["arrival_rate"], color=INK, lw=1.2)
    axes[0].set_ylabel("Requests / s")
    axes[0].set_title("Incoming traffic")
    for p in policies:
        d = sub[sub["policy"] == p]
        axes[1].step(d["window_start_s"], d["billable_vms"], where="post", color=_color(p), label=policy_name(p), lw=1.8)
        axes[2].plot(d["window_start_s"], d["active_vm_utilization"] * 100, color=_color(p), lw=1.4)
    axes[1].set_ylabel("Servers")
    axes[1].set_title("Servers each strategy was paying for")
    axes[1].legend(ncol=3, loc="upper left")
    axes[2].set_ylabel("% busy")
    axes[2].set_ylim(-2, 105)
    axes[2].set_title("How busy those servers were")
    axes[2].set_xlabel("Time into the simulation (seconds)")
    for ax in axes:
        ax.grid(axis="y")
        ax.set_axisbelow(True)
    fig.tight_layout(h_pad=1.2)
    return fig


def close(fig: Figure) -> None:
    plt.close(fig)
