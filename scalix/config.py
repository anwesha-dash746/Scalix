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

KILL_SWITCH_PATH = PROJECT_ROOT / "config" / "LIVE_DISABLED"
KILL_SWITCH_ENV = "SCALIX_LIVE_DISABLED"

_TOP_LEVEL_KEYS = {"mode", "live", "aws", "controller"}
_LIVE_KEYS = {"enabled", "allowed_groups", "max_capacity", "max_step", "min_seconds_between_actions"}
_CONTROLLER_KEYS = {"policy", "policy_settings", "decision_interval_seconds", "demand_signal"}
_DEMAND_SIGNALS = ("CPU", "NONE")
_POLICY_NAMES = ("STATIC", "THRESHOLD", "SCALIX")
_CAPACITY_KEYS = {"capacity", "min_capacity", "initial_capacity", "max_capacity", "worker_count"}
_AWS_KEYS = {"profile", "region", "auto_scaling_group", "metric_period_seconds", "max_metric_age_seconds",
             "budget_usd"}
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
    #: The AWS spending limit for this project, in USD (e.g. the AWS Academy lab credit). Shown on the
    #: dashboard and used for cost reviews. SCALIX never spends money by itself; this is a reminder of
    #: the ceiling every approved action must stay under, not an AWS budget alarm.
    budget_usd: float | None = None

    def __post_init__(self) -> None:
        b = self.budget_usd
        if b is not None and (isinstance(b, bool) or not isinstance(b, (int, float)) or not 0 < b <= 10_000):
            raise ConfigError(f"aws.budget_usd must be a number above 0 (USD), got {b!r}")
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


def _whole(name: str, value: Any, low: int, high: int | None = None) -> None:
    if isinstance(value, bool) or not isinstance(value, int) or value < low or (high is not None and value > high):
        bound = f"from {low} to {high}" if high is not None else f">= {low}"
        raise ConfigError(f"{name} must be a whole number {bound}, got {value!r}")


@dataclass(frozen=True)
class LiveSettings:
    """Gates for LIVE mode. All of them are checked again by the controller before any write."""

    enabled: bool = False
    #: Exact names of the only Auto Scaling groups SCALIX may ever change. Empty = none.
    allowed_groups: tuple[str, ...] = ()
    #: Approved maximum number of workers. SCALIX never sets desired capacity above this, even if
    #: the group's own MaxSize is higher. Default 2; raising it needs your explicit approval.
    max_capacity: int = 2
    #: Largest change in desired capacity per action (a bigger proposal is reduced to this).
    max_step: int = 1
    #: Minimum time between two capacity changes made by SCALIX, whatever the policy says.
    min_seconds_between_actions: int = 300

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ConfigError(f"live.enabled must be true or false (without quotes), got {self.enabled!r}")
        groups = self.allowed_groups
        if isinstance(groups, str) or not isinstance(groups, (list, tuple)):
            raise ConfigError("live.allowed_groups must be a list of exact group names, e.g. [scalix-workers]")
        for g in groups:
            if isinstance(g, str) and any(ch in g for ch in "*?[]"):
                raise ConfigError(f"live.allowed_groups must list exact names, not patterns: {g!r}")
            if not (isinstance(g, str) and _GROUP_RE.match(g)):
                raise ConfigError(f"live.allowed_groups contains an invalid name: {g!r}")
        object.__setattr__(self, "allowed_groups", tuple(groups))
        _whole("live.max_capacity", self.max_capacity, 1, 20)
        _whole("live.max_step", self.max_step, 1, 5)
        _whole("live.min_seconds_between_actions", self.min_seconds_between_actions, 60)


@dataclass(frozen=True)
class ControllerSettings:
    """Which policy the AWS controller uses, and how often it decides."""

    #: THRESHOLD by default: it needs CPU only. SCALIX can be chosen too; it then gets the
    #: CPU-derived demand signal below (Phase 7). See docs/CONTROLLER.md.
    policy: str = "THRESHOLD"
    #: Extra policy settings (thresholds, cooldowns...). Capacity is set by the controller, not here.
    policy_settings: dict = field(default_factory=dict)
    #: Seconds between decisions in --watch mode. Matches CloudWatch's 5-minute basic monitoring.
    decision_interval_seconds: int = 300
    #: Demand signal for SCALIX's trend and spike checks. CPU: CPU utilization x in-service
    #: instances (busy-instance equivalents), free and needs no load balancer. NONE: no signal, so
    #: SCALIX's trend stays UNKNOWN (the Phase 4 behaviour).
    demand_signal: str = "CPU"

    def __post_init__(self) -> None:
        if not (isinstance(self.policy, str) and self.policy.strip().upper() in _POLICY_NAMES):
            raise ConfigError(f"controller.policy must be one of {', '.join(_POLICY_NAMES)}, got {self.policy!r}")
        object.__setattr__(self, "policy", self.policy.strip().upper())
        if not isinstance(self.policy_settings, dict):
            raise ConfigError("controller.policy_settings must contain key: value pairs")
        bad = _CAPACITY_KEYS & set(self.policy_settings)
        if bad:
            raise ConfigError(f"controller.policy_settings may not set capacity ({', '.join(sorted(bad))}); "
                              "the controller takes it from the group and live.max_capacity")
        _whole("controller.decision_interval_seconds", self.decision_interval_seconds, 60)
        if not (isinstance(self.demand_signal, str) and self.demand_signal.strip().upper() in _DEMAND_SIGNALS):
            raise ConfigError(f"controller.demand_signal must be one of {', '.join(_DEMAND_SIGNALS)}, "
                              f"got {self.demand_signal!r}")
        object.__setattr__(self, "demand_signal", self.demand_signal.strip().upper())


@dataclass(frozen=True)
class ScalixConfig:
    """Validated SCALIX settings. Building one with an unsafe combination raises ConfigError."""

    mode: Mode = Mode.SIMULATION
    live: LiveSettings = field(default_factory=LiveSettings)
    aws: AwsSettings = field(default_factory=AwsSettings)
    controller: ControllerSettings = field(default_factory=ControllerSettings)

    @property
    def live_enabled(self) -> bool:
        return self.live.enabled

    def __post_init__(self) -> None:
        if not isinstance(self.mode, Mode):
            raise ConfigError(f"mode must be a Mode, got {self.mode!r}")
        for name, cls in (("live", LiveSettings), ("aws", AwsSettings), ("controller", ControllerSettings)):
            if not isinstance(getattr(self, name), cls):
                raise ConfigError(f"{name} must be a {cls.__name__}")
        if self.mode is Mode.LIVE and not self.live.enabled:
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
        if self.mode is Mode.LIVE:
            group = self.aws.auto_scaling_group
            if not group:
                raise ConfigError("mode LIVE needs aws.auto_scaling_group (the one group to control)")
            if group not in self.live.allowed_groups:
                raise ConfigError(f"mode LIVE: group {group!r} is not in live.allowed_groups; add its exact "
                                  "name there only after you have approved SCALIX controlling it")


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

    live_raw = {k: v for k, v in _section(data, "live", _LIVE_KEYS).items() if v is not None}
    live = LiveSettings(**live_raw)

    aws_raw = {k: v for k, v in _section(data, "aws", _AWS_KEYS).items() if v is not None}
    aws = AwsSettings(**aws_raw)

    controller_raw = {k: v for k, v in _section(data, "controller", _CONTROLLER_KEYS).items() if v is not None}
    controller = ControllerSettings(**controller_raw)

    return ScalixConfig(mode=mode, live=live, aws=aws, controller=controller)


def kill_switch_active(path: Path | None = None, environ: dict | None = None) -> str | None:
    """The global live-action disable. Returns why live actions are disabled, or None.

    Either of these switches LIVE writes off, whatever the config says:
    * the file ``config/LIVE_DISABLED`` exists (create it with ``touch config/LIVE_DISABLED``);
    * the environment variable ``SCALIX_LIVE_DISABLED`` is set to anything except 0/false/no.
    Environment variables can only ever *disable* live actions, never enable them.
    """
    import os

    file = KILL_SWITCH_PATH if path is None else Path(path)
    env = os.environ if environ is None else environ
    if file.exists():
        return f"kill switch file {file.name} exists"
    value = str(env.get(KILL_SWITCH_ENV, "")).strip().lower()
    if value and value not in ("0", "false", "no"):
        return f"environment variable {KILL_SWITCH_ENV} is set"
    return None


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
