"""SCALIX dashboard entry point.

Run from the project root:

    .venv/bin/streamlit run app.py          (macOS / Linux)
    .venv\\Scripts\\streamlit run app.py      (Windows)

This starts the AWS-edition dashboard in ``ui/`` (Python engine). The original Part 1 dashboard,
which drives the Java engine, is still available with ``streamlit run dashboard/app.py``.
"""

from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().parent / "ui" / "app.py"), run_name="__main__")
