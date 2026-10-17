"""Time-Series Analysis: workload intensity, VM count and utilization over time."""

from __future__ import annotations

import streamlit as st

from scalix_dash import plots
from scalix_dash.analysis import resample_timeseries

from .common import Context, policies_of, require_experiment, workloads_of


def render(ctx: Context) -> None:
    st.subheader("Time-Series Analysis")
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
    workload = c1.selectbox("Workload", workloads_of(exp), key="ts_workload")
    policies = c2.multiselect("Policies", policies_of(exp), default=policies_of(exp), key="ts_policies")
    reps = sorted(exp.timeseries["rep"].unique())
    rep = c3.selectbox("Repetition", ["mean of all"] + reps, key="ts_rep")
    default_window = int(exp.summary["decision_interval_s"].iloc[0])
    window = c4.number_input("Window (s)", 1, 600, default_window, key="ts_window",
                             help="Per-tick samples are averaged over windows of this length.")
    if not policies:
        st.info("Select at least one policy.")
        return
    ts = exp.timeseries[exp.timeseries["workload_type"] == workload]
    if rep != "mean of all":
        ts = ts[ts["rep"] == rep]
    rs = resample_timeseries(ts, float(window))
    fig = plots.timeseries_panels(rs, workload, policies)
    st.pyplot(fig)
    plots.close(fig)
    st.caption("Top: measured arrival rate (identical for all policies within a repetition). Middle: VMs being paid "
               "for (active + booting + draining). Bottom: mean CPU utilization of active VMs "
               "(Vm.getCpuPercentUtilization). With 'mean of all', each point averages the repetitions.")
    with st.expander("Windowed data"):
        st.dataframe(rs[rs["policy"].isin(policies)], hide_index=True, width="stretch")
