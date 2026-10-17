"""Overview: headline metrics of the selected experiment, per policy, for one workload."""

from __future__ import annotations

import streamlit as st

from scalix_dash.analysis import KEY_METRICS, METRICS, aggregate, valid_runs

from .common import Context, context_panel, fmt_value, policies_of, require_experiment, workloads_of


def render(ctx: Context) -> None:
    st.subheader("Overview")
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    workload = st.selectbox("Workload", workloads_of(exp), key="ov_workload")
    agg = aggregate(exp.summary[exp.summary["workload_type"] == workload], KEY_METRICS)
    _, excluded = valid_runs(exp.summary[exp.summary["workload_type"] == workload])
    if not excluded.empty:
        st.warning(f"{len(excluded)} run(s) hit the drain limit with unfinished requests and are excluded below.")
    if agg.empty:
        st.error("No valid runs for this workload.")
        return

    st.caption("Values are means over valid repetitions; ± is the sample standard deviation "
               "(shown when there are 2 or more repetitions). Cost is calculated from measured VM time "
               "with the assumed pricing model; all other values are measured.")
    policies = policies_of(exp)
    cols = st.columns(len(policies))
    for col, p in zip(cols, policies):
        with col:
            st.markdown(f"#### {p}")
            sub = agg[agg["policy"] == p].set_index("metric")
            if sub.empty:
                st.write("No valid runs.")
                continue
            for m in KEY_METRICS:
                meta = METRICS[m]
                row = sub.loc[m]
                spread = f"± {fmt_value(row['std'], meta.unit)}" if row["n"] > 1 and row["std"] == row["std"] else ""
                st.metric(f"{meta.label}{' (calc.)' if meta.kind == 'CALCULATED' else ''}",
                          fmt_value(row["mean"], meta.unit), help=f"{meta.description}. n={int(row['n'])}.")
                if spread:
                    st.caption(spread)
    ups = exp.summary[exp.summary["workload_type"] == workload].groupby("policy")[
        ["scale_up_actions", "scale_down_actions", "vms_added", "vms_removed", "cooldown_suppressions"]].sum()
    st.markdown("**Scaling-action totals across all repetitions**")
    st.dataframe(ups.reindex(policies), width="stretch")
    context_panel(exp, workload)
