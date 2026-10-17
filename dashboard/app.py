"""SCALIX dashboard (Part 2).

Launch from the project root:

    .venv/Scripts/streamlit run dashboard/app.py      (Windows)
    .venv/bin/streamlit run dashboard/app.py          (macOS/Linux)

Every chart and table is computed from CSV files written by the Java simulation engine.
"""

from __future__ import annotations

import sys
from pathlib import Path

import streamlit as st

sys.path.insert(0, str(Path(__file__).resolve().parent))

from scalix_dash import catalog  # noqa: E402
from scalix_dash.settings import SettingsError, load_settings  # noqa: E402
from views import common, compare, cloud, events, export, overview, run, timeseries  # noqa: E402

st.set_page_config(page_title="SCALIX Dashboard", layout="wide")


def main() -> None:
    st.title("SCALIX: workload-aware VM auto-scaling")
    st.caption("Results come from the CloudSim Plus simulation engine (Part 1). "
               "No demonstration or synthetic data is shown anywhere in this dashboard.")
    try:
        settings = load_settings()
    except SettingsError as e:
        st.error(f"Cannot load dashboard settings: {e}")
        st.stop()

    entries = catalog.discover(settings.results_dir)
    entry = common.experiment_selector(entries, settings)
    exp = common.load_selected(entry)
    ctx = common.Context(settings=settings, entries=entries, entry=entry, exp=exp)

    tabs = st.tabs(["Overview", "Run Experiments", "Policy Comparison", "Time-Series Analysis",
                    "Scaling Event Log", "Export Results", "Cloud Adapter (optional)"])
    with tabs[0]:
        overview.render(ctx)
    with tabs[1]:
        run.render(ctx)
    with tabs[2]:
        compare.render(ctx)
    with tabs[3]:
        timeseries.render(ctx)
    with tabs[4]:
        events.render(ctx)
    with tabs[5]:
        export.render(ctx)
    with tabs[6]:
        cloud.render(ctx)


main()
