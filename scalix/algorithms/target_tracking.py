"""A model of AWS EC2 Auto Scaling's *target tracking* policy, for comparison in the simulator.

Why: the obvious question about SCALIX is "why not just use what AWS already offers?" AWS's
recommended policy for CPU is target tracking ("keep average CPU at 50%"). This class models how
it behaves so it can run on the same simulated traces as the other strategies. It is a **model
written from AWS's public documentation, not AWS's code**; where AWS doesn't document a detail,
the choice below is stated.

What AWS documents (EC2 Auto Scaling user guide, "Target tracking scaling policies"):

* AWS creates CloudWatch alarms and "calculates the scaling adjustment based on the metric and the
  target value", rounding conservatively: it rounds *up* when adding (1.5 instances -> 2).
* Scale-out happens when the metric is above the target. Scale-in is deliberately gradual: AWS
  removes capacity "only when utilization passes a threshold that is far enough below the target
  value (usually more than 10% lower)".
* While new instances warm up they don't count towards the group's metric, they *do* count as
  desired capacity for the next scale-out, and all scale-in is blocked until they finish warming
  up. The warm-up defaults to the group's default instance warmup, else its default cooldown
  (300 s unless changed).

What an AWS maintainer stated (aws/containers-roadmap issue 2113): target tracking "utilizes 3 data
points for scale-out" and "15 data points for scale-in", with one-minute data points.

How this model implements it, per decision:

1. Group the utilization history into data points of ``datapoint_seconds`` (default 60 s, i.e. 4
   simulator intervals of 15 s; set 300 s to mimic free 5-minute monitoring).
2. **Scale out** when the last ``scale_out_datapoints`` (3) data points are all above the target:
   new capacity = ceil(busy instances / target), where busy = serving instances x latest
   utilization. Instances still starting count as capacity already added.
3. **Scale in** when the last ``scale_in_datapoints`` (15) data points are all below
   ``scale_in_ratio`` (0.9) x target, nothing is warming up, the last scale-up is at least
   ``warmup_seconds`` ago and the last scale-in at least one data point ago. New capacity =
   ceil(busy / target), so it removes as few instances as possible.

Not modelled: alarms' INSUFFICIENT_DATA state, CloudWatch's reporting delay, predictive scaling,
and the exact (undocumented) scale-in threshold.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Any, ClassVar

from scalix.algorithms.base import (
    CapacityLimits,
    ScalingContext,
    ScalingDecision,
    ScalingPolicy,
    check_fraction,
    check_int_at_least,
    check_non_negative,
    fmt,
    limits_parameters,
)


@dataclass(frozen=True)
class TargetTrackingPolicy(ScalingPolicy):
    NAME: ClassVar[str] = "TARGET_TRACKING"

    capacity: CapacityLimits = field(default_factory=CapacityLimits)
    target_utilization: float = 0.50
    interval_seconds: float = 15.0      # how often the policy is asked; must match the simulator's decisions
    datapoint_seconds: float = 60.0     # CloudWatch data-point length (60 = detailed monitoring)
    scale_out_datapoints: int = 3
    scale_in_datapoints: int = 15
    scale_in_ratio: float = 0.9
    warmup_seconds: float = 300.0

    def __post_init__(self) -> None:
        if not isinstance(self.capacity, CapacityLimits):
            raise ValueError("capacity must be a CapacityLimits")
        check_fraction("target_utilization", self.target_utilization)
        if not 0 < self.target_utilization < 1:
            raise ValueError(f"target_utilization must be between 0 and 1, got {self.target_utilization}")
        for name in ("interval_seconds", "datapoint_seconds"):
            check_non_negative(name, getattr(self, name))
            if getattr(self, name) <= 0:
                raise ValueError(f"{name} must be > 0")
        if self.datapoint_seconds < self.interval_seconds - 1e-9:
            raise ValueError("datapoint_seconds must be at least interval_seconds")
        check_int_at_least("scale_out_datapoints", self.scale_out_datapoints, 1)
        check_int_at_least("scale_in_datapoints", self.scale_in_datapoints, 1)
        check_fraction("scale_in_ratio", self.scale_in_ratio)
        if not 0 < self.scale_in_ratio <= 1:
            raise ValueError(f"scale_in_ratio must be within (0, 1], got {self.scale_in_ratio}")
        check_non_negative("warmup_seconds", self.warmup_seconds)

    @property
    def limits(self) -> CapacityLimits:
        return self.capacity

    @property
    def samples_per_datapoint(self) -> int:
        return max(1, round(self.datapoint_seconds / self.interval_seconds))

    def datapoints(self, history, count: int) -> list[float] | None:
        """The last ``count`` data points (averages of consecutive samples), oldest first, or None if too short."""
        k = self.samples_per_datapoint
        if len(history) < count * k:
            return None
        recent = list(history)[-count * k:]
        return [sum(recent[i * k:(i + 1) * k]) / k for i in range(count)]

    def decide(self, ctx: ScalingContext) -> ScalingDecision:
        target = self.target_utilization
        history = tuple(ctx.utilization_history) or (ctx.utilization,)
        n = ctx.provisioned
        k = self.samples_per_datapoint
        latest = self.datapoints(history, 1)[0] if len(history) >= k else ctx.utilization
        busy = ctx.active * latest
        wanted = max(self.capacity.minimum, math.ceil(busy / target - 1e-9))

        high = self.datapoints(history, self.scale_out_datapoints)
        if high is not None and all(v > target for v in high):
            why = (f"last {self.scale_out_datapoints} data point(s) above target {fmt(target, 2)} "
                   f"(latest {fmt(latest, 3)})")
            if n >= self.capacity.maximum:
                return ScalingDecision.none(f"{why}; already at max {self.capacity.maximum} VMs")
            step = min(wanted, self.capacity.maximum) - n
            if step <= 0:
                return ScalingDecision.none(f"{why}; {ctx.booting} warming VM(s) already cover it")
            return ScalingDecision.up(step, f"{why}; capacity for target: ceil({fmt(busy, 2)} / {fmt(target, 2)}) "
                                            f"= {wanted}")

        low_threshold = self.scale_in_ratio * target
        low = self.datapoints(history, self.scale_in_datapoints)
        if low is not None and all(v < low_threshold for v in low):
            why = (f"last {self.scale_in_datapoints} data point(s) below {fmt(low_threshold, 3)} "
                   f"({fmt(self.scale_in_ratio, 2)} x target)")
            if n <= self.capacity.minimum:
                return ScalingDecision.none(f"{why}; already at min {self.capacity.minimum} VMs")
            if ctx.booting > 0 or ctx.time - ctx.last_scale_up_time < self.warmup_seconds:
                return ScalingDecision.none(f"{why}; scale-in blocked while new VMs warm up",
                                            cooldown_blocked=True)
            if ctx.time - ctx.last_scale_down_time < self.datapoint_seconds:
                return ScalingDecision.none(f"{why}; waiting for a new data point after the last scale-in",
                                            cooldown_blocked=True)
            step = n - wanted
            if step <= 0:
                return ScalingDecision.none(f"{why}; removing a VM would push utilization above target")
            return ScalingDecision.down(step, f"{why}; capacity for target: ceil({fmt(busy, 2)} / {fmt(target, 2)}) "
                                              f"= {wanted}")

        return ScalingDecision.none(f"latest data point {fmt(latest, 3)}; no alarm (target {fmt(target, 2)})")

    def parameters(self) -> dict[str, Any]:
        return {**limits_parameters(self.capacity), "target_utilization": self.target_utilization,
                "interval_seconds": self.interval_seconds, "datapoint_seconds": self.datapoint_seconds,
                "scale_out_datapoints": self.scale_out_datapoints, "scale_in_datapoints": self.scale_in_datapoints,
                "scale_in_ratio": self.scale_in_ratio, "warmup_seconds": self.warmup_seconds}
