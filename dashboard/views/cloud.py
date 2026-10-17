"""Optional cloud adapter view: replays a run's scaling decisions through the mock provider."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scalix_dash.cloud import MockCloudProvider, replay
from scalix_dash.cloud.cloudstack import CloudStackConfig, CloudStackProvider
from scalix_dash.cloud.base import operations_from_events

from .common import Context, policies_of, require_experiment, workloads_of


def render(ctx: Context) -> None:
    st.subheader("Cloud Adapter (optional)")
    st.caption("Shows how a run's scaling decisions would map to VM operations. The mock adapter only records "
               "intended operations; nothing is provisioned and no network call is made. The dashboard never "
               "executes real cloud operations.")
    cs = CloudStackConfig.from_settings(ctx.settings.cloud)
    status = "enabled in settings" if cs.enabled else "disabled (default)"
    missing = cs.missing()
    st.info(f"CloudStack adapter: {status}. "
            + ("Configuration complete." if not missing else f"Not configured: {', '.join(missing)}."))
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    c1, c2, c3 = st.columns(3)
    workload = c1.selectbox("Workload", workloads_of(exp), key="cl_workload")
    policy = c2.selectbox("Policy", policies_of(exp), key="cl_policy")
    rep = c3.selectbox("Repetition", sorted(exp.summary["rep"].unique()), key="cl_rep")
    ev = exp.events[(exp.events["workload_type"] == workload) & (exp.events["policy"] == policy)
                    & (exp.events["rep"] == rep)]
    run = exp.summary[(exp.summary["workload_type"] == workload) & (exp.summary["policy"] == policy)
                      & (exp.summary["rep"] == rep)]
    if run.empty:
        st.info("No such run.")
        return
    # Start the mock fleet at the run's VM count before its first decision.
    initial = max(1, int(ev.sort_values("time_s")["vms_before"].iloc[0])) if not ev.empty else 1
    provider = MockCloudProvider(initial_vms=initial, min_vms=1)
    records = replay(ev, provider)
    if not records:
        st.write("This run made no scaling actions, so no VM operations would be issued.")
    else:
        st.markdown(f"**Mock adapter: {len(records)} intended operation(s); final mock fleet {len(provider.vms)} VMs.**")
        st.dataframe(pd.DataFrame([r.as_row() for r in records]), hide_index=True, width="stretch")

    with st.expander("CloudStack API calls these decisions map to (plan only, credentials never shown)"):
        planner = CloudStackProvider(CloudStackConfig(enabled=False, endpoint="", api_key="", secret_key="",
                                                      zone_id=cs.zone_id or "<zone>", template_id=cs.template_id or "<template>",
                                                      service_offering_id=cs.service_offering_id or "<offering>",
                                                      vm_group=cs.vm_group))
        plan_rows = []
        for op in operations_from_events(ev)[:50]:
            for cmd in planner.plan(op):
                plan_rows.append({"sim_time_s": op.sim_time_s, **cmd})
        if plan_rows:
            st.dataframe(pd.DataFrame(plan_rows), hide_index=True, width="stretch")
            st.caption("First 50 operations shown. See docs/CLOUDSTACK.md for the mapping and the safety rules.")
        else:
            st.write("No operations.")
