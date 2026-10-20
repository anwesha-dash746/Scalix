"""Overview: the headline numbers for one strategy in the latest simulated run."""

import streamlit as st

from scalix.sources import DataSource
from ui import charts, common

common.page_header("Overview", "How one strategy did in the latest simulated run. "
                   "Change the run on 'Workload simulation'; compare strategies on 'Policy comparison'.")

result = common.current_result()
if st.session_state.get("is_default_run"):
    st.caption("Showing the default demo run: Mixed pattern, 3 seeds, default settings.")

left, right = st.columns([3, 2])
with left:
    policy = st.segmented_control(
        "Strategy", charts.POLICY_ORDER, default="SCALIX", format_func=charts.POLICY_SHORT.get, key="ov_policy",
        help="Static keeps a fixed number of workers. Threshold adds/removes one worker at 70%/30% "
             "utilization. SCALIX also looks at the demand trend and spikes.") or "SCALIX"
with right:
    scenario = common.scenario_picker(result, key="ov_scenario")

common.show_source(DataSource.SIMULATED, "Averages over all seeds in the run")
summary = result.summary[(result.summary["scenario"] == scenario) & (result.summary["policy"] == policy)]
ts = result.timeseries[(result.timeseries["scenario"] == scenario) & (result.timeseries["policy"] == policy)]
first_seed = int(summary["seed"].min())
ts_one = ts[ts["seed"] == first_seed]
m = summary.mean(numeric_only=True)
currency = summary["cost_currency"].iloc[0]

row1 = st.columns(4)
row1[0].metric("Workers at the end", int(ts_one["provisioned"].iloc[-1]),
               help=f"Workers paid for (serving + starting up) at the end of the run with seed {first_seed}.")
row1[1].metric("Workload pattern", common.SCENARIO_SHORT[scenario], help=common.SCENARIO_HELP[scenario])
row1[2].metric("Average utilization", f"{m['avg_utilization']:.0%}",
               help="Estimated share of the serving workers' capacity that was used, averaged over the run.")
row1[3].metric("Peak utilization", f"{m['peak_utilization']:.0%}",
               help="Highest estimated utilization in any 5-second step. 100% means requests were queueing.")

row2 = st.columns(4)
row2[0].metric("Scaling events", f"{m['scale_up_events'] + m['scale_down_events']:.1f}",
               help=f"Average per run: {m['scale_up_events']:.1f} scale-ups and {m['scale_down_events']:.1f} "
                    "scale-downs.")
row2[1].metric("Worker-minutes", f"{m['worker_minutes']:.0f}",
               help="Total time all workers existed (including start-up), summed. This is what you pay for.")
row2[2].metric(f"Estimated cost ({currency})", f"{m['illustrative_cost']:.3f}",
               help=f"Illustrative only: billed worker-time x {summary['price_per_worker_hour'].iloc[0]} "
                    f"{currency} per worker-hour (assumed). Not an AWS charge.")
row2[3].metric("Avg response (proxy)", f"{m['response_time_proxy_avg_s']:.1f} s",
               help="Simulated proxy: 2 s service time + average queueing delay (Little's law). "
                    "Not a measured latency.")

st.plotly_chart(charts.workers_chart(ts_one, policies=(policy,),
                                     title=f"Workers paid for: {common.policy_label(policy)} (seed {first_seed})"),
                width="stretch", theme=None)
with st.expander("What these numbers mean"):
    st.markdown(
        "- All values come from the **local simulator**, a documented model (see `docs/SIMULATION.md`), "
        "not from a real cloud.\n"
        "- **Utilization** is estimated as served work ÷ capacity. **Response time** is a proxy formula.\n"
        "- **Cost** = billed worker-time × an assumed price per worker-hour. It is for comparing "
        "strategies, not a bill.\n"
        "- Every strategy receives the **identical** demand for each seed, so differences come from the "
        "strategy alone.")
