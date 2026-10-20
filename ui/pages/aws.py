"""AWS monitoring: read-only view of one Auto Scaling group, only when you press a button."""

from datetime import datetime, timezone

import pandas as pd
import streamlit as st

from scalix.aws.monitoring import MetricStatus, MockMonitor, MonitoringSnapshot
from scalix.config import ConfigError
from scalix.sources import DataSource
from ui import common

MOCK_CASES = {
    "busy": dict(cpu_percents=[85.0], desired=1, in_service=1),
    "normal": dict(cpu_percents=[45.0], desired=1, in_service=1),
    "quiet": dict(cpu_percents=[10.0], desired=2, in_service=2),
    "no data yet": dict(cpu_percents=[None], desired=1, in_service=1),
    "stale data": dict(cpu_percents=[60.0], desired=1, in_service=1, reading_age_seconds=2400),
    "broken data": dict(cpu_percents=[float("nan")], desired=1, in_service=1),
}

common.page_header("AWS monitoring", "A read-only view of one EC2 Auto Scaling group: its size and its average "
                   "CPU from CloudWatch. AWS is contacted only when you press a button below.")
st.info("This page **only reads**. The dashboard has no control that changes AWS. Capacity changes are only "
        "possible from the command line, with explicit confirmation (see docs/CONTROLLER.md).",
        icon=":material/visibility:")

config, _ = common.settings()

budget = config.aws.budget_usd
if budget:
    st.warning(f"**Strict AWS budget: USD {budget:g} only.** Reading AWS from this page costs effectively nothing. "
               "Anything that costs money (creating servers, a stack or a load test) needs your separate approval, "
               f"an itemised cost estimate and a deletion plan, and everything together must stay under USD {budget:g}.",
               icon=":material/savings:")
else:
    st.warning("**No AWS budget is set.** Set `aws.budget_usd` in the config before planning anything that costs "
               "money.", icon=":material/savings:")


def last_check_summary() -> None:
    """The newest access check saved by the command line (a local file; no AWS call)."""
    from scalix.aws.check_access import latest_saved_report

    found = latest_saved_report()
    if found is None:
        st.caption("No saved access check yet. Run `python -m scalix.aws.check_access` once your profile is set up.")
        return
    path, report = found
    checks = report.get("checks", [])
    by_name = {c.get("check"): c for c in checks if isinstance(c, dict)}
    identity = by_name.get("Caller identity", {})
    connected = identity.get("status") == "OK"
    denied = [c.get("check") for c in checks if isinstance(c, dict) and c.get("status") == "DENIED"]
    try:
        when = datetime.fromisoformat(str(report.get("generated_utc"))).astimezone().strftime("%d %b %Y, %H:%M (local time)")
    except (TypeError, ValueError):
        when = "an unknown time"
    profile = (report.get("environment") or {}).get("selected_profile") or "?"
    if connected:
        st.success(f"**Last access check: connected (read-only).** On {when}, profile **{profile}** signed in "
                   f"successfully: {identity.get('detail', '')}. Lab credentials may have expired since; run the "
                   "check again to be sure.", icon=":material/link:")
    else:
        st.error(f"**Last access check: not connected.** On {when}, the check could not sign in. Start the lab, "
                 "paste fresh credentials into the profile, then run the check again.", icon=":material/link_off:")
    st.dataframe(pd.DataFrame([{"check": c.get("check"), "result": c.get("status"), "detail": c.get("detail")}
                               for c in checks if isinstance(c, dict)]), hide_index=True, width="stretch")
    st.caption(f"From `{path.name}` (saved on this computer, git-ignored). "
               + (f"Denied: {', '.join(denied)}. " if denied else "")
               + "This check only reads; it created, changed and deleted nothing. AWS Academy lab credentials expire "
                 "when the lab session ends: start the lab again and paste the fresh block into the same profile.")


def render_snapshot(snap: MonitoringSnapshot) -> None:
    when = snap.fetched_at.astimezone(timezone.utc).strftime("%H:%M:%S UTC")
    common.show_source(snap.source, f"Read at {when}")
    g, cpu = snap.group, snap.cpu
    c = st.columns(4)
    if g is not None:
        c[0].metric("Desired workers", g.desired_capacity, help="What the Auto Scaling group is asked to run.")
        c[1].metric("In service", g.in_service, help="Instances running and healthy enough to serve.")
        c[2].metric("Starting up", g.pending, help="Instances launched but not in service yet.")
        c[3].metric("Allowed range", f"{g.min_size}–{g.max_size}", help="The group's own min and max size.")
    else:
        c[0].metric("Desired workers", "–")
    c = st.columns([1, 3])
    c[0].metric("Average CPU", f"{cpu.value:.0%}" if cpu.value is not None else "–",
                help="Average CPUUtilization of the group's instances for the latest 5-minute period.")
    with c[1]:
        message = f"**CPU reading {cpu.status.value}:** {cpu.reason}"
        if cpu.status is MetricStatus.OK:
            st.success(message, icon=":material/check_circle:")
        elif cpu.status in (MetricStatus.STALE, MetricStatus.MISSING):
            st.warning(message + " A decision would HOLD, never treat this as 0%.", icon=":material/schedule:")
        else:
            st.error(message + " A decision would HOLD.", icon=":material/error:")
    for err in snap.errors:
        st.error(f"AWS said: {err}", icon=":material/block:")
    st.caption(f"Demand (requests/s): not available. {snap.demand_note}.")
    if snap.decision_ready:
        st.caption("This data is fit for a decision (fresh CPU and a readable group).")


tab_live, tab_mock = st.tabs(["Live AWS (read-only)", "Mocked preview"])

with tab_live:
    if not config.mode.uses_aws:
        st.markdown("**AWS reading is switched off right now.** The mode is SIMULATION, so nothing on this page "
                    "contacts AWS.")
        last_check_summary()
        st.markdown("To read AWS from this page (see `docs/AWS_SETUP.md`):\n"
                    "1. keep your credentials in a named profile in `~/.aws/credentials` on your computer "
                    "(never in this app, never in a chat);\n"
                    "2. set `mode: READ_ONLY` in `config/scalix.local.yaml` with the profile *name* and region "
                    "(if you renamed that file to switch AWS off, rename it back);\n"
                    "3. restart the dashboard and use the buttons on this tab.")
    else:
        a = config.aws
        st.markdown(f"Profile name **{a.profile}** · region **{a.region}** · group **{a.auto_scaling_group or 'not set'}** "
                    "(names only; credentials are never shown or entered here).")
        c = st.columns(2)
        read_clicked = c[0].button("Read group and CPU now", type="primary", icon=":material/refresh:",
                                   disabled=a.auto_scaling_group is None,
                                   help="Two read-only calls: DescribeAutoScalingGroups and GetMetricStatistics.")
        check_clicked = c[1].button("Run access check", icon=":material/fact_check:",
                                    help="Up to five read-only calls that show what this account allows.")
        if read_clicked:
            try:
                from scalix.aws.monitoring import CloudWatchMonitor

                with st.spinner("Reading from AWS (read-only)..."):
                    st.session_state["aws_snapshot"] = CloudWatchMonitor.from_config(config).snapshot()
            except ConfigError as exc:
                st.warning(f"Cannot read yet: {exc}")
            except Exception as exc:  # never crash the dashboard on an AWS problem
                from scalix.aws.session import describe_error

                st.error(f"Reading failed: {describe_error('read', exc)}")
        if check_clicked:
            try:
                from scalix.aws.check_access import run_access_check

                with st.spinner("Checking access (read-only)..."):
                    st.session_state["aws_report"] = run_access_check(a)
            except Exception as exc:
                from scalix.aws.session import describe_error

                st.error(f"Access check failed: {describe_error('access check', exc)}")
        if "aws_snapshot" in st.session_state:
            render_snapshot(st.session_state["aws_snapshot"])
        if "aws_report" not in st.session_state:
            last_check_summary()
        if "aws_report" in st.session_state:
            report = st.session_state["aws_report"]
            st.subheader("Access check")
            st.dataframe(pd.DataFrame([{"check": c.check, "result": c.status, "detail": c.detail}
                                       for c in report.checks]), hide_index=True, width="stretch")
            if report.prerequisites_missing:
                st.warning("Missing: " + "; ".join(report.prerequisites_missing))
            st.caption(f"AWS calls made: {', '.join(report.calls_made) or 'none'}. Nothing was changed.")
        if a.auto_scaling_group is None:
            st.caption("Reading the group needs `aws.auto_scaling_group`; no group exists yet for this project.")

with tab_mock:
    st.markdown("Scripted data in the same shape as AWS data, judged by the same rules. "
                "Use it to see what each situation looks like. **It never comes from AWS.**")
    case = st.segmented_control("Situation", list(MOCK_CASES), default="busy", key="aws_mock_case") or "busy"
    render_snapshot(MockMonitor(name="mock-scalix-workers", min_size=1, max_size=2,
                                clock=lambda: datetime.now(timezone.utc), **MOCK_CASES[case]).snapshot())
