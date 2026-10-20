"""Strategy C: SCALIX workload-aware scaling.

Each decision interval, SCALIX looks at **utilization** and at the **recent demand history**
(requests per second):

1. **Read the demand signal.**
   *Trend:* is demand rising, falling or stable over the last ``observation_window`` intervals?
   This uses a straight-line fit and catches *sustained* increases.
   *Spike:* did demand jump by at least ``spike_factor`` (default 1.5x) since the previous
   interval? This catches *sudden* jumps, including a rebound after falling demand, where the
   line fit still points down. See :mod:`scalix.algorithms.trend`.
2. **Scale up** when utilization reaches ``upper``, or *earlier*, at ``preemptive_upper``, when
   demand is rising or has just spiked. The step is not always 1: SCALIX sizes the fleet so the
   expected load runs at ``target_utilization``. It counts workers that are still booting, and
   when demand is rising it projects the load ``projection_intervals`` ahead. The step is capped
   by ``max_scale_up_step`` and the maximum capacity.
3. **Scale down cautiously,** one worker at a time, and only when *all* of these hold:
   utilization has been at or below ``lower`` for ``low_intervals_required`` intervals in a row;
   demand is known not to be rising or spiking; the scale-down cooldown has passed; and removing
   a worker would not push the others above ``target_utilization``. That last check prevents
   add/remove flapping.
4. **Cooldowns:** scale-ups wait ``scale_up_cooldown_seconds`` after the last scale-up.
   Scale-downs wait ``scale_down_cooldown_seconds`` after the last action *in either direction*.
5. **Every decision carries a plain-English reason,** including decisions to do nothing.

All numbers are starting values for experiments, not proven optimums. SCALIX is not assumed to
beat the other strategies; the experiments decide that.

Ported from ``ScalixPolicy.java``. Two additions are new in the Python edition, and both can be
switched off to reproduce the Java behaviour exactly:

* spike detection: ``spike_factor=None`` turns it off;
* the sustained-low requirement for scale-down: ``low_intervals_required=1`` turns it off.
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
    fmt,
    Trend,
    check_fraction,
    check_int_at_least,
    check_non_negative,
    check_thresholds,
    limits_parameters,
)
from scalix.algorithms.trend import estimate_trend, spike_ratio

#: Upper bound on projected growth, so one noisy steep slope cannot make a huge step.
MAX_PROJECTED_GROWTH = 2.0


def _printable_slope(slope: float) -> float:
    """Infinite slopes (demand rising from zero) are shown as +/-999 to keep reasons readable."""
    return slope if math.isfinite(slope) else math.copysign(999.0, slope)


def _trailing_low_count(history: tuple[float, ...], lower: float) -> int:
    count = 0
    for value in reversed(history):
        if value > lower:
            break
        count += 1
    return count


@dataclass(frozen=True)
class ScalixPolicy(ScalingPolicy):
    """Workload-aware scaling: utilization + demand trend + spike detection + cautious scale-down."""

    NAME: ClassVar[str] = "SCALIX"

    capacity: CapacityLimits = field(default_factory=CapacityLimits)
    upper: float = 0.70
    lower: float = 0.30
    preemptive_upper: float = 0.55
    target_utilization: float = 0.60
    observation_window: int = 6
    trend_threshold: float = 0.03
    projection_intervals: int = 2
    scale_up_cooldown_seconds: float = 30.0
    scale_down_cooldown_seconds: float = 90.0
    max_scale_up_step: int = 3
    spike_factor: float | None = 1.5
    low_intervals_required: int = 2

    def __post_init__(self) -> None:
        if not isinstance(self.capacity, CapacityLimits):
            raise ValueError("capacity must be a CapacityLimits")
        check_thresholds(self.lower, self.upper)
        check_fraction("preemptive_upper", self.preemptive_upper)
        if not self.lower < self.preemptive_upper <= self.upper:
            raise ValueError(f"preemptive_upper must be within (lower, upper], got {self.preemptive_upper}")
        check_fraction("target_utilization", self.target_utilization)
        if not self.lower < self.target_utilization <= self.upper:
            raise ValueError(f"target_utilization must be within (lower, upper], got {self.target_utilization}")
        check_int_at_least("observation_window", self.observation_window, 2)
        check_non_negative("trend_threshold", self.trend_threshold)
        check_int_at_least("projection_intervals", self.projection_intervals, 0)
        check_non_negative("scale_up_cooldown_seconds", self.scale_up_cooldown_seconds)
        check_non_negative("scale_down_cooldown_seconds", self.scale_down_cooldown_seconds)
        check_int_at_least("max_scale_up_step", self.max_scale_up_step, 1)
        if self.spike_factor is not None:
            check_non_negative("spike_factor", self.spike_factor)
            if not self.spike_factor > 1:
                raise ValueError(f"spike_factor must be > 1 (or None to disable), got {self.spike_factor}")
        check_int_at_least("low_intervals_required", self.low_intervals_required, 1)

    @property
    def limits(self) -> CapacityLimits:
        return self.capacity

    def decide(self, ctx: ScalingContext) -> ScalingDecision:
        est = estimate_trend(ctx.arrival_rate_history, self.observation_window, self.trend_threshold)
        trend, slope = est.trend, est.relative_slope
        ratio = spike_ratio(ctx.arrival_rate_history) if self.spike_factor is not None else None
        spike = ratio is not None and ratio >= self.spike_factor
        u = ctx.utilization
        n = ctx.provisioned

        trend_txt = (f"trend {trend.value} (rel. slope {fmt(_printable_slope(slope), 3, sign=True)}/interval "
                     f"over {est.samples} samples)")
        if spike:
            trend_txt += ("; spike: demand jumped from zero" if math.isinf(ratio)
                          else f"; spike: demand jumped {fmt(ratio, 2)}x since the previous interval")

        def none(reason: str, cooldown_blocked: bool = False) -> ScalingDecision:
            return ScalingDecision.none(reason, trend, slope, cooldown_blocked, spike)

        # ---- scale up -------------------------------------------------------------------
        overloaded = u >= self.upper
        rising_early = (trend is Trend.RISING or spike) and u >= self.preemptive_upper
        if overloaded or rising_early:
            if overloaded:
                why = f"utilization {fmt(u, 3)} >= upper {fmt(self.upper, 2)}"
            elif trend is Trend.RISING:
                why = f"utilization {fmt(u, 3)} >= preemptive {fmt(self.preemptive_upper, 2)} and demand rising"
            else:
                why = f"utilization {fmt(u, 3)} >= preemptive {fmt(self.preemptive_upper, 2)} and demand spiked"
            if n >= self.capacity.maximum:
                return none(f"{why}; already at max {self.capacity.maximum} VMs; {trend_txt}")
            if ctx.time - ctx.last_scale_up_time < self.scale_up_cooldown_seconds:
                return none(f"{why}; scale-up suppressed by cooldown; {trend_txt}", cooldown_blocked=True)
            step = self.scale_up_step(ctx, trend, slope)
            if step <= 0:
                return none(f"{why}; {ctx.booting} booting VM(s) already cover projected demand; {trend_txt}")
            return ScalingDecision.up(step, f"{why}; {trend_txt}", trend, slope, spike)

        # ---- scale down (cautious) ----------------------------------------------------------
        if u <= self.lower:
            why = f"utilization {fmt(u, 3)} <= lower {fmt(self.lower, 2)}"
            if trend in (Trend.RISING, Trend.UNKNOWN):
                return none(f"{why} but demand is {trend.value}; holding; {trend_txt}")
            if spike:
                return none(f"{why} but demand just spiked; holding; {trend_txt}")
            if n <= self.capacity.minimum:
                return none(f"{why}; already at min {self.capacity.minimum} VMs; {trend_txt}")
            if self.low_intervals_required > 1:
                low_so_far = _trailing_low_count(ctx.utilization_history, self.lower)
                if low_so_far < self.low_intervals_required:
                    return none(f"{why}; waiting until utilization has been <= {fmt(self.lower, 2)} for "
                                f"{self.low_intervals_required} intervals in a row ({low_so_far} so far); "
                                f"{trend_txt}")
            since_last_action = ctx.time - max(ctx.last_scale_up_time, ctx.last_scale_down_time)
            if since_last_action < self.scale_down_cooldown_seconds:
                return none(f"{why}; scale-down suppressed by cooldown; {trend_txt}", cooldown_blocked=True)
            if ctx.booting == 0:
                if ctx.active <= 1:
                    return none(f"{why}; only one serving VM; {trend_txt}")
                projected = u * ctx.active / (ctx.active - 1)
                if projected > self.target_utilization:
                    return none(f"{why}; removing a VM would raise utilization to {fmt(projected, 3)} > "
                                f"target {fmt(self.target_utilization, 2)}; {trend_txt}")
            return ScalingDecision.down(1, f"{why}; {trend_txt}", trend, slope, spike)

        return none(f"utilization {fmt(u, 3)} within band; {trend_txt}")

    def scale_up_step(self, ctx: ScalingContext, trend: Trend, slope: float) -> int:
        """Number of workers to add.

        Busy capacity is ``active * utilization`` (in workers). When demand is RISING it is
        projected ``projection_intervals`` ahead along the trend, capped at 2x. The fleet,
        including workers still booting, is sized so that load runs at ``target_utilization``.
        When utilization is already at or above ``upper`` the step is at least 1, because a
        saturated CPU hides how much demand there really is.
        """
        busy = ctx.active * ctx.utilization
        if trend is Trend.RISING:
            growth = (MAX_PROJECTED_GROWTH if math.isinf(slope)
                      else min(MAX_PROJECTED_GROWTH, 1 + slope * self.projection_intervals))
            busy *= growth
        desired = math.ceil(busy / self.target_utilization - 1e-9)
        step = desired - ctx.provisioned
        if ctx.utilization >= self.upper:
            step = max(step, 1)
        step = min(step, self.max_scale_up_step)
        step = min(step, self.capacity.maximum - ctx.provisioned)
        return step

    def parameters(self) -> dict[str, Any]:
        return {
            **limits_parameters(self.capacity),
            "upper": self.upper,
            "lower": self.lower,
            "preemptive_upper": self.preemptive_upper,
            "target_utilization": self.target_utilization,
            "observation_window": self.observation_window,
            "trend_threshold": self.trend_threshold,
            "projection_intervals": self.projection_intervals,
            "scale_up_cooldown_seconds": self.scale_up_cooldown_seconds,
            "scale_down_cooldown_seconds": self.scale_down_cooldown_seconds,
            "max_scale_up_step": self.max_scale_up_step,
            "spike_factor": self.spike_factor,
            "low_intervals_required": self.low_intervals_required,
        }
