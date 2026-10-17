import sys
from pathlib import Path

import pytest

DASHBOARD = Path(__file__).resolve().parents[1]
TESTS = Path(__file__).resolve().parent
for p in (DASHBOARD, TESTS):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from scalix_dash.settings import PROJECT_ROOT, Settings  # noqa: E402

FAKE_ENGINE = TESTS / "fake_engine.py"


@pytest.fixture
def fake_settings(tmp_path) -> Settings:
    """Settings whose engine is the test-only fake engine and whose results go to tmp_path."""
    return Settings(project_root=PROJECT_ROOT, engine_command=[sys.executable, str(FAKE_ENGINE)], engine_jar=None,
                    timeout_seconds=20, results_dir=tmp_path / "results", defaults={}, cloud={})
