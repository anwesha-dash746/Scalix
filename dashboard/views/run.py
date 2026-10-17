"""Run Experiments: parameter form -> validation -> Java engine -> loaded results."""

from __future__ import annotations

import streamlit as st

from scalix_dash import POLICIES, WORKLOADS
from scalix_dash.params import MAX_VMS_CAPACITY, ExperimentParams
from scalix_dash.runner import SUCCESS, SimulationRunner

from .common import SELECTED_KEY, Context

LAST_RESULT_KEY = "last_batch_result"


def render(ctx: Context) -> None:
    st.subheader("Run Experiments")
    st.caption("Each repetition runs the Java engine once with its own seed; within a repetition every selected "
               "policy receives the identical request sequence, VM type, timing and pricing.")
    runner = SimulationRunner(ctx.settings)
    problems = runner.preflight()
    if problems:
        for p in problems:
            st.error(p)

    d = ExperimentParams.from_dict(ctx.settings.defaults)
    with st.form("run_form"):
        c1, c2 = st.columns(2)
        with c1:
            workloads = st.multiselect("Workloads", list(WORKLOADS), default=[w for w in d.workloads if w in WORKLOADS], key="f_workloads")
            policies = st.multiselect("Scaling policies", list(POLICIES), default=[p for p in d.policies if p in POLICIES], key="f_policies")
            duration = st.number_input("Simulation duration (s)", 60, 86_400, int(d.duration_s), step=60,
                                       help="Arrival phase length. Burst and fluctuation timing scale with it.", key="f_duration")
            repetitions = st.number_input("Repetitions (seeds)", 1, 30, int(d.repetitions),
                                          help="Seeds base, base+1, ...; used to estimate variability.", key="f_reps")
            base_seed = st.number_input("Base seed", value=int(d.base_seed), step=1, key="f_seed")
        with c2:
            initial_vms = st.number_input("Initial VM count (all policies; STATIC keeps it)", 1, MAX_VMS_CAPACITY,
                                          int(d.initial_vms), key="f_initial")
            vm_min, vm_max = st.columns(2)
            min_vms = vm_min.number_input("Minimum VMs", 1, MAX_VMS_CAPACITY, int(d.min_vms), key="f_min")
            max_vms = vm_max.number_input("Maximum VMs", 1, MAX_VMS_CAPACITY, int(d.max_vms), key="f_max")
            t_low, t_up = st.columns(2)
            lower = t_low.number_input("Lower threshold", 0.0, 1.0, float(d.lower_threshold), step=0.05, format="%.2f", key="f_lower")
            upper = t_up.number_input("Upper threshold", 0.0, 1.0, float(d.upper_threshold), step=0.05, format="%.2f", key="f_upper")
            window = st.number_input("SCALIX trend observation window (decision intervals)", 2, 100,
                                     int(d.observation_window), key="f_window")
            cu, cd = st.columns(2)
            cool_up = cu.number_input("SCALIX scale-up cooldown (s)", 0, 86_400, int(d.scale_up_cooldown_s), key="f_cool_up")
            cool_down = cd.number_input("SCALIX scale-down cooldown (s)", 0, 86_400, int(d.scale_down_cooldown_s), key="f_cool_down")
            cool_thr = st.number_input("THRESHOLD cooldown (s, 0 = none)", 0, 86_400, int(d.threshold_cooldown_s), key="f_cool_thr")
        with st.expander("Advanced settings"):
            a1, a2, a3 = st.columns(3)
            trend = a1.number_input("Trend threshold (relative slope/interval)", 0.0, 10.0, float(d.trend_threshold),
                                    step=0.01, format="%.3f", key="f_trend")
            interval = a1.number_input("Decision interval (s)", 1, 3600, int(d.decision_interval_s), key="f_interval")
            boot = a2.number_input("VM boot time (s)", 0, 3600, int(d.vm_boot_s), key="f_boot")
            price = a2.number_input("Price per VM-hour (USD, assumed)", 0.0, 1000.0, float(d.price_per_vm_hour),
                                    step=0.01, format="%.4f", key="f_price")
            pre = a3.number_input("SCALIX preemptive threshold", 0.0, 1.0, float(d.scalix_preemptive_upper),
                                  step=0.05, format="%.2f", key="f_pre")
            target = a3.number_input("SCALIX target utilization", 0.0, 1.0, float(d.scalix_target_utilization),
                                     step=0.05, format="%.2f", key="f_target")
            drain = a1.number_input("Max drain time after arrivals stop (s)", 0, 86_400, int(d.max_drain_s), key="f_drain")
        submitted = st.form_submit_button("Run experiment", type="primary", disabled=bool(problems), key="f_submit")

    if submitted:
        params = ExperimentParams(
            workloads=list(workloads), policies=list(policies), duration_s=float(duration),
            initial_vms=int(initial_vms), min_vms=int(min_vms), max_vms=int(max_vms),
            upper_threshold=float(upper), lower_threshold=float(lower), observation_window=int(window),
            trend_threshold=float(trend), scale_up_cooldown_s=float(cool_up), scale_down_cooldown_s=float(cool_down),
            threshold_cooldown_s=float(cool_thr), repetitions=int(repetitions), base_seed=int(base_seed),
            decision_interval_s=float(interval), vm_boot_s=float(boot), price_per_vm_hour=float(price),
            max_drain_s=float(drain), scalix_preemptive_upper=float(pre), scalix_target_utilization=float(target))
        errors = params.validate()
        if errors:
            st.error("Please fix these parameters (nothing was run):\n\n" + "\n".join(f"- {e}" for e in errors))
        else:
            _execute(runner, params)

    last = st.session_state.get(LAST_RESULT_KEY)
    if last:
        _show_result(last)


def _execute(runner: SimulationRunner, params: ExperimentParams) -> None:
    runs = len(params.workloads) * len(params.policies)
    with st.status(f"Running {params.repetitions} repetition(s) x {runs} run(s)...", expanded=True) as status:
        bar = st.progress(0.0)

        def progress(done: int, total: int, msg: str) -> None:
            bar.progress(done / total)
            st.write(msg)

        result = runner.run_batch(params, progress=progress)
        if result.problems:
            status.update(label="Experiment not started", state="error")
        elif result.status == SUCCESS:
            status.update(label=f"Experiment {result.batch_id} completed", state="complete")
        else:
            status.update(label=f"Experiment {result.batch_id}: {result.status}", state="error")
    st.session_state[LAST_RESULT_KEY] = {
        "batch_id": result.batch_id, "status": result.status, "problems": result.problems,
        "dir": str(result.batch_dir),
        "reps": [{"rep": f"rep-{r.index:02d}", "seed": r.seed, "status": r.status, "message": r.message,
                  "duration_s": round(r.duration_s, 2), "log": r.log_file} for r in result.reps],
    }
    if any(r.ok for r in result.reps):
        st.session_state[SELECTED_KEY] = result.batch_id
        st.rerun()


def _show_result(last: dict) -> None:
    st.markdown("---")
    st.markdown(f"**Last submission:** `{last['batch_id']}` — status **{last['status']}**")
    for p in last["problems"]:
        st.error(p)
    if last["reps"]:
        st.dataframe(last["reps"], hide_index=True, width="stretch")
    if last["status"] == SUCCESS:
        st.success("Results loaded. The experiment is selected in the sidebar; see the other tabs.")
    elif last["reps"]:
        st.warning("Some or all repetitions failed. Only successful repetitions are used in the analysis; "
                   "see each repetition's log file for the engine output.")
