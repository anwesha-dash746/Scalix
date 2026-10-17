"""Over time: how each strategy reacted to the traffic."""

from __future__ import annotations

import streamlit as st

from scalix_dash import plots
from scalix_dash.analysis import resample_timeseries
from scalix_dash.story import policy_name, scenario_name

from .common import Context, policies_of, require_experiment, workloads_of


def render(ctx: Context) -> None:
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    st.markdown('<div class="sx-kicker">Over time</div>', unsafe_allow_html=True)
    st.header("Watch each strategy react")
    st.markdown('<p class="sx-lede">The top chart is the traffic. Below it: how many servers each strategy paid for, '
                "and how busy those servers were. A good strategy adds servers just before they're needed and "
                "gives them back when traffic drops.</p>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.2, 2, 1])
    workload = c1.selectbox("Traffic pattern", workloads_of(exp), format_func=scenario_name, key="ts_workload")
    policies = c2.multiselect("Strategies", policies_of(exp), default=policies_of(exp), format_func=policy_name,
                              key="ts_policies")
    reps = sorted(exp.timeseries["rep"].unique())
    rep = c3.selectbox("Run", ["Average of all"] + reps, key="ts_rep")
    if not policies:
        st.info("Pick at least one strategy.")
        return
    ts = exp.timeseries[exp.timeseries["workload_type"] == workload]
    if rep != "Average of all":
        ts = ts[ts["rep"] == rep]
    with st.expander("Chart settings"):
        default_window = int(exp.summary["decision_interval_s"].iloc[0])
        window = st.slider("Smooth over this many seconds", 1, 120, default_window, key="ts_window")
    rs = resample_timeseries(ts, float(window))
    fig = plots.timeseries_panels(rs, workload, policies)
    st.pyplot(fig)
    plots.close(fig)
    with st.expander("The numbers behind the chart"):
        st.dataframe(rs[rs["policy"].isin(policies)], hide_index=True, width="stretch")
