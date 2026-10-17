"""Export Results: downloadable CSVs (and a zip with the raw engine files)."""

from __future__ import annotations

import streamlit as st

from scalix_dash.exports import export_files, zip_bundle

from .common import Context, require_experiment

DESCRIPTIONS = {
    "runs_summary.csv": "One row per run (workload x policy x repetition) with all measured metrics and parameters.",
    "aggregated_summary.csv": "Mean, std, min, max, 95% CI per workload x policy x metric (valid runs only).",
    "timeseries.csv": "Per-tick VM counts, utilization, arrivals and requests in system.",
    "scaling_events.csv": "Every policy decision with reason and VM count before/after.",
    "workloads.csv": "Workload definitions, seeds and generated request counts.",
}


def render(ctx: Context) -> None:
    st.subheader("Export Results")
    if not require_experiment(ctx):
        return
    exp = ctx.exp
    st.caption(f"All files are generated from the real engine output of `{exp.entry.id}` "
               f"({exp.n_reps} valid repetition(s)).")
    files = export_files(exp)
    for name, data in files.items():
        c1, c2 = st.columns([1, 3])
        c1.download_button(name, data, file_name=f"{exp.entry.id}_{name}", mime="text/csv", key=f"dl_{name}")
        c2.caption(f"{DESCRIPTIONS.get(name, '')} ({len(data) / 1024:.0f} KiB)")
    st.download_button("Everything as .zip (derived CSVs + raw engine files)", zip_bundle(exp),
                       file_name=f"{exp.entry.id}.zip", mime="application/zip", key="dl_zip")
