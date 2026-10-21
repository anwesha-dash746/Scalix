"""Scaling history: every recorded decision with its reason, labelled simulated, mocked or live."""

from dataclasses import replace

import pandas as pd
import streamlit as st

import scalix.aws.audit as audit_module
from scalix.aws.audit import AuditError, AuditLog
from scalix.aws.controller import MOCK_SCENARIOS, Controller
from scalix.aws.monitoring import MockMonitor
from scalix.sources import DataSource
from ui import charts, common

common.page_header("Scaling history", "Every recorded scaling decision and why it was made. Simulated decisions "
                   "come from the latest run; controller decisions come from the audit logs on this computer.")

config, _ = common.settings()
tab_sim, tab_aws, tab_mock = st.tabs(["Simulated decisions", "Controller log: AWS", "Controller log: mocked"])


def audit_table(entries: list[dict]) -> pd.DataFrame:
    rows = []
    for e in entries:
        d = e.get("decision") or {}
        before, after = e.get("desired_before"), e.get("desired_after")
        rows.append({
            "time (UTC)": e.get("time_utc", ""),
            "data": e.get("data_source", ""),
            "outcome": e.get("outcome", ""),
            "workers": f"{before if before is not None else '–'} → {after if after is not None else '–'}",
            "CPU": f"{e['cpu_value']:.0%}" if isinstance(e.get("cpu_value"), (int, float)) else e.get("cpu_status", ""),
            "policy said": f"{d.get('action', '')} {d.get('delta', '')}".strip(),
            "actual AWS change?": "YES" if e.get("outcome") == "APPLIED" else "no",
            "approved via": e.get("approved_via") or ("command line" if e.get("outcome") in ("APPLYING", "APPLIED", "FAILED") else ""),
            "reason": e.get("reason", ""),
        })
    return pd.DataFrame(rows)


def show_log(path, source: DataSource, empty_note: str) -> None:
    try:
        entries = AuditLog(path).entries()
    except AuditError as exc:
        st.error(f"The audit log could not be read: {exc}. The controller refuses to change anything until "
                 "this is fixed.", icon=":material/error:")
        return
    common.show_source(source, f"File: {path.name}")
    if not entries:
        st.info(empty_note)
        return
    table = audit_table(entries[::-1])
    applied = int((table["actual AWS change?"] == "YES").sum())
    st.caption(f"{len(table)} entr{'y' if len(table) == 1 else 'ies'}, newest first. "
               f"Actual AWS changes recorded: {applied}.")
    st.dataframe(table, hide_index=True, width="stretch", height=360)


with tab_sim:
    result = common.current_result()
    common.show_source(DataSource.SIMULATED, "No real servers were changed")
    c = st.columns([2, 1, 3, 1])
    with c[0]:
        scenario = common.scenario_picker(result, key="hist_scenario")
    with c[1]:
        seed = common.seed_picker(result, scenario, key="hist_seed", label="Seed (one run)")
    policies = c[2].multiselect("Strategies", list(charts.POLICY_ORDER), default=list(charts.POLICY_ORDER),
                                format_func=charts.POLICY_SHORT.get, key="hist_policies")
    only = c[3].toggle("Changes", value=True, key="hist_only", help="Show only decisions that changed the worker count.")
    ev = result.events[(result.events["scenario"] == scenario) & (result.events["seed"] == seed)
                       & (result.events["policy"].isin(policies))]
    if only:
        ev = ev[ev["applied_delta"] != 0]
    ev = ev.sort_values(["time_s", "policy"], kind="stable")
    st.dataframe(pd.DataFrame({
        "minute": (ev["time_s"] / 60).round(2),
        "strategy": ev["policy"].map(charts.POLICY_SHORT),
        "action": ev["action"],
        "workers": ev["workers_before"].astype(str) + " → " + ev["workers_after"].astype(str),
        "reason": ev["reason"],
        "trend": ev["trend"],
        "spike": ev["spike"].map({True: "yes", False: ""}),
        "cooldown blocked": ev["cooldown_blocked"].map({True: "yes", False: ""}),
        "actual AWS change?": "no (simulated)",
    }), hide_index=True, width="stretch", height=360,
        column_config={"reason": st.column_config.TextColumn("reason", width="large")})

with tab_aws:
    show_log(audit_module.DEFAULT_AUDIT_PATH, DataSource.LIVE_AWS,
             "No controller runs against AWS have been recorded on this computer.")
    if config.mode.uses_aws and config.aws.auto_scaling_group:
        st.markdown("**Dry-run decision with live data.** Reads the group and CPU (read-only), runs the policy "
                    "and every safety check, and records the result. It cannot apply anything.")
        if st.button("Run one dry-run decision", icon=":material/science:", key="hist_live_dry"):
            try:
                from scalix.aws.monitoring import CloudWatchMonitor

                ctl = Controller(config, CloudWatchMonitor.from_config(config),
                                 audit=AuditLog(audit_module.DEFAULT_AUDIT_PATH))
                st.code(ctl.tick().render(), language=None)
            except Exception as exc:
                st.error(f"Dry run could not start: {exc}")
    else:
        st.caption("Live dry runs need READ_ONLY mode and a configured group (see docs/CONTROLLER.md).")

with tab_mock:
    st.markdown("**Dry-run decision with mocked data.** Shows exactly what the controller would check and propose. "
                "Mocked data can never cause a real change.")
    c = st.columns([2, 1])
    scenario = c[0].selectbox("Mocked situation", sorted(MOCK_SCENARIOS), key="hist_mock_case")
    if c[1].button("Run one mocked dry run", icon=":material/science:", key="hist_mock_dry"):
        monitor = MockMonitor(name="mock-scalix-workers", **MOCK_SCENARIOS[scenario])
        demo = replace(config, live=replace(config.live, allowed_groups=(monitor.name,)))
        ctl = Controller(demo, monitor, audit=AuditLog(audit_module.MOCK_AUDIT_PATH))
        st.code(ctl.tick().render(), language=None)
    show_log(audit_module.MOCK_AUDIT_PATH, DataSource.MOCKED, "No mocked controller runs recorded yet.")
