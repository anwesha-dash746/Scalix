"""Plotly figure builders. Pure functions of DataFrames: no Streamlit, easy to test.

Conventions: one y-axis per chart; 2 px lines; recessive solid hairline grid; a legend whenever
there are two or more series; unified hover. Each strategy keeps the same colour everywhere
(colour follows the entity, never its rank). The palette passed the colour-vision checks on the
app surface; SCALIX's aqua is below 3:1 contrast, so every chart also has a legend and the
numbers are always available in a table view.
"""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go

POLICY_ORDER = ("STATIC", "THRESHOLD", "SCALIX")
POLICY_COLORS = {"STATIC": "#2a78d6", "THRESHOLD": "#eb6834", "SCALIX": "#1baf7a"}
POLICY_LABELS = {"STATIC": "Static (fixed)", "THRESHOLD": "Threshold (70/30)", "SCALIX": "SCALIX (workload-aware)"}
POLICY_SHORT = {"STATIC": "Static", "THRESHOLD": "Threshold", "SCALIX": "SCALIX"}

SURFACE = "#fbfaf7"
TEXT = "#1f2328"
TEXT_MUTED = "#52514e"
GRID = "#e9e5dc"
NEUTRAL_DARK = "#3d3c39"
NEUTRAL_LIGHT = "#a3a199"


def _layout(fig: go.Figure, title: str, y_title: str, *, height: int = 330, y_range=None,
            y_format: str | None = None, legend: bool = True) -> go.Figure:
    fig.update_layout(
        title=dict(text=title, font=dict(size=15, color=TEXT), x=0, xanchor="left"),
        height=height, margin=dict(l=64, r=24, t=72, b=56),
        paper_bgcolor=SURFACE, plot_bgcolor=SURFACE,
        font=dict(family="'Segoe UI', 'Helvetica Neue', Arial, sans-serif", size=12, color=TEXT_MUTED),
        hovermode="x unified", showlegend=legend,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, xanchor="right", x=1, title=None),
    )
    fig.update_xaxes(title="time (minutes)", showgrid=False, linecolor=GRID, ticks="outside", tickcolor=GRID, automargin=True)
    fig.update_yaxes(title=y_title, gridcolor=GRID, gridwidth=1, zeroline=False, linecolor=GRID, automargin=True,
                     range=y_range, tickformat=y_format)
    return fig


def _minutes(ts: pd.DataFrame) -> pd.Series:
    return ts["time_s"] / 60.0


def demand_chart(workload: pd.DataFrame) -> go.Figure:
    """Expected demand curve and the random arrivals actually drawn, in requests/s."""
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=_minutes(workload), y=workload["arrival_rate_rps"], name="arrivals (random draw)",
                             mode="lines", line=dict(color=NEUTRAL_LIGHT, width=1.5),
                             hovertemplate="%{y:.1f} req/s"))
    fig.add_trace(go.Scatter(x=_minutes(workload), y=workload["expected_rate_rps"], name="expected demand",
                             mode="lines", line=dict(color=NEUTRAL_DARK, width=2),
                             hovertemplate="%{y:.1f} req/s"))
    return _layout(fig, "Incoming demand", "requests per second", y_range=[0, None])


def utilization_chart(ts: pd.DataFrame, threshold: float = 0.70) -> go.Figure:
    """Estimated utilization of serving workers per strategy (0-100 %)."""
    fig = go.Figure()
    for p in POLICY_ORDER:
        d = ts[ts["policy"] == p]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(x=_minutes(d), y=d["utilization"], name=POLICY_LABELS[p], mode="lines",
                                 line=dict(color=POLICY_COLORS[p], width=2), hovertemplate="%{y:.0%}"))
    fig.add_hline(y=threshold, line=dict(color=TEXT_MUTED, width=1),
                  annotation_text=f"{threshold:.0%} threshold", annotation_position="top right",
                  annotation_font=dict(size=11, color=TEXT_MUTED), annotation_bgcolor=SURFACE)
    return _layout(fig, "How busy the workers were (estimated)", "utilization", y_range=[0, 1.05], y_format=".0%")


def workers_chart(ts: pd.DataFrame, policies=POLICY_ORDER, title: str = "Workers paid for") -> go.Figure:
    """Provisioned workers (serving + booting) per strategy, as steps."""
    fig = go.Figure()
    for p in policies:
        d = ts[ts["policy"] == p]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(x=_minutes(d), y=d["provisioned"], name=POLICY_LABELS[p], mode="lines",
                                 line=dict(color=POLICY_COLORS[p], width=2, shape="hv"),
                                 hovertemplate="%{y} workers"))
    return _layout(fig, title, "workers", y_range=[0, max(1, ts["provisioned"].max()) * 1.15],
                   legend=len(policies) > 1)


def queue_chart(ts: pd.DataFrame) -> go.Figure:
    """Requests waiting (unserved demand) per strategy."""
    fig = go.Figure()
    for p in POLICY_ORDER:
        d = ts[ts["policy"] == p]
        if d.empty:
            continue
        fig.add_trace(go.Scatter(x=_minutes(d), y=d["queue"], name=POLICY_LABELS[p], mode="lines",
                                 line=dict(color=POLICY_COLORS[p], width=2), hovertemplate="%{y:.0f} waiting"))
    return _layout(fig, "Requests waiting (overload)", "requests waiting", y_range=[0, None])


def metric_bars(agg: pd.DataFrame, metric: str, label: str, fmt: str = ".2f") -> go.Figure:
    """Mean per strategy with a 95% confidence-interval whisker (when there is more than one run)."""
    rows = agg[agg["metric"] == metric].set_index("policy").reindex([p for p in POLICY_ORDER])
    rows = rows.dropna(subset=["mean"])
    err = (rows["ci95_high"] - rows["mean"]).fillna(0)
    fig = go.Figure(go.Bar(
        x=[POLICY_LABELS[p] for p in rows.index], y=rows["mean"],
        marker=dict(color=[POLICY_COLORS[p] for p in rows.index], line=dict(width=0), cornerradius=4),
        error_y=dict(type="data", array=err, visible=bool((err > 0).any()), color=TEXT_MUTED, thickness=1, width=6),
        # Value labels only when there is no whisker to collide with; the table and tooltip carry values.
        text=None if bool((err > 0).any()) else [format(v, fmt) for v in rows["mean"]],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{x}: %{y:" + fmt + "}<extra></extra>", width=0.5,
    ))
    fig = _layout(fig, label, label, height=300, legend=False)
    fig.update_xaxes(title=None)
    fig.update_layout(hovermode="closest", bargap=0.4)
    return fig
