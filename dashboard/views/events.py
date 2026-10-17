"""Scaling Event Log: when each policy scaled, in which direction, and why."""

from __future__ import annotations

import streamlit as st

from scalix_dash.analysis import scaling_actions

from .common import Context, policies_of, require_experiment, workloads_of

COLUMNS = ["rep", "time_s", "policy", "direction", "applied_delta", "vms_before", "vms_after",
           "interval_utilization", "interval_arrival_rate", "trend", "cooldown_blocked", "reason"]


def render(ctx: Context) -> None:
    st.subheader("Scaling Event Log")
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    c1, c2, c3, c4 = st.columns([1, 2, 1, 1])
    workload = c1.selectbox("Workload", workloads_of(exp), key="ev_workload")
    policies = c2.multiselect("Policies", policies_of(exp), default=policies_of(exp), key="ev_policies")
    reps = sorted(exp.events["rep"].unique()) if not exp.events.empty else []
    rep = c3.selectbox("Repetition", ["all"] + reps, key="ev_rep")
    show_all = c4.toggle("Show non-actions too", value=False, key="ev_all",
                         help="Include decisions that kept the fleet unchanged (e.g. blocked by cooldown).")
    ev = exp.events[(exp.events["workload_type"] == workload) & (exp.events["policy"].isin(policies))]
    if rep != "all":
        ev = ev[ev["rep"] == rep]
    table = scaling_actions(ev, include_non_actions=show_all)
    if table.empty:
        st.info("No scaling actions for this selection (STATIC never scales).")
        return
    counts = table.groupby(["policy", "direction"]).size().unstack(fill_value=0)
    st.dataframe(counts, width="stretch")
    st.dataframe(table[COLUMNS].sort_values(["rep", "time_s", "policy"]), hide_index=True, width="stretch",
                 column_config={"reason": st.column_config.TextColumn("reason", width="large"),
                                "interval_utilization": st.column_config.NumberColumn(format="%.3f"),
                                "interval_arrival_rate": st.column_config.NumberColumn(format="%.2f")})
    st.caption("Reasons are recorded by the policy at decision time (Part 1 engine). VM counts are ACTIVE + BOOTING "
               "before and after the decision; removed VMs drain their queue before being destroyed.")
