"""Shared dashboard state: experiment selection, cached loading, small UI helpers."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import streamlit as st

from scalix_dash import POLICIES, WORKLOADS, catalog
from scalix_dash.analysis import valid_runs
from scalix_dash.catalog import CatalogEntry, LoadedExperiment
from scalix_dash.plots import POLICY_COLORS
from scalix_dash.settings import Settings
from scalix_dash.story import policy_name, scenario_name

SELECTED_KEY = "selected_experiment"
NAV_KEY = "nav"
PENDING_NAV_KEY = "pending_nav"


def go_to(page: str) -> None:
    """Switch section on the next rerun."""
    st.session_state[PENDING_NAV_KEY] = page
    st.rerun()


@dataclass
class Context:
    settings: Settings
    entries: list[CatalogEntry]
    entry: CatalogEntry | None
    exp: LoadedExperiment | None


def friendly_label(e: CatalogEntry) -> str:
    when = _when(e.created)
    if e.kind == "dashboard" and e.params:
        names = [scenario_name(w) for w in e.params.get("workloads", [])]
        scen = ", ".join(names[:2]) + (f" +{len(names) - 2}" if len(names) > 2 else "")
        reps = e.params.get("repetitions", 1)
        flag = "" if e.status == "SUCCESS" else f"  ({e.status.lower()})"
        return f"{scen} · {reps} run{'s' if reps != 1 else ''} · {when}{flag}"
    return f"{e.id} · command-line run · {when}"


def _when(iso: str) -> str:
    try:
        return datetime.fromisoformat(iso).strftime("%d %b, %H:%M")
    except (TypeError, ValueError):
        return iso or "?"


def experiment_selector(entries: list[CatalogEntry], settings: Settings) -> CatalogEntry | None:
    with st.sidebar:
        st.markdown("### Your experiments")
        if not entries:
            st.info("Nothing here yet. Run your first comparison from the **Start** page.")
            return None
        ids = [e.id for e in entries]
        current = st.session_state.get(SELECTED_KEY)
        if current not in ids:
            current = ids[0]
        by_id = {e.id: e for e in entries}
        choice = st.radio("Experiments", ids, index=ids.index(current), format_func=lambda i: friendly_label(by_id[i]),
                          label_visibility="collapsed")
        st.session_state[SELECTED_KEY] = choice
        entry = by_id[choice]
        bad = {r: s for r, s in entry.rep_status.items() if s != "SUCCESS"}
        if bad:
            st.warning("Some runs failed and are left out: " + ", ".join(f"{r} ({s.lower()})" for r, s in bad.items()))
        st.divider()
        if st.button("New comparison", width="stretch"):
            go_to("Start")
        st.caption("Every number in this app comes from the CloudSim Plus simulation. Nothing is made up or estimated "
                   "by the dashboard itself.")
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
        st.sidebar.error("This experiment has no successful runs to show.")
        return None
    exp = _load_cached(entry, _fingerprint(entry))
    if exp.skipped:
        st.sidebar.warning("Left out: " + "; ".join(exp.skipped))
    if exp.summary.empty:
        st.sidebar.error("No valid results could be loaded for this experiment.")
        return None
    return exp


def require_experiment(ctx: Context) -> bool:
    if ctx.exp is None:
        st.info("There are no results to show yet. Head to **Start** and run a comparison. It only takes a few seconds.")
        return False
    return True


def excluded_note(summary) -> None:
    _, excluded = valid_runs(summary)
    if not excluded.empty:
        st.warning(f"{len(excluded)} run(s) couldn't finish all their requests within the time limit, so they're left "
                   "out of the averages.")


def ordered(values, reference) -> list[str]:
    present = list(dict.fromkeys(values))
    return [v for v in reference if v in present] + [v for v in present if v not in reference]


def workloads_of(exp: LoadedExperiment) -> list[str]:
    return ordered(exp.summary["workload_type"], WORKLOADS)


def policies_of(exp: LoadedExperiment) -> list[str]:
    return ordered(exp.summary["policy"], POLICIES)


def dot(policy: str) -> str:
    return f'<span class="sx-dot" style="background:{POLICY_COLORS.get(policy, "#4b5563")}"></span>'


def policy_heading(policy: str) -> None:
    st.markdown(f'<div class="sx-big">{dot(policy)}{policy_name(policy)}</div>', unsafe_allow_html=True)


def context_panel(exp: LoadedExperiment, workload: str | None = None) -> None:
    """The conditions behind the numbers: scenario, settings, pricing, seeds."""
    s = exp.summary if workload is None else exp.summary[exp.summary["workload_type"] == workload]
    if s.empty:
        st.write("No runs.")
        return
    first = s.iloc[0]
    st.markdown(
        f"- **Simulated time:** {first['workload_duration_s']:.0f} s of traffic; decisions every "
        f"{first['decision_interval_s']:.0f} s; a new server takes {first['vm_boot_s']:.0f} s to start\n"
        f"- **Server type:** {int(first['vm_pes'])} CPU cores × {first['vm_mips_per_pe']:.0f} MIPS; "
        f"a typical request takes about {first['request_mean_length_mi'] / first['vm_mips_per_pe']:.0f} s of CPU\n"
        f"- **Pricing (assumed, not a real price list):** `{first['pricing_model']}`\n"
        f"- **Random seeds:** {', '.join(str(sd) for sd in sorted(set(s['seed'])))}")
    wl = exp.workloads.drop_duplicates(["workload_type"])
    if workload:
        wl = wl[wl["workload_type"] == workload]
    st.dataframe(wl[["workload_type", "arrival_pattern", "rate_params", "duration_s"]], hide_index=True, width="stretch")
    st.dataframe(s.drop_duplicates(["policy"])[["policy", "policy_params"]], hide_index=True, width="stretch")
