"""Workload simulation: choose a demand pattern and settings, run all strategies locally."""

import streamlit as st

from scalix.sources import DataSource
from ui import charts, common, live_chart

common.page_header("Workload simulation", "Choose a demand pattern and settings, then run all three strategies "
                   "on the identical workload. Runs on this computer in a second or two; nothing touches AWS.")

form_values = dict(st.session_state.get("form", common.DEFAULT_FORM))

with st.form("simulation_form"):
    st.markdown("**Workload**")
    scenarios = st.pills("Demand patterns", list(common.SCENARIO_LABELS), selection_mode="multi",
                         default=form_values["scenarios"], format_func=lambda s: common.SCENARIO_LABELS[s],
                         help=" · ".join(f"{common.SCENARIO_LABELS[k]}: {v}" for k, v in common.SCENARIO_HELP.items()))
    c = st.columns(4)
    duration = c[0].slider("Duration (minutes)", 5, 120, int(form_values["duration_min"]), step=5,
                           help="Length of each simulated run.")
    step = c[1].selectbox("Time step (seconds)", [1, 3, 5, 15], index=[1, 3, 5, 15].index(int(form_values["step_s"])),
                          help="Resolution of the simulation. Decisions are made every 15 s, so the step must divide 15.")
    rate = c[2].number_input("Base demand (requests/s)", 0.5, 50.0, float(form_values["base_rate"]), step=0.5,
                             help="Demand intensity. Every pattern is a multiple of this rate.")
    reps = c[3].number_input("Runs (seeds)", 1, 10, int(form_values["repetitions"]),
                             help="Each run uses a different random seed (base, base+1, ...). More runs give "
                                  "tighter confidence intervals.")
    seed = c[0].number_input("Base seed", 0, 10_000, int(form_values["seed"]),
                             help="Same seed and settings always give exactly the same results.")

    st.markdown("**Workers and cost**")
    c = st.columns(4)
    min_w = c[0].number_input("Minimum workers", 1, 40, int(form_values["min_workers"]))
    init_w = c[1].number_input("Starting workers", 1, 40, int(form_values["initial_workers"]),
                               help="All strategies start here. Static keeps exactly this many.")
    max_w = c[2].number_input("Maximum workers", 1, 40, int(form_values["max_workers"]))
    cap = c[3].number_input("Capacity per worker (req/s)", 0.1, 20.0, float(form_values["capacity_rps"]), step=0.1,
                            help="Requests one worker can serve per second.")
    delay = c[0].number_input("Start-up delay (s)", 0, 600, int(form_values["startup_delay_s"]), step=10,
                              help="New workers are paid for immediately but only serve after this delay.")
    price = c[1].number_input("Price per worker-hour", 0.0, 10.0, float(form_values["price"]),
                              step=0.001, format="%.4f",
                              help="Default: t3.micro + public IPv4 + 8 GB gp3 at AWS us-east-1 list prices "
                                   "(see scalix/pricing.py). An estimate, not an AWS bill.")
    credits = c[3].checkbox("Add T3 CPU credits", bool(form_values.get("cpu_credits", True)),
                            help="T3 instances charge extra when busy above their 10% baseline (Unlimited mode). "
                                 "Untick for a flat hourly price, like a non-burstable instance.")
    currency = c[2].text_input("Currency label", form_values["currency"], max_chars=8)

    with st.expander("Strategy settings (starting values, not proven optimums)"):
        c = st.columns(3)
        th_upper = c[0].slider("Scale-up threshold", 0.40, 0.95, float(form_values["th_upper"]), 0.05,
                               help="Threshold and SCALIX add capacity at this utilization.")
        th_lower = c[1].slider("Scale-down threshold", 0.05, 0.60, float(form_values["th_lower"]), 0.05)
        th_cd = c[2].number_input("Threshold cooldown (s)", 0, 600, int(form_values["th_cooldown"]), step=15)
        c = st.columns(3)
        sx_pre = c[0].slider("SCALIX early scale-up at", 0.30, 0.95, float(form_values["sx_preemptive"]), 0.05,
                             help="When demand is rising or spiking, SCALIX scales up from this utilization.")
        sx_target = c[1].slider("SCALIX target utilization", 0.35, 0.95, float(form_values["sx_target"]), 0.05,
                                help="SCALIX sizes each scale-up so the load runs at this utilization.")
        sx_window = c[2].number_input("Trend window (intervals)", 2, 20, int(form_values["sx_window"]))
        c = st.columns(3)
        sx_trend = c[0].number_input("Trend threshold (rel. slope)", 0.0, 0.5, float(form_values["sx_trend"]),
                                     step=0.01, format="%.2f")
        sx_up = c[1].number_input("SCALIX scale-up cooldown (s)", 0, 600, int(form_values["sx_up_cd"]), step=15)
        sx_down = c[2].number_input("SCALIX scale-down cooldown (s)", 0, 900, int(form_values["sx_down_cd"]), step=15)
        c = st.columns(3)
        sx_step = c[0].number_input("SCALIX max step", 1, 10, int(form_values["sx_step"]))
        sx_spike = c[1].number_input("Spike factor (≤1 turns it off)", 1.0, 5.0, float(form_values["sx_spike"]),
                                     step=0.1, format="%.1f")
        sx_low = c[2].number_input("Low intervals before scale-down", 1, 10, int(form_values["sx_low"]))

    submitted = st.form_submit_button("Run simulation", type="primary", icon=":material/play_arrow:")

if submitted:
    new_form = {
        "scenarios": scenarios or [], "duration_min": duration, "step_s": step, "base_rate": rate, "seed": seed,
        "repetitions": reps, "startup_delay_s": delay, "capacity_rps": cap, "min_workers": min_w,
        "initial_workers": init_w, "max_workers": max_w, "price": price, "cpu_credits": credits, "currency": currency.strip() or "USD",
        "th_upper": th_upper, "th_lower": th_lower, "th_cooldown": th_cd, "sx_preemptive": sx_pre,
        "sx_target": sx_target, "sx_window": sx_window, "sx_trend": sx_trend, "sx_up_cd": sx_up,
        "sx_down_cd": sx_down, "sx_step": sx_step, "sx_spike": sx_spike, "sx_low": sx_low,
    }
    try:
        with st.spinner("Simulating..."):
            common.run(new_form)
        st.session_state["is_default_run"] = False
        st.success("Done. The new run is now used on every page.")
    except ValueError as exc:
        st.error(f"Not run, because a setting is invalid: {exc}")

result = common.current_result()
st.divider()
common.show_source(DataSource.SIMULATED, "Demand is drawn at random around the pattern; same seed = same demand")
c = st.columns(2)
with c[0]:
    scenario = common.scenario_picker(result, key="sim_scenario")
with c[1]:
    seed_sel = common.seed_picker(result, scenario, key="sim_seed")

workload = result.workloads[(result.workloads["scenario"] == scenario) & (result.workloads["seed"] == seed_sel)]
ts = result.timeseries[(result.timeseries["scenario"] == scenario) & (result.timeseries["seed"] == seed_sel)]
threshold = float(st.session_state.get("form", common.DEFAULT_FORM)["th_upper"])
live_chart.show(charts.live_demand(workload))
live_chart.show(charts.live_utilization(ts, threshold))

with st.expander("Table view and downloads"):
    st.dataframe(result.summary[["scenario", "policy", "seed", "avg_utilization", "peak_utilization",
                                 "worker_minutes", "scale_up_events", "scale_down_events",
                                 "response_time_model_avg_s", "response_time_model_p95_s", "estimated_cost"]],
                 hide_index=True, width="stretch")
    st.download_button("Download per-run summary (CSV)", result.summary.to_csv(index=False),
                       "scalix-summary.csv", "text/csv")
    st.download_button("Download time series (CSV)", result.timeseries.to_csv(index=False),
                       "scalix-timeseries.csv", "text/csv")
