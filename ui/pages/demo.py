"""Demo: a guided replay of one real day of web traffic (NASA, July 1995) for every strategy."""

import pandas as pd
import streamlit as st

from scalix.simulation import Scenario, WorkloadSpec, generate, simulate
from scalix.simulation.experiment import default_policies
from scalix.sources import DataSource
from ui import charts, common, live_chart

common.page_header("Demo: real web traffic", "One real day from the NASA Kennedy Space Center web server (July 1995), "
                   "replayed for every strategy on the same clock. 24 hours play in 20 seconds per chart.")

DAYS = {13: "13 July: Space Shuttle launch, the busiest day", 6: "6 July: an ordinary busy weekday",
        9: "9 July: a quiet Sunday", 22: "22 July: a quiet Saturday"}
c = st.columns([3, 2])
with c[0]:
    day = st.selectbox("Day of July 1995", list(DAYS) + [d for d in range(1, 28) if d not in DAYS], key="demo_day",
                       format_func=lambda d: DAYS.get(d, f"{d} July"))


@st.cache_data(show_spinner=False)
def run_day(day: int):
    """Simulate every default strategy on one real day. Returns per-minute frames and the per-run summary."""
    trace = generate(WorkloadSpec(scenario=Scenario.NASA, duration_seconds=86_400.0, seed=day))
    per_min = []
    summaries = {}
    for policy in default_policies():
        result = simulate(policy, trace)
        summaries[policy.name] = result.summary
        ts = result.timeseries
        minute = (ts["time_s"] // 60).astype(int)
        agg = ts.groupby(minute).agg(provisioned=("provisioned", "mean"), queue=("queue", "mean"),
                                     arrivals=("arrivals", "sum"))
        agg["policy"] = policy.name
        agg["hour"] = agg.index / 60.0
        per_min.append(agg.reset_index(drop=True))
    return pd.concat(per_min, ignore_index=True), summaries


with st.spinner("Simulating 24 hours of real traffic for every strategy (about 10 seconds the first time)..."):
    frame, summaries = run_day(day)
common.show_source(DataSource.SIMULATED, "Real request counts from the 1995 log (×2.37 to fit a 20-worker fleet); "
                   "the servers and their response times are simulated")

# ----------------------------------------------------------------------- step 1: the traffic
st.subheader("1. The traffic")
st.markdown("These are **real requests**: every request NASA's web server logged that day, counted per 5 seconds. "
            "Watch for the morning ramp, the bursts, and the quiet night.")
demand = frame[frame["policy"] == "STATIC"]
live_chart.show(live_chart.ChartSpec(
    title=f"Requests per second, {DAYS.get(day, f'{day} July')} (per-minute average)", y_label="requests per second",
    series=(live_chart.Series("real demand (scaled)", list(demand["hour"]), list(demand["arrivals"] / 60.0),
                              charts.NEUTRAL_DARK),),
    x_label="hour of day (local time)", time_unit="h", y_format="1f", unit=" req/s"))

# ----------------------------------------------------------------------- step 2: the strategies
st.subheader("2. Four strategies, the same day")
st.markdown("**Static** never changes. **Threshold** adds one worker above 70% busy. **SCALIX** also watches where "
            "demand is heading and adds several at once. **AWS target tracking** is a model of AWS's own policy "
            "(keep CPU near 50%). Watch who reacts first when a burst arrives.")


def series(column: str, step: bool = False, policies=charts.POLICY_ORDER):
    out = []
    for p in policies:
        d = frame[frame["policy"] == p]
        out.append(live_chart.Series(charts.POLICY_SHORT[p], list(d["hour"]), list(d[column]), charts.POLICY_COLORS[p],
                                     step=step))
    return tuple(out)


live_chart.show(live_chart.ChartSpec(
    title="Workers paid for (per-minute average)", y_label="workers", series=series("provisioned"),
    x_label="hour of day (local time)", time_unit="h", y_format="1f", unit=" workers",
    y_max=max(1.0, float(frame["provisioned"].max())) * 1.15))

# ----------------------------------------------------------------------- step 3: who kept users waiting
st.subheader("3. Who kept users waiting?")
st.markdown("Requests that arrive when every worker is busy have to wait. A flat line at zero is what users want.")
live_chart.show(live_chart.ChartSpec(
    title="Requests waiting, all four strategies (per-minute average)", y_label="requests waiting", series=series("queue"),
    x_label="hour of day (local time)", time_unit="h", y_format="int", unit=" waiting",
    y_max=max(5.0, float(frame["queue"].max()) * 1.12)))
scalers = ("THRESHOLD", "SCALIX", "TARGET_TRACKING")
zoom = frame[frame["policy"].isin(scalers)]
st.markdown("The same chart **zoomed in on the three auto-scalers**, so their smaller queues are visible "
            "(Static is left out because its queue is far larger).")
live_chart.show(live_chart.ChartSpec(
    title="Requests waiting, auto-scalers only (per-minute average)", y_label="requests waiting",
    series=series("queue", policies=scalers), x_label="hour of day (local time)", time_unit="h", y_format="int",
    unit=" waiting", y_max=max(5.0, float(zoom["queue"].max()) * 1.12)))

# ----------------------------------------------------------------------- step 4: the scorecard
st.subheader("4. The scorecard for this day")
rows = []
for p in charts.POLICY_ORDER:
    s = summaries[p]
    rows.append({"Strategy": charts.POLICY_LABELS[p], "p95 response (s)": round(s["response_time_model_p95_s"], 2),
                 "Time with requests waiting (min)": round(s["time_with_queue_s"] / 60, 1),
                 "Worker-hours": round(s["worker_minutes"] / 60, 1),
                 "Estimated cost (USD)": round(s["estimated_cost"], 2),
                 "Scale-ups / downs": f"{s['scale_up_events']} / {s['scale_down_events']}"})
score = pd.DataFrame(rows)
st.dataframe(score, hide_index=True, width="stretch")

fastest = min(charts.POLICY_ORDER, key=lambda p: summaries[p]["response_time_model_p95_s"])
cheapest = min(charts.POLICY_ORDER, key=lambda p: summaries[p]["estimated_cost"])
x, t = summaries["SCALIX"], summaries["THRESHOLD"]
st.markdown(
    f"- **Fastest for users this day:** {charts.POLICY_SHORT[fastest]} "
    f"(p95 {summaries[fastest]['response_time_model_p95_s']:.2f} s).\n"
    f"- **Cheapest:** {charts.POLICY_SHORT[cheapest]} (USD {summaries[cheapest]['estimated_cost']:.2f}).\n"
    f"- **SCALIX vs Threshold:** p95 {x['response_time_model_p95_s']:.2f} s against "
    f"{t['response_time_model_p95_s']:.2f} s, for {x['estimated_cost'] - t['estimated_cost']:+.2f} USD.")
st.caption("One day is one example. The Results page has all 27 days with confidence intervals; costs are estimates "
           "from AWS list prices for t3.micro workers, not AWS bills.")
