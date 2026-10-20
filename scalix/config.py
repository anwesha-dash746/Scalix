"""Run-mode and AWS settings for SCALIX.

SCALIX has three run modes:

* ``SIMULATION`` (default): everything runs locally; AWS is never contacted.
* ``READ_ONLY``: may *read* AWS state (CloudWatch metrics, Auto Scaling group state); never changes anything.
* ``LIVE``: may change the capacity of one allowlisted Auto Scaling group. It needs two explicit
  switches, ``mode: LIVE`` **and** ``live.enabled: true``, so one typo cannot turn it on.

Where settings come from:

1. ``config/scalix.yaml``: committed, safe defaults (SIMULATION, no AWS details).
2. ``config/scalix.local.yaml``: optional, **git-ignored**, your personal settings (AWS profile
   name, region, group name). Its values override the committed file. Copy
   ``config/scalix.local.example.yaml`` to start.

If neither file exists, SCALIX uses the safe defaults. The mode is never taken from environment
variables, so a leftover shell variable cannot switch on AWS access by accident.

AWS credentials are **never** stored here. ``aws.profile`` is only the *name* of a profile in your
own ``~/.aws/credentials`` file, and READ_ONLY / LIVE refuse to run without one. That way SCALIX
never quietly picks up whatever credentials happen to be in the environment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "scalix.yaml"
LOCAL_CONFIG_PATH = PROJECT_ROOT / "config" / "scalix.local.yaml"

_TOP_LEVEL_KEYS = {"mode", "live", "aws"}
_LIVE_KEYS = {"enabled"}
_AWS_KEYS = {"profile", "region", "auto_scaling_group", "metric_period_seconds", "max_metric_age_seconds"}
_REGION_RE = re.compile(r"^[a-z]{2}(-gov|-iso[a-z]*)?-[a-z]+-\d+$")
_GROUP_RE = re.compile(r"^[\w\-.:/ ]{1,255}$")
_PROFILE_RE = re.compile(r"^[\w\-.@+]{1,128}$")


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
class AwsSettings:
    """Which AWS account view to read. Names only, never credentials."""

    profile: str | None = None
    region: str | None = None
    auto_scaling_group: str | None = None
    #: CloudWatch period. EC2 basic (free) monitoring publishes every 5 minutes, so 300 s;
    #: 60 s needs detailed monitoring, which AWS charges for.
    metric_period_seconds: int = 300
    #: A CPU reading older than this is treated as STALE and not used for decisions.
    max_metric_age_seconds: int = 900

    def __post_init__(self) -> None:
        for name, pattern in (("profile", _PROFILE_RE), ("auto_scaling_group", _GROUP_RE)):
            value = getattr(self, name)
            if value is not None and not (isinstance(value, str) and pattern.match(value)):
                raise ConfigError(f"aws.{name} must be a plain name, got {value!r}")
        if self.region is not None and not (isinstance(self.region, str) and _REGION_RE.match(self.region)):
            raise ConfigError(f"aws.region must look like 'us-east-1', got {self.region!r}")
        p = self.metric_period_seconds
        if not (isinstance(p, int) and not isinstance(p, bool) and p >= 60 and p % 60 == 0):
            raise ConfigError(f"aws.metric_period_seconds must be 60 or a multiple of 60, got {p!r}")
        a = self.max_metric_age_seconds
        if not (isinstance(a, int) and not isinstance(a, bool) and a >= p):
            raise ConfigError(f"aws.max_metric_age_seconds must be a whole number >= the metric period ({p}), got {a!r}")

    def missing_for_monitoring(self) -> list[str]:
        """Settings that must be filled in before AWS can be read."""
        return [f"aws.{k}" for k in ("profile", "region", "auto_scaling_group") if getattr(self, k) is None]


@dataclass(frozen=True)
class ScalixConfig:
    """Validated SCALIX settings. Building one with an unsafe combination raises ConfigError."""

    mode: Mode = Mode.SIMULATION
    live_enabled: bool = False
    aws: AwsSettings = field(default_factory=AwsSettings)

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode):
            raise ConfigError(f"mode must be a Mode, got {self.mode!r}")
        if not isinstance(self.live_enabled, bool):
            raise ConfigError(f"live.enabled must be true or false, got {self.live_enabled!r}")
        if not isinstance(self.aws, AwsSettings):
            raise ConfigError("aws must be an AwsSettings")
        if self.mode is Mode.LIVE and not self.live_enabled:
            raise ConfigError(
                "mode is LIVE but live.enabled is false. LIVE mode can change AWS resources, "
                "so it needs both 'mode: LIVE' and 'live.enabled: true'."
            )
        if self.mode.uses_aws:
            missing = [k for k in ("aws.profile", "aws.region") if getattr(self.aws, k[4:]) is None]
            if missing:
                raise ConfigError(
                    f"mode {self.mode.value} contacts AWS, so it needs {' and '.join(missing)}. Put them in "
                    f"config/scalix.local.yaml (git-ignored); see config/scalix.local.example.yaml."
                )


def parse_mode(value: Any) -> Mode:
    """Turn a config value such as ``"simulation"`` or ``"READ_ONLY"`` into a Mode."""
    if not isinstance(value, str):
        raise ConfigError(f"mode must be text (SIMULATION, READ_ONLY or LIVE), got {value!r}")
    try:
        return Mode(value.strip().upper())
    except ValueError:
        raise ConfigError(f"unknown mode {value!r}; use SIMULATION, READ_ONLY or LIVE") from None


def _section(data: dict, name: str, allowed: set[str]) -> dict:
    section = data.get(name) or {}
    if not isinstance(section, dict):
        raise ConfigError(f"'{name}' must contain key: value pairs")
    unknown = set(section) - allowed
    if unknown:
        raise ConfigError(f"unknown {name} setting(s): {', '.join(sorted(map(str, unknown)))}")
    return section


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

    live = _section(data, "live", _LIVE_KEYS)
    live_enabled = live.get("enabled", False)
    if not isinstance(live_enabled, bool):
        raise ConfigError(f"live.enabled must be true or false (without quotes), got {live_enabled!r}")

    aws_raw = {k: v for k, v in _section(data, "aws", _AWS_KEYS).items() if v is not None}
    aws = AwsSettings(**aws_raw)

    return ScalixConfig(mode=mode, live_enabled=live_enabled, aws=aws)


def _read_yaml(file: Path) -> Any:
    try:
        return yaml.safe_load(file.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ConfigError(f"{file} is not valid YAML: {exc}") from None


def merge_settings(base: Any, override: Any) -> Any:
    """Override wins; sections (dicts) are merged one level deep."""
    if base is None:
        return override
    if override is None:
        return base
    if not isinstance(base, dict) or not isinstance(override, dict):
        raise ConfigError("both config files must contain key: value pairs at the top level")
    merged = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**merged[key], **value}
        else:
            merged[key] = value
    return merged


def load_config(path: str | Path | None = None) -> ScalixConfig:
    """Load and validate the SCALIX settings.

    With no ``path``: reads ``config/scalix.yaml`` merged with the optional, git-ignored
    ``config/scalix.local.yaml``, falling back to safe defaults if neither exists.
    With a ``path``: reads exactly that file, which must exist.
    """
    if path is not None:
        file = Path(path)
        if not file.is_file():
            raise ConfigError(f"config file not found: {file}")
        try:
            return parse_config(_read_yaml(file))
        except ConfigError as exc:
            raise ConfigError(f"{file}: {exc}") from None

    data = _read_yaml(DEFAULT_CONFIG_PATH) if DEFAULT_CONFIG_PATH.is_file() else None
    if LOCAL_CONFIG_PATH.is_file():
        data = merge_settings(data, _read_yaml(LOCAL_CONFIG_PATH))
    try:
        return parse_config(data)
    except ConfigError as exc:
        raise ConfigError(f"config/scalix.yaml + scalix.local.yaml: {exc}") from None
