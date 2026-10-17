"""Shared dashboard state: experiment selection, cached loading and context panels."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd
import streamlit as st

from scalix_dash import POLICIES, WORKLOADS, catalog
from scalix_dash.analysis import valid_runs
from scalix_dash.catalog import CatalogEntry, LoadedExperiment
from scalix_dash.settings import Settings

SELECTED_KEY = "selected_experiment"


@dataclass
class Context:
    settings: Settings
    entries: list[CatalogEntry]
    entry: CatalogEntry | None
    exp: LoadedExperiment | None


def experiment_selector(entries: list[CatalogEntry], settings: Settings) -> CatalogEntry | None:
    with st.sidebar:
        st.header("Experiment")
        if st.button("Refresh list", width="stretch"):
            st.rerun()
        if not entries:
            st.info(f"No experiments found in `{settings.results_dir}`. Run one in the **Run Experiments** tab.")
            return None
        ids = [e.id for e in entries]
        current = st.session_state.get(SELECTED_KEY)
        index = ids.index(current) if current in ids else 0
        choice = st.selectbox("Select experiment", ids, index=index,
                              format_func=lambda i: next(e.label for e in entries if e.id == i))
        st.session_state[SELECTED_KEY] = choice
        entry = next(e for e in entries if e.id == choice)
        st.caption(f"Source: `{entry.path}`")
        st.caption(f"Created: {entry.created} · Kind: {entry.kind} · Status: {entry.status}")
        if entry.rep_status:
            bad = {r: s for r, s in entry.rep_status.items() if s != "SUCCESS"}
            if bad:
                st.warning("Repetitions without valid results (excluded): "
                           + ", ".join(f"{r} ({s})" for r, s in bad.items()))
        return entry


def _fingerprint(entry: CatalogEntry) -> str:
    parts = [str(entry.path), entry.status]
    for d in entry.rep_dirs:
        f = d / "summary.csv"
        parts.append(f"{d.name}:{f.stat().st_mtime_ns if f.exists() else 0}")
    return "|".join(parts)


@st.cache_data(show_spinner="Loading results...", max_entries=8)
def _load_cached(_entry: CatalogEntry, fingerprint: str) -> LoadedExperiment:
    return catalog.load(_entry)


def load_selected(entry: CatalogEntry | None) -> LoadedExperiment | None:
    if entry is None:
        return None
    if not entry.rep_dirs:
        st.sidebar.error("This experiment has no successful repetitions to display.")
        return None
    exp = _load_cached(entry, _fingerprint(entry))
    if exp.skipped:
        st.sidebar.warning("Skipped: " + "; ".join(exp.skipped))
    if exp.summary.empty:
        st.sidebar.error("No valid results could be loaded for this experiment.")
        return None
    with st.sidebar:
        _, excluded = valid_runs(exp.summary)
        st.metric("Valid repetitions", exp.n_reps)
        st.caption(f"Runs loaded: {len(exp.summary)} · excluded from statistics (drain timeout): {len(excluded)}")
    return exp


def require_experiment(ctx: Context) -> bool:
    if ctx.exp is None:
        st.info("Select an experiment in the sidebar, or run one in the **Run Experiments** tab.")
        return False
    return True


def ordered(values, reference) -> list[str]:
    present = list(dict.fromkeys(values))
    return [v for v in reference if v in present] + [v for v in present if v not in reference]


def workloads_of(exp: LoadedExperiment) -> list[str]:
    return ordered(exp.summary["workload_type"], WORKLOADS)


def policies_of(exp: LoadedExperiment) -> list[str]:
    return ordered(exp.summary["policy"], POLICIES)


def context_panel(exp: LoadedExperiment, workload: str | None = None) -> None:
    """Shows the conditions behind the numbers: workload, parameters, pricing, seeds."""
    with st.expander("Experiment conditions (workload, parameters, pricing, seeds)", expanded=False):
        s = exp.summary if workload is None else exp.summary[exp.summary["workload_type"] == workload]
        if s.empty:
            st.write("No runs.")
            return
        first = s.iloc[0]
        c1, c2 = st.columns(2)
        with c1:
            st.markdown(f"**Pricing model (assumed):** `{first['pricing_model']}`")
            st.markdown(f"**Duration:** {first['workload_duration_s']:.0f} s · **decision interval:** "
                        f"{first['decision_interval_s']:.0f} s · **VM boot:** {first['vm_boot_s']:.0f} s")
            st.markdown(f"**VM type:** {int(first['vm_pes'])} PEs × {first['vm_mips_per_pe']:.0f} MIPS · "
                        f"**mean request:** {first['request_mean_length_mi']:.0f} MI")
            st.markdown(f"**Repetitions / seeds:** {', '.join(f'{r}={sd}' for r, sd in sorted(set(zip(s['rep'], s['seed']))))}")
        with c2:
            wl = exp.workloads.drop_duplicates(["workload_type"])
            if workload:
                wl = wl[wl["workload_type"] == workload]
            st.dataframe(wl[["workload_type", "arrival_pattern", "rate_params", "duration_s"]],
                         hide_index=True, width="stretch")
        params = s.drop_duplicates(["policy"])[["policy", "policy_params"]]
        st.dataframe(params, hide_index=True, width="stretch")


def fmt_value(v: float, unit: str) -> str:
    if v is None or pd.isna(v):
        return "n/a"
    if unit == "fraction":
        return f"{v:.3f}"
    if unit == "USD":
        return f"${v:.4f}"
    if unit in ("count", "VMs"):
        return f"{v:.2f}" if v % 1 else f"{v:.0f}"
    return f"{v:.2f} {unit}"
