"""Results: a plain-language verdict first, the details on request."""

from __future__ import annotations

import streamlit as st

from scalix_dash import plots
from scalix_dash.analysis import (KEY_METRICS, METRICS, aggregate, best_policies, comparison_table,
                                  config_consistency, relative_to_baseline, resample_timeseries, win_counts)
from scalix_dash.story import (PLAIN_METRICS, fmt, policy_name, scenario_description, scenario_name,
                               tradeoff_sentences, verdicts)

from .common import Context, excluded_note, policies_of, policy_heading, require_experiment, workloads_of


def render(ctx: Context) -> None:
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    workloads = workloads_of(exp)
    st.markdown('<div class="sx-kicker">Results</div>', unsafe_allow_html=True)
    if len(workloads) > 1:
        workload = st.pills("Traffic pattern", workloads, default=workloads[0], format_func=scenario_name,
                            key="res_workload", required=True, label_visibility="collapsed") or workloads[0]
    else:
        workload = workloads[0]
    st.header(scenario_name(workload))
    n_runs = exp.n_reps
    st.markdown(f'<p class="sx-lede">{scenario_description(workload)} Each strategy faced exactly the same requests '
                f'({n_runs} run{"s" if n_runs != 1 else ""} with different random traffic).</p>', unsafe_allow_html=True)

    summary = exp.summary[exp.summary["workload_type"] == workload]
    excluded_note(summary)
    agg = aggregate(summary, list(METRICS))
    if agg.empty:
        st.error("None of the runs for this traffic pattern produced usable results.")
        return

    # ---- verdict -------------------------------------------------------------------------
    vs = verdicts(agg, workload)
    if vs:
        cols = st.columns(len(vs))
        for col, v in zip(cols, vs):
            with col.container(border=True, height="stretch"):
                st.markdown(f'<div class="sx-kicker">{v.title}</div><div class="sx-big">{v.policy}</div>'
                            f'<div class="sx-value">{v.value}</div><div class="sx-note">{v.note}</div>',
                            unsafe_allow_html=True)
        for sentence in tradeoff_sentences(agg, workload):
            st.markdown(f"> {sentence}")
    st.write("")

    # ---- strategy cards ------------------------------------------------------------------
    st.subheader("Strategy by strategy")
    policies = policies_of(exp)
    cols = st.columns(len(policies))
    for col, p in zip(cols, policies):
        sub = agg[agg["policy"] == p].set_index("metric")
        with col.container(border=True, height="stretch"):
            policy_heading(p)
            if sub.empty:
                st.write("No usable runs.")
                continue
            lines = []
            for m, label in PLAIN_METRICS.items():
                row = sub.loc[m]
                spread = f' <span class="sx-note">± {fmt(m, row["std"])}</span>' if row["n"] > 1 and row["std"] == row["std"] else ""
                lines.append(f"<div class='sx-note'>{label}</div><div class='sx-value'>{fmt(m, row['mean'])}{spread}</div>")
            st.markdown("".join(lines), unsafe_allow_html=True)
    if n_runs > 1:
        st.caption("± shows how much the result varied between runs (standard deviation).")

    # ---- over time -----------------------------------------------------------------------
    st.subheader("What happened over time")
    ts = exp.timeseries[exp.timeseries["workload_type"] == workload]
    window = float(summary["decision_interval_s"].iloc[0])
    fig = plots.timeseries_panels(resample_timeseries(ts, window), workload, policies)
    st.pyplot(fig)
    plots.close(fig)
    if n_runs > 1:
        st.caption("Lines are averaged over all runs. The **Over time** page lets you look at a single run.")

    # ---- details -------------------------------------------------------------------------
    with st.expander("Full statistics and comparisons (for the detail-minded)"):
        _details(ctx)


def _details(ctx: Context) -> None:
    exp = ctx.exp
    agg = aggregate(exp.summary, list(METRICS))
    diffs = config_consistency(exp.configs)
    if diffs.empty:
        st.success("Fair comparison: every run used the same settings apart from its random seed, and within a run "
                   "all strategies got identical requests, servers, timing and prices.")
    else:
        st.error("Runs in this experiment used different settings, so they are not directly comparable.")
        st.dataframe(diffs, hide_index=True, width="stretch")

    metric = st.selectbox("Metric", KEY_METRICS + [m for m in METRICS if m not in KEY_METRICS],
                          format_func=lambda m: f"{METRICS[m].label} ({METRICS[m].unit}, {METRICS[m].better} is better)",
                          key="cmp_metric")
    m = METRICS[metric]
    st.caption(f"{'Calculated' if m.kind == 'CALCULATED' else 'Measured'}: {m.description}. "
               "Cells show mean ± standard deviation over runs; bars show 95% confidence intervals when there are 2+ runs.")
    table = comparison_table(agg, metric)
    table.index = [scenario_name(w) for w in table.index]
    table.columns = [policy_name(p) for p in table.columns]
    st.dataframe(table, width="stretch")
    if 1 < int(agg["n"].max()) < 5:
        st.caption("With few runs the 95% intervals are wide (2 runs use t = 12.71, 3 runs t = 4.30). They are shown "
                   "as computed, without clipping.")
    fig = plots.metric_bars(agg, metric)
    st.pyplot(fig)
    plots.close(fig)

    st.markdown("**Speed versus cost**")
    fig = plots.tradeoff_scatter(agg, "total_cost_usd", "p95_response_time_s")
    st.pyplot(fig, width="content")
    plots.close(fig)

    st.markdown("**Best strategy per traffic pattern and metric**")
    st.caption("'Clear' means the 95% confidence intervals don't overlap. With one run, no difference can be called real.")
    best = best_policies(agg)
    if not best.empty:
        view = best.assign(
            traffic=best["workload_type"].map(scenario_name),
            best_policy=best["best_policy"].map(lambda s: " = ".join(policy_name(p) for p in s.split(" = "))),
            runner_up=best["runner_up"].map(lambda s: policy_name(s) if s else ""))
        st.dataframe(view[["traffic", "metric_label", "best_policy", "best_mean", "runner_up", "runner_up_mean",
                           "evidence"]], hide_index=True, width="stretch")
        st.markdown("**How often each strategy came out on top**")
        st.dataframe(win_counts(best).rename(columns=policy_name), width="stretch")

    if "STATIC" in policies_of(exp):
        st.markdown("**Change compared with the fixed fleet**")
        rel = relative_to_baseline(agg, "STATIC")
        if not rel.empty:
            rel = rel.assign(metric=rel["metric"].map(lambda k: METRICS[k].label),
                             workload_type=rel["workload_type"].map(scenario_name),
                             policy=rel["policy"].map(policy_name))
            wide = rel.pivot_table(index=["workload_type", "policy"], columns="metric", values="pct_change")
            st.dataframe(wide.style.format("{:+.1f}%", na_rep="n/a"), width="stretch")
    if st.toggle("Show the raw statistics table", key="cmp_raw"):
        st.dataframe(agg, hide_index=True, width="stretch")
