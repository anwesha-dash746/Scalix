"""Dashboard settings loaded from YAML (config/dashboard.yaml by default)."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SETTINGS_FILE = PROJECT_ROOT / "config" / "dashboard.yaml"
SETTINGS_ENV = "SCALIX_DASHBOARD_CONFIG"


class SettingsError(ValueError):
    """Raised when the settings file is missing or invalid."""


@dataclass(frozen=True)
class Settings:
    project_root: Path
    engine_command: list[str]
    engine_jar: Path | None
    timeout_seconds: float
    results_dir: Path
    defaults: dict[str, Any] = field(default_factory=dict)
    cloud: dict[str, Any] = field(default_factory=dict)

    @property
    def dashboard_runs_dir(self) -> Path:
        return self.results_dir / "dashboard"


def load_settings(path: Path | str | None = None) -> Settings:
    """Loads settings from ``path``, the ``SCALIX_DASHBOARD_CONFIG`` env var, or the default file."""
    if path is None:
        path = os.environ.get(SETTINGS_ENV) or DEFAULT_SETTINGS_FILE
    path = Path(path)
    if not path.is_file():
        raise SettingsError(f"Settings file not found: {path}")
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise SettingsError(f"Settings file {path} is not valid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise SettingsError(f"Settings file {path} must contain a mapping")

    # Relative paths are relative to the project root: the parent of the config/ folder
    # for the default layout, or the settings file's own folder otherwise.
    root = path.resolve().parent.parent if path.resolve().parent.name == "config" else path.resolve().parent

    engine = raw.get("engine") or {}
    command = engine.get("command")
    if not isinstance(command, list) or not command or not all(isinstance(c, str) and c for c in command):
        raise SettingsError("engine.command must be a non-empty list of strings")
    jar = engine.get("jar")
    timeout = engine.get("timeout_seconds", 900)
    if not isinstance(timeout, (int, float)) or timeout <= 0:
        raise SettingsError("engine.timeout_seconds must be a positive number")
    results_dir = raw.get("results_dir", "results")
    if not isinstance(results_dir, str) or not results_dir:
        raise SettingsError("results_dir must be a non-empty string")

    return Settings(
        project_root=root,
        engine_command=[_resolve_arg(c, root) for c in command],
        engine_jar=_resolve(jar, root) if jar else None,
        timeout_seconds=float(timeout),
        results_dir=_resolve(results_dir, root),
        defaults=dict(raw.get("defaults") or {}),
        cloud=dict(raw.get("cloud") or {}),
    )


def _resolve(p: str, root: Path) -> Path:
    q = Path(p)
    return q if q.is_absolute() else (root / q)


def _resolve_arg(arg: str, root: Path) -> str:
    """Makes path-like command arguments (containing a slash, e.g. the jar) project-relative.

    Bare executable names such as ``java`` are left for the OS to find on PATH.
    """
    if arg.startswith("-") or os.path.isabs(arg) or not ("/" in arg or "\\" in arg):
        return arg
    return str(root / arg)
