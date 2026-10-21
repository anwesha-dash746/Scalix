"""Build a policy from its name and settings, for example from a config file or the dashboard."""

from __future__ import annotations

from typing import Any

from scalix.algorithms.base import CapacityLimits, ScalingPolicy
from scalix.algorithms.scalix_policy import ScalixPolicy
from scalix.algorithms.static import StaticPolicy
from scalix.algorithms.target_tracking import TargetTrackingPolicy
from scalix.algorithms.threshold import ThresholdPolicy

POLICIES: dict[str, type[ScalingPolicy]] = {
    StaticPolicy.NAME: StaticPolicy,
    ThresholdPolicy.NAME: ThresholdPolicy,
    ScalixPolicy.NAME: ScalixPolicy,
    TargetTrackingPolicy.NAME: TargetTrackingPolicy,
}
POLICY_NAMES = tuple(POLICIES)

_LIMIT_KEYS = ("min_capacity", "initial_capacity", "max_capacity")


def create_policy(name: str, **settings: Any) -> ScalingPolicy:
    """Create a policy by name (case-insensitive) with optional settings.

    Capacity can be given as ``capacity=CapacityLimits(...)`` or as the flat keys
    ``min_capacity`` / ``initial_capacity`` / ``max_capacity`` (the same keys that
    ``parameters()`` returns), so a policy's recorded parameters can rebuild it exactly.
    Unknown settings raise ValueError instead of being silently ignored.

    >>> create_policy("threshold", upper=0.8).parameters()["upper"]
    0.8
    """
    key = str(name).strip().upper()
    if key not in POLICIES:
        raise ValueError(f"unknown policy {name!r}; valid: {', '.join(POLICY_NAMES)}")
    cls = POLICIES[key]
    settings = dict(settings)

    flat = {k: settings.pop(k) for k in _LIMIT_KEYS if k in settings}
    if flat:
        if cls is StaticPolicy:
            raise ValueError("STATIC takes worker_count, not min/initial/max capacity")
        if "capacity" in settings:
            raise ValueError("give either capacity=... or min/initial/max_capacity, not both")
        defaults = CapacityLimits()
        settings["capacity"] = CapacityLimits(
            flat.get("min_capacity", defaults.minimum),
            flat.get("initial_capacity", defaults.initial),
            flat.get("max_capacity", defaults.maximum),
        )
    try:
        return cls(**settings)
    except TypeError as exc:
        raise ValueError(f"invalid setting for {key}: {exc}") from None
