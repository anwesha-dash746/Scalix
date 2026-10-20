"""Strategy B: classic reactive threshold scaling.

Each decision interval:

* utilization **above** ``upper`` (default 0.70): add 1 worker;
* utilization **below** ``lower`` (default 0.30): remove 1 worker;
* otherwise: do nothing.

It always stays within the capacity limits. After any change it waits ``cooldown_seconds`` before
the next one, in either direction. Without the cooldown it would keep adding workers while the
new ones are still booting and the measured utilization is still high. Cloud auto-scalers use a
cooldown for the same reason.

The thresholds are starting values for experiments, not proven optimums.

Ported from ``ThresholdPolicy.java``. Difference: the default cooldown here is 60 s, where the Java
default was 0 (no cooldown). The project brief asks for cooldowns on this strategy, and a baseline
without one would be an unfairly weak comparison. Set ``cooldown_seconds=0`` for the original
behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from scalix.algorithms.base import (
    CapacityLimits,
    ScalingContext,
    ScalingDecision,
    ScalingPolicy,
    fmt,
    Trend,
    check_non_negative,
    check_thresholds,
    limits_parameters,
)


@dataclass(frozen=True)
class ThresholdPolicy(ScalingPolicy):
    """Add one worker above ``upper``, remove one below ``lower``, with a shared cooldown."""

    NAME: ClassVar[str] = "THRESHOLD"

    capacity: CapacityLimits = field(default_factory=CapacityLimits)
    upper: float = 0.70
    lower: float = 0.30
    cooldown_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.capacity, CapacityLimits):
            raise ValueError("capacity must be a CapacityLimits")
        check_thresholds(self.lower, self.upper)
        check_non_negative("cooldown_seconds", self.cooldown_seconds)

    @property
    def limits(self) -> CapacityLimits:
        return self.capacity

    def decide(self, ctx: ScalingContext) -> ScalingDecision:
        u = ctx.utilization
        n = ctx.provisioned
        last_action = max(ctx.last_scale_up_time, ctx.last_scale_down_time)
        in_cooldown = ctx.time - last_action < self.cooldown_seconds

        if u > self.upper:
            if n >= self.capacity.maximum:
                return ScalingDecision.none(
                    f"utilization {fmt(u, 3)} > upper {fmt(self.upper, 2)} but already at max "
                    f"{self.capacity.maximum} VMs")
            if in_cooldown:
                return ScalingDecision.none(
                    f"utilization {fmt(u, 3)} > upper {fmt(self.upper, 2)} but in cooldown",
                    Trend.UNKNOWN, 0.0, cooldown_blocked=True)
            return ScalingDecision.up(1, f"utilization {fmt(u, 3)} > upper {fmt(self.upper, 2)}")

        if u < self.lower:
            if n <= self.capacity.minimum:
                return ScalingDecision.none(
                    f"utilization {fmt(u, 3)} < lower {fmt(self.lower, 2)} but already at min "
                    f"{self.capacity.minimum} VMs")
            if in_cooldown:
                return ScalingDecision.none(
                    f"utilization {fmt(u, 3)} < lower {fmt(self.lower, 2)} but in cooldown",
                    Trend.UNKNOWN, 0.0, cooldown_blocked=True)
            return ScalingDecision.down(1, f"utilization {fmt(u, 3)} < lower {fmt(self.lower, 2)}")

        return ScalingDecision.none(
            f"utilization {fmt(u, 3)} within [{fmt(self.lower, 2)}, {fmt(self.upper, 2)}]")

    def parameters(self) -> dict[str, Any]:
        return {**limits_parameters(self.capacity), "upper": self.upper, "lower": self.lower,
                "cooldown_seconds": self.cooldown_seconds}
