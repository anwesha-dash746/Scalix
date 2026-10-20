"""Policy comparison: the three strategies on identical workloads."""

import pandas as pd
import streamlit as st

from scalix.sources import DataSource
from ui import charts, common

common.page_header("Policy comparison", "Static, Threshold and SCALIX on the identical workload. No strategy is "
                   "assumed to win: each metric is judged on its own, with 95% confidence intervals.")

result = common.current_result()
c = st.columns(2)
with c[0]:
    scenario = common.scenario_picker(result, key="cmp_scenario")
with c[1]:
    seed = common.seed_picker(result, scenario, key="cmp_seed")
common.show_source(DataSource.SIMULATED)

ts = result.timeseries[(result.timeseries["scenario"] == scenario) & (result.timeseries["seed"] == seed)]
agg = result.aggregate[result.aggregate["scenario"] == scenario]
n_runs = int(agg["n"].max())

st.plotly_chart(charts.workers_chart(ts, title=f"Workers paid for over time (seed {seed})"), width="stretch", theme=None)
st.plotly_chart(charts.queue_chart(ts), width="stretch", theme=None)

# ----------------------------------------------------------------------- comparison table
METRICS = [
    ("worker_minutes", "Worker-minutes (resource-time)", ".0f", "lower"),
    ("illustrative_cost", "Estimated cost (illustrative)", ".3f", "lower"),
    ("avg_utilization", "Average utilization", ".1%", "higher"),
    ("time_above_threshold_s", "Time above threshold (s)", ".0f", "lower"),
    ("time_with_queue_s", "Time overloaded, requests waiting (s)", ".0f", "lower"),
    ("unserved_at_end", "Requests still waiting at the end", ".0f", "lower"),
    ("response_time_proxy_avg_s", "Avg response, proxy (s)", ".2f", "lower"),
    ("response_time_proxy_p95_s", "p95 response, proxy (s)", ".2f", "lower"),
    ("scale_up_events", "Scale-ups", ".1f", None),
    ("scale_down_events", "Scale-downs", ".1f", None),
]


def verdict(metric: str, direction: str | None) -> str:
    rows = agg[agg["metric"] == metric].set_index("policy")
    if rows.empty:
        return ""
    if direction is None:
        return "context only (fewer is not automatically better)"
    best = rows["mean"].idxmin() if direction == "lower" else rows["mean"].idxmax()
    others = rows.drop(index=best)
    if (rows["mean"] == rows.loc[best, "mean"]).sum() > 1:
        return "tie"
    if n_runs < 2:
        return f"{charts.POLICY_SHORT[best]} (single run, no CI)"
    b = rows.loc[best]
    clear = all((b["ci95_high"] < o["ci95_low"]) if direction == "lower" else (b["ci95_low"] > o["ci95_high"])
                for _, o in others.iterrows())
    return f"{charts.POLICY_SHORT[best]}: " + ("clear lead" if clear else "too close to call")


table = []
for metric, label, fmt, direction in METRICS:
    rows = agg[agg["metric"] == metric].set_index("policy")
    entry = {"Metric": label, "Better": direction or "-"}
    for p in charts.POLICY_ORDER:
        entry[common.policy_label(p)] = common.fmt_ci(rows.loc[p], fmt) if p in rows.index else "-"
    entry["Best (95% CI)"] = verdict(metric, direction)
    table.append(entry)

st.subheader("Side by side")
st.caption(f"Mean over {n_runs} run(s)" + (" ± half-width of the 95% confidence interval." if n_runs > 1 else "."))
st.dataframe(pd.DataFrame(table), hide_index=True, width="stretch")
st.caption("'Clear lead' means the 95% intervals do not overlap; otherwise the difference may be noise. "
           "Read the metrics together: high utilization is only good if requests are not waiting, and the "
           "cheapest strategy may be the one that lets demand queue. Costs use an assumed price and are not "
           "AWS charges.")

choice = st.selectbox("Chart a metric", [m[0] for m in METRICS], format_func=dict((m[0], m[1]) for m in METRICS).get,
                      key="cmp_metric")
label, fmt = next((m[1], m[2]) for m in METRICS if m[0] == choice)
st.plotly_chart(charts.metric_bars(agg, choice, label, fmt), width="stretch", theme=None)

# ----------------------------------------------------------------------- decision log
st.subheader("Scaling actions and reasons")
ev = result.events[(result.events["scenario"] == scenario) & (result.events["seed"] == seed)]
c = st.columns([2, 1])
show = c[0].multiselect("Strategies", list(charts.POLICY_ORDER), default=["THRESHOLD", "SCALIX"],
                        format_func=charts.POLICY_SHORT.get, key="cmp_log_policies")
only_actions = c[1].toggle("Only changes", value=True, key="cmp_only_actions",
                           help="Hide decisions where nothing changed.")
ev = ev[ev["policy"].isin(show)]
if only_actions:
    ev = ev[ev["applied_delta"] != 0]
ev = ev.sort_values(["time_s", "policy"], kind="stable")
log = pd.DataFrame({
    "minute": (ev["time_s"] / 60).round(2),
    "strategy": ev["policy"].map(common.policy_label),
    "action": ev["action"],
    "workers": ev["workers_before"].astype(str) + " → " + ev["workers_after"].astype(str),
    "utilization": ev["utilization"].map("{:.0%}".format),
    "demand (req/s)": ev["arrival_rate_rps"].round(1),
    "reason": ev["reason"],
})
st.dataframe(log, hide_index=True, width="stretch", height=320,
             column_config={"reason": st.column_config.TextColumn("reason", width="large")})
st.caption(f"{len(log)} decision(s) shown. All are simulated decisions; no real servers were changed.")
