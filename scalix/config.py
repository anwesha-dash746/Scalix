"""Run-mode configuration for SCALIX.

SCALIX has three run modes:

* ``SIMULATION`` (default): everything runs locally; AWS is never contacted.
* ``READ_ONLY``: may *read* AWS state (for example CloudWatch metrics); never changes anything.
* ``LIVE``: may change the capacity of one allowlisted Auto Scaling group. It needs two explicit
  switches, ``mode: LIVE`` **and** ``live.enabled: true``, so one typo cannot turn it on.

The settings live in ``config/scalix.yaml``. If that file is missing, SCALIX uses the safe
defaults (SIMULATION). The mode is never taken from environment variables, so a leftover shell
variable cannot switch on AWS access by accident.

Later phases add the AWS-specific settings (allowlisted group, capacity cap, cooldowns, and so on).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "scalix.yaml"

_TOP_LEVEL_KEYS = {"mode", "live"}
_LIVE_KEYS = {"enabled"}


class ConfigError(ValueError):
    """The SCALIX configuration is invalid. The message says what to fix."""


class Mode(str, Enum):
    """How SCALIX is allowed to interact with AWS."""

    SIMULATION = "SIMULATION"
    READ_ONLY = "READ_ONLY"
    LIVE = "LIVE"

    @property
    def uses_aws(self) -> bool:
        """True if this mode may contact AWS at all."""
        return self is not Mode.SIMULATION

    @property
    def may_change_aws(self) -> bool:
        """True only for LIVE: the one mode that may change AWS resources."""
        return self is Mode.LIVE


@dataclass(frozen=True)
class ScalixConfig:
    """Validated SCALIX settings. Building one with an unsafe combination raises ConfigError."""

    mode: Mode = Mode.SIMULATION
    live_enabled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode):
            raise ConfigError(f"mode must be a Mode, got {self.mode!r}")
        if not isinstance(self.live_enabled, bool):
            raise ConfigError(f"live.enabled must be true or false, got {self.live_enabled!r}")
        if self.mode is Mode.LIVE and not self.live_enabled:
            raise ConfigError(
                "mode is LIVE but live.enabled is false. LIVE mode can change AWS resources, "
                "so it needs both 'mode: LIVE' and 'live.enabled: true' in config/scalix.yaml."
            )


def parse_mode(value: Any) -> Mode:
    """Turn a config value such as ``"simulation"`` or ``"READ_ONLY"`` into a Mode."""
    if not isinstance(value, str):
        raise ConfigError(f"mode must be text (SIMULATION, READ_ONLY or LIVE), got {value!r}")
    try:
        return Mode(value.strip().upper())
    except ValueError:
        raise ConfigError(f"unknown mode {value!r}; use SIMULATION, READ_ONLY or LIVE") from None


def parse_config(data: Any) -> ScalixConfig:
    """Validate an already-parsed YAML document. Unknown keys are rejected, not ignored."""
    if data is None:
        return ScalixConfig()
    if not isinstance(data, dict):
        raise ConfigError("the SCALIX config file must contain key: value pairs at the top level")

    unknown = set(data) - _TOP_LEVEL_KEYS
    if unknown:
        raise ConfigError(f"unknown setting(s): {', '.join(sorted(map(str, unknown)))}")

    mode = parse_mode(data["mode"]) if "mode" in data else Mode.SIMULATION

    live = data.get("live") or {}
    if not isinstance(live, dict):
        raise ConfigError("'live' must contain key: value pairs, for example 'enabled: false'")
    unknown_live = set(live) - _LIVE_KEYS
    if unknown_live:
        raise ConfigError(f"unknown live setting(s): {', '.join(sorted(map(str, unknown_live)))}")
    live_enabled = live.get("enabled", False)
    if not isinstance(live_enabled, bool):
        raise ConfigError(f"live.enabled must be true or false (without quotes), got {live_enabled!r}")

    return ScalixConfig(mode=mode, live_enabled=live_enabled)


def load_config(path: str | Path | None = None) -> ScalixConfig:
    """Load and validate the SCALIX settings.

    With no ``path``, reads ``config/scalix.yaml`` and falls back to the safe defaults if that
    file does not exist. An explicitly given ``path`` must exist.
    """
    explicit = path is not None
    file = Path(path) if explicit else DEFAULT_CONFIG_PATH
    if not file.is_file():
        if explicit:
            raise ConfigError(f"config file not found: {file}")
        return ScalixConfig()
    try:
        data = yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file} is not valid YAML: {exc}") from None
    try:
        return parse_config(data)
    except ConfigError as exc:
        raise ConfigError(f"{file}: {exc}") from None
