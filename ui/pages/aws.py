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

common.page_header("AWS monitoring", "A view of one EC2 Auto Scaling group: its size and its average "
                   "CPU from CloudWatch. AWS is contacted only when you press a button below.")
st.info("This page **reads only**, unless `config/scalix.local.yaml` itself sets `mode: LIVE` and "
        "`live.enabled: true`. Then an approval panel appears for **one** change at a time: you see SCALIX's "
        "proposal, type the group name, tick the box, and the same controller gates apply as on the command line. "
        "The kill switch turns on straight after (see docs/CONTROLLER.md).",
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


def approval_panel(config) -> None:
    """Approve one live change (only shown when the config file enables LIVE). Logic: scalix.aws.approval."""
    import time as _time

    from scalix.aws import approval
    from scalix.aws import audit as audit_module
    from scalix.aws.audit import AuditLog

    st.subheader("Approve a live change")
    st.error("**LIVE mode is on in your config file.** Approving here changes your real Auto Scaling group "
             "(one `SetDesiredCapacity` call). Strict budget: USD 20.", icon=":material/warning:")
    from scalix.config import kill_switch_active

    kill = kill_switch_active()
    if kill:
        st.info(f"Kill switch is on ({kill}), so nothing can be applied. Remove `config/LIVE_DISABLED` by hand "
                "only when you have decided to make another change.", icon=":material/lock:")
    if st.button("Get SCALIX's proposal (dry run, live data)", icon=":material/science:", key="approve_get"):
        try:
            from scalix.aws.monitoring import CloudWatchMonitor

            with st.spinner("Reading AWS and running SCALIX (dry run)..."):
                result, proposal = approval.get_proposal(config, CloudWatchMonitor.from_config(config),
                                                         AuditLog(audit_module.DEFAULT_AUDIT_PATH))
            st.session_state["approve_result"] = result
            st.session_state["approve_proposal"] = proposal
            st.session_state.pop("approve_outcome", None)
        except Exception as exc:
            from scalix.aws.session import describe_error

            st.error(f"Could not get a proposal: {describe_error('proposal', exc)}")
    result = st.session_state.get("approve_result")
    proposal = st.session_state.get("approve_proposal")
    if result is not None:
        st.code(result.render(), language=None)
    if result is not None and proposal is None:
        st.info("Nothing to approve: SCALIX proposes no change, or a safety check refused it.")
    if proposal is not None:
        age = proposal.age(_time.time())
        left = max(0, approval.PROPOSAL_MAX_AGE_SECONDS - age)
        st.markdown(f"**Proposal:** change `{proposal.group}` from **{proposal.desired_before}** to "
                    f"**{proposal.desired_proposed}** workers. Valid for {left:.0f} more seconds.")
        with st.form("approve_form", clear_on_submit=True):
            typed = st.text_input(f"Type the group name to confirm ({proposal.group})", key="approve_typed")
            ack = st.checkbox("I approve this one change to my real AWS group (about USD 0.03 for this test)",
                              key="approve_ack")
            submitted = st.form_submit_button("Apply this one change", type="primary", icon=":material/bolt:")
        if submitted:
            try:
                from scalix.aws.monitoring import CloudWatchMonitor

                with st.spinner("Re-reading AWS and applying if it still matches..."):
                    outcome = approval.approve(config, proposal, typed, ack,
                                               monitor=CloudWatchMonitor.from_config(config),
                                               audit=AuditLog(audit_module.DEFAULT_AUDIT_PATH))
            except Exception as exc:
                from scalix.aws.session import describe_error

                outcome = f"Not applied: {describe_error('apply', exc)}"
            st.session_state["approve_outcome"] = outcome
            if not isinstance(outcome, str):
                st.session_state["approve_proposal"] = None   # a proposal can be used once
    outcome = st.session_state.get("approve_outcome")
    if isinstance(outcome, str):
        st.warning(outcome)
    elif outcome is not None:
        (st.success if outcome.outcome.value == "APPLIED" else st.error)(
            f"{outcome.outcome.value}: {outcome.reason}. The kill switch is now on." if outcome.outcome.value
            in ("APPLIED", "FAILED") else f"{outcome.outcome.value}: {outcome.reason}")
        st.code(outcome.render(), language=None)
        st.caption("Recorded in the audit log as approved from the dashboard; see the Scaling history page.")


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


def render_access_report(report, source: DataSource) -> None:
    """The access-check result, the same way for a live and a mocked check."""
    st.subheader("Access check")
    common.show_source(source, "Read-only checks of what this account allows")
    st.dataframe(pd.DataFrame([{"check": c.check, "result": c.status, "detail": c.detail} for c in report.checks]),
                 hide_index=True, width="stretch")
    if report.prerequisites_missing:
        st.warning("Missing: " + "; ".join(report.prerequisites_missing))
    calls = ", ".join(report.calls_made) or "none"
    if source is DataSource.MOCKED:
        st.caption(f"Calls a real check would make: {calls}. This is scripted data: nothing was sent to AWS.")
    else:
        st.caption(f"AWS calls made: {calls}. Nothing was changed.")


def mock_access_report(case: str):
    """A scripted access check shaped like a real AWS Academy one, for the mocked tab."""
    from scalix.aws.check_access import AccessReport, CheckResult

    cpu = MOCK_CASES[case]["cpu_percents"][0]
    cpu_detail = ("no datapoints in the latest period (MISSING, not 0%)" if cpu is None
                  else "malformed datapoint (MALFORMED)" if cpu != cpu
                  else f"latest 5-minute average {cpu:.0f}%")
    return AccessReport(
        generated_utc=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        environment={"selected_profile": MOCK_PROFILE, "temporary_credentials": True},
        checks=[CheckResult("AWS CLI", "INFO", "not needed: the AWS Python library is enough"),
                CheckResult("Credentials", "OK", "found via shared-credentials-file; temporary (scripted)"),
                CheckResult("Region", "INFO", MOCK_REGION),
                CheckResult("Caller identity", "OK", "account ********0000, identity arn:aws:sts::********0000:"
                            "assumed-role/voclabs/use… (scripted)"),
                CheckResult("Describe Auto Scaling groups", "OK", f"permitted; 1 group(s) visible: {MOCK_GROUP}"),
                CheckResult("Describe EC2 instances", "OK",
                            f"permitted; first page shows {MOCK_CASES[case]['in_service']} instance(s): running"),
                CheckResult("List CloudWatch metrics", "OK", "permitted; 1 EC2 CPU metric series visible"),
                CheckResult("Read CloudWatch CPU for the group", "OK", cpu_detail)],
        calls_made=["sts:GetCallerIdentity", "autoscaling:DescribeAutoScalingGroups", "ec2:DescribeInstances",
                    "cloudwatch:ListMetrics", "cloudwatch:GetMetricStatistics"])


def mock_snapshot(case: str) -> MonitoringSnapshot:
    return MockMonitor(name=MOCK_GROUP, min_size=1, max_size=2, clock=lambda: datetime.now(timezone.utc),
                       **MOCK_CASES[case]).snapshot()


def connection_line(profile: str, region: str, group: str | None, note: str) -> None:
    st.markdown(f"Profile name **{profile}** · region **{region}** · group **{group or 'not set'}** ({note}).")


def action_buttons(prefix: str, read_disabled: bool) -> tuple[bool, bool]:
    c = st.columns(2)
    read = c[0].button("Read group and CPU now", type="primary", icon=":material/refresh:", key=f"{prefix}_read",
                       disabled=read_disabled,
                       help="Two read-only calls: DescribeAutoScalingGroups and GetMetricStatistics.")
    check = c[1].button("Run access check", icon=":material/fact_check:", key=f"{prefix}_check",
                        help="Up to five read-only calls that show what this account allows.")
    return read, check


MOCK_PROFILE, MOCK_REGION, MOCK_GROUP = "mock-profile", "us-east-1", "mock-scalix-workers"

tab_live, tab_mock = st.tabs(["Live AWS", "Mocked preview"])

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
        st.caption("The **Mocked preview** tab shows exactly what this tab looks like when it is reading AWS.")
    else:
        a = config.aws
        connection_line(a.profile, a.region, a.auto_scaling_group,
                        "names only; credentials are never shown or entered here")
        read_clicked, check_clicked = action_buttons("live", read_disabled=a.auto_scaling_group is None)
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
        if "aws_report" in st.session_state:
            render_access_report(st.session_state["aws_report"], DataSource.LIVE_AWS)
        else:
            last_check_summary()
        if a.auto_scaling_group is None:
            st.caption("Reading the group needs `aws.auto_scaling_group`; no group exists yet for this project.")
        from scalix.aws.approval import live_approval_enabled

        if live_approval_enabled(config):
            approval_panel(config)

with tab_mock:
    st.markdown("The same view as the live tab, filled with **scripted data shaped exactly like AWS's answers** and "
                "judged by the same rules. Pick a situation, then use the buttons as you would on the live tab. "
                "**It never comes from AWS.**")
    case = st.segmented_control("Situation", list(MOCK_CASES), default="busy", key="aws_mock_case") or "busy"
    connection_line(MOCK_PROFILE, MOCK_REGION, MOCK_GROUP, "scripted names; no credentials involved")
    mock_read, mock_check = action_buttons("mock", read_disabled=False)
    if mock_read or ("mock_snapshot" in st.session_state and st.session_state.get("mock_case_read") != case):
        st.session_state["mock_snapshot"] = mock_snapshot(case)
        st.session_state["mock_case_read"] = case
    if mock_check or ("mock_report" in st.session_state and st.session_state.get("mock_case_checked") != case):
        st.session_state["mock_report"] = mock_access_report(case)
        st.session_state["mock_case_checked"] = case
    if "mock_snapshot" in st.session_state:
        render_snapshot(st.session_state["mock_snapshot"])
    if "mock_report" in st.session_state:
        render_access_report(st.session_state["mock_report"], DataSource.MOCKED)
    if "mock_snapshot" not in st.session_state and "mock_report" not in st.session_state:
        st.caption("Press a button to see the result, exactly where the live tab shows it.")
