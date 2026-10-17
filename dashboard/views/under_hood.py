"""Under the hood: the problem, the method, the definitions, and the technical status."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from scalix_dash.analysis import METRICS, config_consistency
from scalix_dash.runner import SimulationRunner
from scalix_dash.story import PLAIN_METRICS

from . import cloud
from .common import Context, context_panel


def render(ctx: Context) -> None:
    st.markdown('<div class="sx-kicker">Under the hood</div>', unsafe_allow_html=True)
    st.header("How this works")

    with st.expander("What problem is SCALIX solving?", expanded=True):
        st.markdown(
            "Cloud providers rent servers by the hour or second. An app's traffic goes up and down all day, so the "
            "number of servers it needs keeps changing. **Auto-scaling** is the job of adding servers when traffic "
            "grows and removing them when it shrinks.\n\n"
            "The usual approaches each have a weak spot:\n"
            "- A **fixed fleet** is either too small during a rush (users wait) or too big the rest of the time "
            "(you pay for idle machines).\n"
            "- A **simple rule** (add above 70% busy, remove below 30%) only reacts *after* servers are already "
            "overloaded, and a new server takes time to start. It can also flip-flop, adding and removing servers "
            "over and over.\n\n"
            "**SCALIX** also looks at the *direction* demand is moving, so it can add capacity early when traffic is "
            "climbing. It waits before removing servers to avoid flip-flopping, and never drops below a safe minimum.\n\n"
            "This app doesn't claim SCALIX always wins. Faster responses usually cost more servers, and for some "
            "traffic a fixed fleet or the simple rule is the better deal. The results pages show those trade-offs "
            "honestly.")

    with st.expander("How the experiment is run"):
        st.markdown(
            "1. Your settings become a configuration file for the **simulator**, a Java program built on "
            "CloudSim Plus, a widely used cloud-simulation library.\n"
            "2. The simulator generates the traffic (random arrivals with a fixed seed) and plays the *exact same* "
            "requests to every strategy, on the same server type, with the same timing and prices.\n"
            "3. Each extra run repeats this with different random traffic, so the averages and the spread between "
            "runs tell you how reliable a difference is.\n"
            "4. The simulator writes CSV files. The dashboard checks them (right columns, right types, nothing "
            "missing) before showing anything. Failed runs are reported and left out, never shown as results.")

    with st.expander("What the numbers mean"):
        rows = []
        for col, plain in PLAIN_METRICS.items():
            m = METRICS[col]
            rows.append({"In this app": plain, "Technical name": col, "Type": m.kind.lower(),
                         "Exactly what it is": m.description})
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
        st.markdown(
            "- **Measured** numbers come straight from the simulation. **Calculated** numbers (only the cost) apply "
            "an *assumed* price of $0.10 per server-hour to the measured server time. It is not a real cloud "
            "provider's price list.\n"
            "- **±** is the standard deviation between runs. The detailed comparison also shows 95% confidence "
            "intervals and only calls a winner *clear* when those intervals don't overlap.\n"
            "- Full definitions: `docs/METRICS.md` in the project.")

    if ctx.exp is not None:
        with st.expander("Conditions of the selected experiment"):
            diffs = config_consistency(ctx.exp.configs)
            if diffs.empty:
                st.success("All runs used identical settings apart from the random seed.")
            else:
                st.error("Runs used different settings:")
                st.dataframe(diffs, hide_index=True, width="stretch")
            context_panel(ctx.exp)

    with st.expander("Simulator status"):
        problems = SimulationRunner(ctx.settings).preflight()
        if problems:
            for p in problems:
                st.error(p)
        else:
            st.success("The simulator is installed and ready.")
        st.markdown(f"- Command: `{' '.join(ctx.settings.engine_command)}`\n"
                    f"- Results folder: `{ctx.settings.results_dir}`\n"
                    f"- Time limit per run: {ctx.settings.timeout_seconds:.0f} s\n"
                    "- Settings file: `config/dashboard.yaml` (or the `SCALIX_DASHBOARD_CONFIG` environment variable)")

    with st.expander("Connecting to a real cloud (optional)"):
        cloud.render_section(ctx)
