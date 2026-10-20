"""SCALIX dashboard: navigation and sidebar. Start with ``streamlit run app.py`` from the project root."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:  # allows `streamlit run ui/app.py` as well
    sys.path.insert(0, str(ROOT))

import streamlit as st  # noqa: E402

from ui import common  # noqa: E402

st.set_page_config(page_title="SCALIX", page_icon=":material/stacked_line_chart:", layout="wide",
                   initial_sidebar_state="expanded")

PAGES = Path(__file__).resolve().parent / "pages"
navigation = st.navigation([
    st.Page(PAGES / "overview.py", title="Overview", icon=":material/dashboard:", default=True),
    st.Page(PAGES / "simulation.py", title="Workload simulation", icon=":material/tune:"),
    st.Page(PAGES / "comparison.py", title="Policy comparison", icon=":material/compare_arrows:"),
    st.Page(PAGES / "aws.py", title="AWS monitoring", icon=":material/cloud:"),
    st.Page(PAGES / "history.py", title="Scaling history", icon=":material/history:"),
])
config, config_error = common.settings()
common.sidebar(config, config_error)
navigation.run()
