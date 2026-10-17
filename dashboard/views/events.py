"""Decision log: every time a strategy added or removed servers, and why."""

from __future__ import annotations

import streamlit as st

from scalix_dash.analysis import scaling_actions
from scalix_dash.story import policy_name, scenario_name

from .common import Context, policies_of, require_experiment, workloads_of


def _what(row) -> str:
    d = int(row["applied_delta"])
    if d > 0:
        return f"Added {d} server{'s' if d != 1 else ''}"
    if d < 0:
        return f"Removed {-d} server{'s' if d != -1 else ''}"
    return "Held off (cooling down)" if row["cooldown_blocked"] else "No change"


def render(ctx: Context) -> None:
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    st.markdown('<div class="sx-kicker">Decision log</div>', unsafe_allow_html=True)
    st.header("Every decision, with its reason")
    st.markdown('<p class="sx-lede">Each strategy checks the servers every few seconds and decides whether to add '
                "or remove some. This is its diary: what it saw, what it did, and why.</p>", unsafe_allow_html=True)
    c1, c2, c3 = st.columns([1.2, 2, 1])
    workload = c1.selectbox("Traffic pattern", workloads_of(exp), format_func=scenario_name, key="ev_workload")
    policies = c2.multiselect("Strategies", policies_of(exp), default=[p for p in policies_of(exp) if p != "STATIC"]
                              or policies_of(exp), format_func=policy_name, key="ev_policies")
    reps = sorted(exp.events["rep"].unique()) if not exp.events.empty else []
    rep = c3.selectbox("Run", ["All runs"] + reps, key="ev_rep")
    show_all = st.toggle("Also show checks where nothing changed", value=False, key="ev_all")

    ev = exp.events[(exp.events["workload_type"] == workload) & (exp.events["policy"].isin(policies))]
    if rep != "All runs":
        ev = ev[ev["rep"] == rep]
    table = scaling_actions(ev, include_non_actions=show_all)
    if table.empty:
        st.info("No changes to show. The fixed fleet never resizes, by design.")
        return
    added = int(table.loc[table["applied_delta"] > 0, "applied_delta"].sum())
    removed = int(-table.loc[table["applied_delta"] < 0, "applied_delta"].sum())
    a, b, c = st.columns(3)
    a.metric("Resizing decisions", int((table["applied_delta"] != 0).sum()))
    b.metric("Servers added", added)
    c.metric("Servers removed", removed)

    view = table.sort_values(["rep", "time_s", "policy"]).assign(
        strategy=lambda t: t["policy"].map(policy_name),
        what=lambda t: t.apply(_what, axis=1),
        servers=lambda t: t["vms_before"].astype(str) + " → " + t["vms_after"].astype(str),
        busy=lambda t: (t["interval_utilization"] * 100).round(0),
    )
    st.dataframe(
        view[["rep", "time_s", "strategy", "what", "servers", "busy", "interval_arrival_rate", "trend", "reason"]],
        hide_index=True, width="stretch",
        column_config={
            "rep": "Run", "time_s": st.column_config.NumberColumn("Time (s)", format="%.0f"),
            "strategy": "Strategy", "what": "What it did", "servers": "Servers",
            "busy": st.column_config.NumberColumn("% busy", format="%.0f%%"),
            "interval_arrival_rate": st.column_config.NumberColumn("Requests/s", format="%.1f"),
            "trend": "Demand trend", "reason": st.column_config.TextColumn("Why (recorded by the strategy)", width="large"),
        })
    st.caption("Removed servers first finish the requests already queued on them before they are switched off.")
