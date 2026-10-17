"""Download: the results as CSV files (and everything as a zip)."""

from __future__ import annotations

import streamlit as st

from scalix_dash.exports import export_files, zip_bundle

from .common import Context, require_experiment

DESCRIPTIONS = {
    "runs_summary.csv": ("One row per run", "Every measured number for each strategy, traffic pattern and run."),
    "aggregated_summary.csv": ("Averages and spread", "Mean, standard deviation, min, max and 95% interval per metric."),
    "timeseries.csv": ("Second by second", "Servers, busyness, arrivals and queue length at every simulated second."),
    "scaling_events.csv": ("Decision log", "Every decision with its reason and server count before and after."),
    "workloads.csv": ("Traffic patterns", "How each traffic pattern was generated, including its seed."),
}


def render(ctx: Context) -> None:
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    st.markdown('<div class="sx-kicker">Download</div>', unsafe_allow_html=True)
    st.header("Take the results with you")
    st.markdown('<p class="sx-lede">Plain CSV files that open in Excel, Google Sheets or pandas. They contain exactly '
                "what the simulator measured, nothing smoothed or estimated.</p>", unsafe_allow_html=True)
    files = export_files(exp)
    cols = st.columns(2)
    for i, (name, data) in enumerate(files.items()):
        title, desc = DESCRIPTIONS.get(name, (name, ""))
        with cols[i % 2].container(border=True):
            st.markdown(f"**{title}**  \n<span class='sx-note'>{desc}</span>", unsafe_allow_html=True)
            st.download_button(f"{name} · {len(data) / 1024:.0f} KB", data, file_name=f"{exp.entry.id}_{name}",
                               mime="text/csv", key=f"dl_{name}")
    st.write("")
    st.download_button("Download everything (.zip, including the simulator's raw files)", zip_bundle(exp),
                       file_name=f"{exp.entry.id}.zip", mime="application/zip", key="dl_zip", type="primary")
