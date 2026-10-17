"""Start page: what SCALIX is about, and a simple way to run a comparison."""

from __future__ import annotations

import streamlit as st

from scalix_dash import POLICIES, WORKLOADS
from scalix_dash.params import MAX_VMS_CAPACITY, ExperimentParams
from scalix_dash.runner import SUCCESS, SimulationRunner
from scalix_dash.story import POLICY_BLURBS, PRECISION, scenario_description, scenario_name

from .common import SELECTED_KEY, Context, go_to, policy_heading

LAST_RESULT_KEY = "last_batch_result"


def render(ctx: Context) -> None:
    st.markdown('<div class="sx-kicker">Cloud auto-scaling, simulated</div>', unsafe_allow_html=True)
    st.title("SCALIX")
    st.markdown("### Rent the right number of servers, at the right moment.")
    st.markdown(
        '<p class="sx-lede">Apps never get the same traffic all day. Rent too few servers and your users wait; '
        "rent too many and you pay for machines sitting idle. SCALIX is a smarter rule for deciding when to add "
        "or remove servers. Here you can put it head-to-head with two common approaches in a realistic cloud "
        "simulation, and see for yourself where it helps and where it doesn't.</p>", unsafe_allow_html=True)
    st.write("")

    cols = st.columns(3)
    for col, p in zip(cols, POLICIES):
        with col.container(border=True, height="stretch"):
            policy_heading(p)
            st.markdown(f'<span class="sx-note">{POLICY_BLURBS[p]}</span>', unsafe_allow_html=True)

    st.write("")
    st.subheader("Try it")
    runner = SimulationRunner(ctx.settings)
    problems = runner.preflight()
    for p in problems:
        st.error(p)

    d = ExperimentParams.from_dict(ctx.settings.defaults)
    with st.container(border=True):
        st.markdown("**1. Pick the kind of traffic to simulate**")
        workloads = st.pills("Traffic", list(WORKLOADS), selection_mode="multi",
                             default=[w for w in d.workloads if w in WORKLOADS], format_func=scenario_name,
                             key="f_workloads", label_visibility="collapsed")
        for w in workloads or []:
            st.markdown(f'<span class="sx-note"><b>{scenario_name(w)}:</b> {scenario_description(w)}</span>',
                        unsafe_allow_html=True)

        st.markdown("**2. How careful should the test be?**")
        default_precision = next((k for k, v in PRECISION.items() if v == d.repetitions), "Balanced")
        precision = st.segmented_control("Precision", list(PRECISION), default=default_precision, required=True,
                                         key="f_precision", label_visibility="collapsed")
        st.caption("Each extra run replays the test with fresh random traffic, so you can tell a real difference from "
                   "luck. Balanced (3 runs) takes about 10-20 seconds.")

        with st.expander("Fine-tune the simulation (optional)"):
            settings = _fine_tune(d)

        clicked = st.button("Run the comparison", type="primary", disabled=bool(problems), key="f_submit")

    if clicked:
        reps = settings.pop("custom_reps") or PRECISION.get(precision or "Balanced", 3)
        params = ExperimentParams(workloads=list(workloads or []), repetitions=int(reps), **settings)
        errors = params.validate()
        if errors:
            st.error("A few settings need fixing first (nothing was run):\n\n" + "\n".join(f"- {e}" for e in errors))
        else:
            _execute(runner, params)

    last = st.session_state.get(LAST_RESULT_KEY)
    if last and last["status"] != SUCCESS:
        _show_failure(last)


def _fine_tune(d: ExperimentParams) -> dict:
    """Every technical knob, grouped, with plain explanations. Returns ExperimentParams kwargs."""
    st.caption("The defaults are sensible starting points. Change them only if you want to explore.")
    policies = st.multiselect("Strategies to compare", list(POLICIES), default=list(d.policies), key="f_policies",
                              format_func=lambda p: {"STATIC": "Fixed fleet", "THRESHOLD": "Rule-based"}.get(p, p))
    c1, c2, c3 = st.columns(3)
    with c1:
        st.markdown("**Simulation**")
        duration = st.number_input("Length of the traffic (seconds)", 60, 86_400, int(d.duration_s), step=60, key="f_duration")
        custom = st.toggle("Choose the exact number of runs", key="f_custom_reps")
        reps = st.number_input("Number of runs", 1, 30, int(d.repetitions), key="f_reps", disabled=not custom)
        seed = st.number_input("Random seed", value=int(d.base_seed), step=1, key="f_seed",
                               help="Same seed = same traffic, so results can be reproduced exactly.")
    with c2:
        st.markdown("**Servers**")
        initial = st.number_input("Servers at the start (every strategy)", 1, MAX_VMS_CAPACITY, int(d.initial_vms),
                                  key="f_initial", help="The fixed fleet keeps this many the whole time.")
        min_vms = st.number_input("Never fewer than", 1, MAX_VMS_CAPACITY, int(d.min_vms), key="f_min")
        max_vms = st.number_input("Never more than", 1, MAX_VMS_CAPACITY, int(d.max_vms), key="f_max")
    with c3:
        st.markdown("**When to resize**")
        upper = st.number_input("Add servers above this busyness", 0.0, 1.0, float(d.upper_threshold), step=0.05,
                                format="%.2f", key="f_upper")
        lower = st.number_input("Remove servers below this busyness", 0.0, 1.0, float(d.lower_threshold), step=0.05,
                                format="%.2f", key="f_lower")
        thr_cool = st.number_input("Rule-based: pause between changes (s)", 0, 86_400, int(d.threshold_cooldown_s),
                                   key="f_cool_thr")
    st.markdown("**SCALIX specifics**")
    s1, s2, s3 = st.columns(3)
    window = s1.number_input("Look back this many decisions to spot a trend", 2, 100, int(d.observation_window),
                             key="f_window", help="Decisions happen every 15 s by default, so 6 = the last 90 s.")
    cool_up = s2.number_input("Wait after adding servers (s)", 0, 86_400, int(d.scale_up_cooldown_s), key="f_cool_up")
    cool_down = s3.number_input("Wait before removing servers (s)", 0, 86_400, int(d.scale_down_cooldown_s),
                                key="f_cool_down")
    trend, pre, target = d.trend_threshold, d.scalix_preemptive_upper, d.scalix_target_utilization
    interval, boot, price, drain = d.decision_interval_s, d.vm_boot_s, d.price_per_vm_hour, d.max_drain_s
    if st.toggle("Show expert settings", key="f_expert"):
        e1, e2, e3 = st.columns(3)
        trend = e1.number_input("Trend threshold (relative slope per decision)", 0.0, 10.0, float(trend),
                                step=0.01, format="%.3f", key="f_trend")
        pre = e1.number_input("SCALIX scales early above this busyness when demand rises", 0.0, 1.0, float(pre),
                              step=0.05, format="%.2f", key="f_pre")
        target = e1.number_input("SCALIX sizes the fleet for this busyness", 0.0, 1.0, float(target),
                                 step=0.05, format="%.2f", key="f_target")
        interval = e2.number_input("Seconds between decisions", 1, 3600, int(interval), key="f_interval")
        boot = e2.number_input("Seconds for a new server to start", 0, 3600, int(boot), key="f_boot")
        price = e3.number_input("Price per server-hour (USD, assumed)", 0.0, 1000.0, float(price),
                                step=0.01, format="%.4f", key="f_price")
        drain = e3.number_input("Max extra time to finish queued requests (s)", 0, 86_400, int(drain), key="f_drain")
    return dict(policies=list(policies), duration_s=float(duration), initial_vms=int(initial), min_vms=int(min_vms),
                max_vms=int(max_vms), upper_threshold=float(upper), lower_threshold=float(lower),
                observation_window=int(window), trend_threshold=float(trend), scale_up_cooldown_s=float(cool_up),
                scale_down_cooldown_s=float(cool_down), threshold_cooldown_s=float(thr_cool), base_seed=int(seed),
                decision_interval_s=float(interval), vm_boot_s=float(boot), price_per_vm_hour=float(price),
                max_drain_s=float(drain), scalix_preemptive_upper=float(pre), scalix_target_utilization=float(target),
                custom_reps=int(reps) if custom else None)


def _execute(runner: SimulationRunner, params: ExperimentParams) -> None:
    label = f"Simulating {len(params.workloads)} traffic pattern(s) × {len(params.policies)} strategies, " \
            f"{params.repetitions} run(s) each..."
    with st.status(label, expanded=True) as status:
        bar = st.progress(0.0)

        def progress(done: int, total: int, msg: str) -> None:
            bar.progress(done / total)
            st.write(msg)

        result = runner.run_batch(params, progress=progress)
        if result.problems:
            status.update(label="The comparison could not start", state="error")
        elif result.status == SUCCESS:
            status.update(label="Done", state="complete")
        else:
            status.update(label=f"Finished with problems: {result.status}", state="error")
    st.session_state[LAST_RESULT_KEY] = {
        "batch_id": result.batch_id, "status": result.status, "problems": result.problems,
        "reps": [{"run": f"rep-{r.index:02d}", "seed": r.seed, "status": r.status, "details": r.message,
                  "log file": r.log_file} for r in result.reps],
    }
    if any(r.ok for r in result.reps):
        st.session_state[SELECTED_KEY] = result.batch_id
        if result.status == SUCCESS:
            go_to("Results")
        st.rerun()


def _show_failure(last: dict) -> None:
    st.markdown(f"**Last attempt:** `{last['batch_id']}` finished with status **{last['status']}**.")
    for p in last["problems"]:
        st.error(p)
    if last["reps"]:
        st.dataframe(last["reps"], hide_index=True, width="stretch")
        st.caption("Failed runs are never shown as results. The log file of each run contains the simulator's output.")
