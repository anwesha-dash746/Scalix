"""SCALIX dashboard entry point.

Run from the project root:

    .venv/bin/streamlit run app.py          (macOS / Linux)
    .venv\\Scripts\\streamlit run app.py      (Windows)

This file only hands over to ``dashboard/app.py``, where the dashboard lives, so both
``streamlit run app.py`` and ``streamlit run dashboard/app.py`` start the same app.
"""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / "dashboard" / "app.py"), run_name="__main__")
