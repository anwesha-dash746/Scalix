"""Shared helpers for the dashboard pages: settings, labels, source badges, the current run."""

from __future__ import annotations

import html
from typing import Any

import pandas as pd
import streamlit as st

from scalix import pricing
from scalix.algorithms import CapacityLimits, ScalixPolicy, StaticPolicy, TargetTrackingPolicy, ThresholdPolicy
from scalix.config import ConfigError, Mode, ScalixConfig, load_config
from scalix.simulation import ExperimentSpec, SimulationConfig, WorkloadSpec, run_experiment
from scalix.sources import DataSource
from ui.charts import POLICY_LABELS, POLICY_ORDER

SCENARIO_LABELS = {
    "NORMAL": "Normal traffic",
    "RISING": "Gradually rising",
    "SPIKE": "Sudden spike, then decline",
    "MIXED": "Mixed (all patterns)",
}
SCENARIO_SHORT = {"NORMAL": "Normal", "RISING": "Rising", "SPIKE": "Spike", "MIXED": "Mixed"}
SCENARIO_HELP = {
    "NORMAL": "Moderate ups and downs around the base rate (±25%).",
    "RISING": "A slow straight-line rise from half to 2.5 times the base rate.",
    "SPIKE": "A sudden jump to 3x the base rate, a short hold, then a decline back.",
    "MIXED": "Normal, then rising, then a spike, a decline and a quiet period.",
}

_BADGE_STYLE = {
    DataSource.SIMULATED: ("#e8f1fb", "#1c5cab"),
    DataSource.MOCKED: ("#fdf0dc", "#8a5a00"),
    DataSource.LIVE_AWS: ("#e3f6ee", "#0f6b4a"),
}


def badge(source: DataSource) -> str:
    bg, fg = _BADGE_STYLE[source]
    return (f'<span style="background:{bg};color:{fg};border-radius:999px;padding:2px 10px;'
            f'font-size:0.78rem;font-weight:600;letter-spacing:0.02em;white-space:nowrap">'
            f'{html.escape(source.value)}</span>')


def show_source(source: DataSource, note: str = "") -> None:
    """A one-line label saying where the values below come from."""
    text = f"{badge(source)}&nbsp; <span style='color:#52514e;font-size:0.9rem'>{html.escape(source.label)}"
    if note:
        text += f". {html.escape(note)}"
    st.markdown(text + "</span>", unsafe_allow_html=True)


def page_header(title: str, subtitle: str) -> None:
    st.title(title)
    st.caption(subtitle)


def settings() -> tuple[ScalixConfig, str | None]:
    """Current config. If it is invalid, fall back to safe SIMULATION defaults and report why."""
    try:
        return load_config(), None
    except ConfigError as exc:
        return ScalixConfig(), str(exc)


def sidebar(config: ScalixConfig, config_error: str | None) -> None:
    with st.sidebar:
        st.markdown("### SCALIX")
        st.caption("Workload-aware auto-scaling: local dashboard")
        mode = config.mode
        if mode is Mode.SIMULATION:
            st.success("**Mode: SIMULATION.** Everything runs on this computer. AWS is never contacted.")
        elif mode is Mode.READ_ONLY:
            st.info("**Mode: READ_ONLY.** The AWS page can *read* your group when you press its button. "
                    "Nothing can be changed from here.")
        else:
            st.warning("**Mode: LIVE in config.** One capacity change at a time can be approved on the AWS "
                       "monitoring page (typed group name and a tick box) or from the command line.")
        if config_error:
            st.error(f"Config problem, using safe defaults: {config_error}")
        st.markdown("**Where numbers come from**")
        for src in DataSource:
            st.markdown(f"{badge(src)}&nbsp; <span style='font-size:0.85rem'>{html.escape(src.label)}</span>",
                        unsafe_allow_html=True)
        st.caption("Costs are estimates from AWS list prices (t3.micro, us-east-1), never AWS bills.")


# --------------------------------------------------------------------------- the current simulation run

DEFAULT_FORM: dict[str, Any] = {
    "scenarios": ["MIXED"], "duration_min": 30, "step_s": 5, "base_rate": 5.0, "seed": 42, "repetitions": 3,
    "startup_delay_s": 30, "capacity_rps": 1.0, "min_workers": 1, "initial_workers": 8, "max_workers": 20,
    "price": round(pricing.WORKER_HOURLY, 6), "cpu_credits": True, "currency": "USD",
    "th_upper": 0.70, "th_lower": 0.30, "th_cooldown": 60,
    "sx_preemptive": 0.55, "sx_target": 0.60, "sx_window": 6, "sx_trend": 0.03, "sx_up_cd": 30, "sx_down_cd": 90,
    "sx_step": 3, "sx_spike": 1.5, "sx_low": 2,
    "tt_target": 0.50, "tt_warmup": 300,
}


def build_spec(form: dict[str, Any]) -> ExperimentSpec:
    """Turn form values into an experiment. Raises ValueError with a readable message if invalid."""
    cap = CapacityLimits(int(form["min_workers"]), int(form["initial_workers"]), int(form["max_workers"]))
    spike = float(form["sx_spike"])
    policies = (
        StaticPolicy(worker_count=cap.initial),
        ThresholdPolicy(capacity=cap, upper=float(form["th_upper"]), lower=float(form["th_lower"]),
                        cooldown_seconds=float(form["th_cooldown"])),
        ScalixPolicy(capacity=cap, upper=float(form["th_upper"]), lower=float(form["th_lower"]),
                     preemptive_upper=float(form["sx_preemptive"]), target_utilization=float(form["sx_target"]),
                     observation_window=int(form["sx_window"]), trend_threshold=float(form["sx_trend"]),
                     scale_up_cooldown_seconds=float(form["sx_up_cd"]),
                     scale_down_cooldown_seconds=float(form["sx_down_cd"]),
                     max_scale_up_step=int(form["sx_step"]), spike_factor=None if spike <= 1 else spike,
                     low_intervals_required=int(form["sx_low"])),
        TargetTrackingPolicy(capacity=cap, target_utilization=float(form.get("tt_target", 0.50)),
                             warmup_seconds=float(form.get("tt_warmup", 300))),
    )
    if not form["scenarios"]:
        raise ValueError("choose at least one workload pattern")
    return ExperimentSpec(
        scenarios=tuple(form["scenarios"]), repetitions=int(form["repetitions"]), base_seed=int(form["seed"]),
        workload=WorkloadSpec(duration_seconds=float(form["duration_min"]) * 60, step_seconds=float(form["step_s"]),
                              base_rate=float(form["base_rate"])),
        simulation=SimulationConfig(startup_delay_seconds=float(form["startup_delay_s"]),
                                    worker_capacity_rps=float(form["capacity_rps"]),
                                    price_per_worker_hour=float(form["price"]), currency=str(form["currency"]),
                                    charge_cpu_credits=bool(form.get("cpu_credits", True))),
        policies=policies,
    )


def run(form: dict[str, Any]):
    spec = build_spec(form)
    result = run_experiment(spec)
    st.session_state["result"] = result
    st.session_state["form"] = dict(form)
    return result


def current_result():
    """The latest run; on first visit, a small default run so every page has something to show."""
    if "result" not in st.session_state:
        with st.spinner("Running the default simulation (Mixed pattern, 3 seeds)..."):
            run(DEFAULT_FORM)
        st.session_state["is_default_run"] = True
    return st.session_state["result"]


def scenario_picker(result, key: str) -> str:
    options = list(result.summary["scenario"].unique())
    return st.selectbox("Workload pattern", options, key=key, format_func=lambda s: SCENARIO_LABELS.get(s, s),
                        help="Patterns included in the latest run. Re-run on 'Workload simulation' for others.")


def seed_picker(result, scenario: str, key: str, label: str = "Seed (one run) for the time charts") -> int:
    seeds = sorted(result.summary.loc[result.summary["scenario"] == scenario, "seed"].unique())
    return int(st.selectbox(label, seeds, key=key,
                            help="Every strategy saw exactly the same demand for a given seed."))


def fmt_ci(row: pd.Series, fmt: str) -> str:
    mean = format(row["mean"], fmt)
    if row["n"] < 2 or pd.isna(row["ci95_high"]):
        return mean
    return f"{mean} ± {format(row['ci95_high'] - row['mean'], fmt)}"


def policy_label(p: str) -> str:
    return POLICY_LABELS.get(p, p)


__all__ = ["DEFAULT_FORM", "POLICY_ORDER", "SCENARIO_LABELS", "SCENARIO_HELP", "badge", "build_spec",
           "current_result", "fmt_ci", "page_header", "policy_label", "run", "scenario_picker", "seed_picker",
           "settings", "show_source", "sidebar"]
