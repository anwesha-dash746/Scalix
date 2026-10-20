"""Phase 0 foundation: the package imports cleanly, needs no AWS, and secrets stay out of Git."""

from __future__ import annotations

import importlib
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

SUBPACKAGES = [
    "scalix",
    "scalix.algorithms",
    "scalix.simulation",
    "scalix.metrics",
    "scalix.aws",
    "scalix.config",
    "scalix.sources",
    "scalix.aws.session",
    "scalix.aws.monitoring",
    "scalix.aws.check_access",
]


@pytest.mark.parametrize("name", SUBPACKAGES)
def test_subpackage_imports(name):
    assert importlib.import_module(name) is not None


def _clean_env(tmp_path: Path) -> dict[str, str]:
    """Environment with no AWS variables and an empty home, so no ~/.aws files can be found."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("AWS_")}
    env["HOME"] = str(tmp_path)
    env["USERPROFILE"] = str(tmp_path)
    env["AWS_CONFIG_FILE"] = str(tmp_path / "no-config")
    env["AWS_SHARED_CREDENTIALS_FILE"] = str(tmp_path / "no-credentials")
    env["PYTHONPATH"] = str(ROOT)
    return env


def test_package_works_without_aws_and_never_loads_boto3(tmp_path):
    """Run in a fresh interpreter with no AWS credentials. boto3 must not be imported."""
    code = (
        "import sys\n"
        "import scalix, scalix.algorithms, scalix.simulation, scalix.metrics, scalix.aws\n"
        "from scalix.config import load_config, Mode, DEFAULT_CONFIG_PATH\n"
        "assert load_config(DEFAULT_CONFIG_PATH).mode is Mode.SIMULATION\n"
        "import scalix.aws.session, scalix.aws.monitoring, scalix.aws.check_access, scalix.sources\n"
        "loaded = sorted(m for m in sys.modules if m.split('.')[0] in ('boto3', 'botocore'))\n"
        "assert not loaded, loaded\n"
        "print('ok')\n"
    )
    out = subprocess.run([sys.executable, "-c", code], cwd=tmp_path, env=_clean_env(tmp_path),
                         capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    assert out.stdout.strip() == "ok"


def test_root_entry_point_renders_the_dashboard():
    from streamlit.testing.v1 import AppTest

    at = AppTest.from_file(str(ROOT / "app.py"), default_timeout=60).run()
    assert not at.exception, [e.value for e in at.exception]
    assert [t.value for t in at.title] == ["SCALIX"]


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="git or the .git folder is not available")
@pytest.mark.parametrize("path", [
    ".env",
    ".env.local",
    ".venv/bin/python",
    "my-key.pem",
    ".aws/credentials",
    "credentials",
    "credentials.csv",
    "student_accessKeys.csv",
    "config/scalix.local.yaml",
    "results/aws/access-report-1.json",
])
def test_secret_and_local_files_are_git_ignored(path):
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
    assert result.returncode == 0, f"{path} is not ignored by .gitignore"


@pytest.mark.skipif(shutil.which("git") is None or not (ROOT / ".git").exists(),
                    reason="git or the .git folder is not available")
@pytest.mark.parametrize("path", ["config/scalix.yaml", "app.py", "scalix/config.py"])
def test_project_files_are_not_ignored(path):
    result = subprocess.run(["git", "check-ignore", "-q", "--no-index", path], cwd=ROOT)
    assert result.returncode == 1, f"{path} should be tracked but .gitignore excludes it"
