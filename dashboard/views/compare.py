"""Policy Comparison: same conditions, all policies, with variability and trade-offs."""

from __future__ import annotations

import streamlit as st

from scalix_dash import plots
from scalix_dash.analysis import (KEY_METRICS, METRICS, aggregate, best_policies, comparison_table,
                                  config_consistency, relative_to_baseline, valid_runs, win_counts)

from .common import Context, context_panel, policies_of, require_experiment, workloads_of


def render(ctx: Context) -> None:
    st.subheader("Policy Comparison")
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    policies = policies_of(exp)
    if len(policies) < 2:
        st.info("This experiment contains a single policy; run STATIC, THRESHOLD and SCALIX together to compare.")
    workloads = st.multiselect("Workloads", workloads_of(exp), default=workloads_of(exp), key="cmp_workloads")
    summary = exp.summary[exp.summary["workload_type"].isin(workloads)]
    valid, excluded = valid_runs(summary)
    if not excluded.empty:
        st.warning(f"{len(excluded)} run(s) excluded from statistics (drain timeout): "
                   + ", ".join(excluded["run_id"]))
    agg = aggregate(summary, list(METRICS))
    if agg.empty:
        st.error("No valid runs to compare.")
        return

    _fairness(ctx)

    metric = st.selectbox("Metric", KEY_METRICS + [m for m in METRICS if m not in KEY_METRICS],
                          format_func=lambda m: f"{METRICS[m].label} ({METRICS[m].unit}, {METRICS[m].better} is better)",
                          key="cmp_metric")
    m = METRICS[metric]
    st.caption(f"{m.kind}: {m.description}. Cells show mean ± sample std over repetitions "
               f"(n = {int(agg['n'].max())} max); bars show 95% CI when n ≥ 2.")
    st.dataframe(comparison_table(agg, metric), width="stretch")
    if 1 < int(agg["n"].max()) < 5:
        st.caption("Few repetitions make 95% intervals wide (n = 2 uses t = 12.71, n = 3 uses t = 4.30). "
                   "Intervals are shown as computed, without clipping; add repetitions to narrow them.")
    fig = plots.metric_bars(agg, metric)
    st.pyplot(fig)
    plots.close(fig)

    st.markdown("#### Trade-offs")
    c1, c2 = st.columns([1, 2])
    with c1:
        xm = st.selectbox("X axis", KEY_METRICS, index=KEY_METRICS.index("total_cost_usd"),
                          format_func=lambda k: METRICS[k].label, key="cmp_x")
        ym = st.selectbox("Y axis", KEY_METRICS, index=KEY_METRICS.index("p95_response_time_s"),
                          format_func=lambda k: METRICS[k].label, key="cmp_y")
        wl = st.selectbox("Workload", ["(all)"] + workloads, key="cmp_scatter_wl")
    with c2:
        fig = plots.tradeoff_scatter(agg, xm, ym, None if wl == "(all)" else wl)
        st.pyplot(fig)
        plots.close(fig)

    st.markdown("#### Which policy did best, per workload and metric")
    st.caption("Best = best mean. 'Clear' requires non-overlapping 95% confidence intervals from repetitions; "
               "with one repetition no claim about a real difference can be made. Every policy can win here.")
    best = best_policies(agg)
    if not best.empty:
        st.dataframe(best[["workload_type", "metric_label", "best_policy", "best_mean", "runner_up",
                           "runner_up_mean", "evidence"]], hide_index=True, width="stretch")
        st.markdown("**Number of workloads where each policy had the best mean**")
        st.dataframe(win_counts(best), width="stretch")

    if "STATIC" in policies:
        st.markdown("#### Change versus STATIC (same workload)")
        rel = relative_to_baseline(agg, "STATIC")
        if not rel.empty:
            rel = rel.assign(metric=rel["metric"].map(lambda k: METRICS[k].label))
            wide = rel.pivot_table(index=["workload_type", "policy"], columns="metric", values="pct_change")
            st.caption("Percent change of the mean relative to STATIC. Whether higher is good depends on the metric "
                       "(e.g. higher CPU utilization is good, higher cost is not).")
            st.dataframe(wide.style.format("{:+.1f}%", na_rep="n/a"), width="stretch")

    with st.expander("All statistics (long format)"):
        st.dataframe(agg, hide_index=True, width="stretch")
    context_panel(exp)


def _fairness(ctx: Context) -> None:
    diffs = config_consistency(ctx.exp.configs)
    if diffs.empty:
        st.success("Fairness check: all repetitions used identical configuration apart from the seed, and within a "
                   "repetition all policies shared the same requests, VM type, timing and pricing.")
    else:
        st.error("Fairness check failed: repetitions differ in configuration beyond the seed. "
                 "Comparisons across these repetitions are not like-for-like.")
        st.dataframe(diffs, hide_index=True, width="stretch")
