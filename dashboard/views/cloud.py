"""Optional cloud adapter section: replays a run's decisions through the mock provider."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scalix_dash.cloud import MockCloudProvider, replay
from scalix_dash.cloud.base import operations_from_events
from scalix_dash.cloud.cloudstack import CloudStackConfig, CloudStackProvider
from scalix_dash.story import policy_name, scenario_name

from .common import Context, policies_of, workloads_of


def render_section(ctx: Context) -> None:
    st.markdown(
        "SCALIX's decisions could drive a real cloud such as Apache CloudStack. This is **optional and switched off**. "
        "Below, a *mock* cloud replays a run's decisions and records what it would have done. Nothing is created, "
        "nothing is sent over the network, and this dashboard never touches a real cloud.")
    cs = CloudStackConfig.from_settings(ctx.settings.cloud)
    missing = cs.missing()
    st.caption(f"CloudStack adapter: {'enabled' if cs.enabled else 'disabled (default)'}; "
               + ("configured." if not missing else f"not configured ({', '.join(missing)} missing).")
               + " Setup and safety rules: docs/CLOUDSTACK.md")
    if ctx.exp is None:
        return
    exp = ctx.exp
    c1, c2, c3 = st.columns(3)
    workload = c1.selectbox("Traffic pattern", workloads_of(exp), format_func=scenario_name, key="cl_workload")
    policy = c2.selectbox("Strategy", policies_of(exp), format_func=policy_name, key="cl_policy")
    rep = c3.selectbox("Run", sorted(exp.summary["rep"].unique()), key="cl_rep")
    ev = exp.events[(exp.events["workload_type"] == workload) & (exp.events["policy"] == policy)
                    & (exp.events["rep"] == rep)]
    initial = max(1, int(ev.sort_values("time_s")["vms_before"].iloc[0])) if not ev.empty else 1
    provider = MockCloudProvider(initial_vms=initial, min_vms=1)
    records = replay(ev, provider)
    if not records:
        st.write("This run never resized, so a cloud would have received no requests.")
        return
    st.markdown(f"The mock cloud would have handled **{len(records)} requests** and ended with "
                f"**{len(provider.vms)} servers**.")
    st.dataframe(pd.DataFrame([r.as_row() for r in records]), hide_index=True, width="stretch")
    if st.toggle("Show the CloudStack API calls these map to (plan only)", key="cl_plan"):
        planner = CloudStackProvider(CloudStackConfig(
            enabled=False, endpoint="", api_key="", secret_key="", zone_id=cs.zone_id or "<zone>",
            template_id=cs.template_id or "<template>", service_offering_id=cs.service_offering_id or "<offering>",
            vm_group=cs.vm_group))
        rows = [{"sim_time_s": op.sim_time_s, **cmd} for op in operations_from_events(ev)[:50] for cmd in planner.plan(op)]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.caption("First 50 operations. Credentials are never shown or stored here.")
