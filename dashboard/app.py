"""SCALIX dashboard.

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
from views import common, events, export, results, start, timeseries, under_hood  # noqa: E402

st.set_page_config(page_title="SCALIX", layout="wide", initial_sidebar_state="expanded")

PAGES = {
    "Start": start.render,
    "Results": results.render,
    "Over time": timeseries.render,
    "Decision log": events.render,
    "Download": export.render,
    "Under the hood": under_hood.render,
}

STYLE = """
<style>
  .block-container {max-width: 1180px; padding-top: 2.2rem;}
  .sx-kicker {text-transform: uppercase; letter-spacing: .08em; font-size: .72rem; color: #6b7280; margin-bottom: .15rem;}
  .sx-lede {font-size: 1.12rem; line-height: 1.6; color: #3f444b; max-width: 44rem;}
  .sx-big {font-family: Georgia, serif; font-size: 1.55rem; line-height: 1.2; margin: .1rem 0 .2rem 0;}
  .sx-value {font-size: 1.05rem; color: #1f2328;}
  .sx-note {font-size: .86rem; color: #6b7280;}
  .sx-dot {display: inline-block; width: .7rem; height: .7rem; border-radius: 50%; margin-right: .45rem; vertical-align: baseline;}
</style>
"""


def main() -> None:
    st.markdown(STYLE, unsafe_allow_html=True)
    try:
        settings = load_settings()
    except SettingsError as e:
        st.error(f"The dashboard settings file could not be loaded: {e}")
        st.stop()

    entries = catalog.discover(settings.results_dir)
    entry = common.experiment_selector(entries, settings)
    exp = common.load_selected(entry)
    ctx = common.Context(settings=settings, entries=entries, entry=entry, exp=exp)

    # Other pages request navigation through PENDING_NAV_KEY; a widget's own key may only be
    # changed before the widget is drawn in a run.
    if common.PENDING_NAV_KEY in st.session_state:
        st.session_state[common.NAV_KEY] = st.session_state.pop(common.PENDING_NAV_KEY)
    st.session_state.setdefault(common.NAV_KEY, "Start")
    page = st.segmented_control("Section", list(PAGES), key=common.NAV_KEY, required=True,
                                label_visibility="collapsed")
    st.write("")
    PAGES[page or "Start"](ctx)


main()
