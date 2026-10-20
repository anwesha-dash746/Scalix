"""Shared types for every scaling policy.

A policy is a *pure function*: it receives a :class:`ScalingContext` (a snapshot of measurements)
and returns a :class:`ScalingDecision`. It never reads a clock, never sleeps, never calls AWS and
never changes anything itself. Whoever calls it (the simulator, or later the AWS controller)
applies the decision. Pure functions are easy to test and the same code can drive both the
simulator and a real cloud.

Ported from the Java engine (``src/main/java/com/scalix/scaling``), which remains as a reference.
"""

from __future__ import annotations

import math
from abc import ABC, abstractmethod
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal
from enum import Enum
from typing import Any, ClassVar, Sequence


class Action(str, Enum):
    """What a policy wants to do this decision interval."""

    SCALE_UP = "SCALE_UP"
    SCALE_DOWN = "SCALE_DOWN"
    NONE = "NONE"


class Trend(str, Enum):
    """Direction of recent demand, estimated by :mod:`scalix.algorithms.trend`."""

    RISING = "RISING"
    FALLING = "FALLING"
    STABLE = "STABLE"
    UNKNOWN = "UNKNOWN"  # not enough history to estimate a trend


def fmt(value: float, places: int, sign: bool = False) -> str:
    """Format a number for a decision reason, rounding halves *up* (0.5625 -> "0.563").

    Python's own formatting rounds halves to even (0.5625 -> "0.562"). Rounding half up matches
    what people expect and what the Java engine wrote, so reasons are identical in both.
    """
    if not math.isfinite(value):
        return f"{value:+}" if sign else f"{value}"
    exact = Decimal(repr(float(value))).quantize(Decimal(1).scaleb(-places), rounding=ROUND_HALF_UP)
    return format(exact, f"{'+' if sign else ''}.{places}f")


def _is_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def check_fraction(name: str, value: float) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and 0 <= value <= 1):
        raise ValueError(f"{name} must be within [0, 1], got {value!r}")


def check_thresholds(lower: float, upper: float) -> None:
    check_fraction("lower threshold", lower)
    check_fraction("upper threshold", upper)
    if not lower < upper:
        raise ValueError(f"lower threshold ({lower}) must be < upper threshold ({upper})")


def check_non_negative(name: str, value: float) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value >= 0):
        raise ValueError(f"{name} must be finite and >= 0, got {value!r}")


def check_int_at_least(name: str, value: int, minimum: int) -> None:
    if not (_is_int(value) and value >= minimum):
        raise ValueError(f"{name} must be a whole number >= {minimum}, got {value!r}")


@dataclass(frozen=True)
class CapacityLimits:
    """Bounds on the number of workers (VMs). ``minimum`` is at least 1: the last worker is never removed.

    These play the same role as an EC2 Auto Scaling group's min / desired / max sizes.
    """

    minimum: int = 1
    initial: int = 8
    maximum: int = 20

    def __post_init__(self) -> None:
        check_int_at_least("minimum capacity", self.minimum, 1)
        check_int_at_least("maximum capacity", self.maximum, self.minimum)
        if not (_is_int(self.initial) and self.minimum <= self.initial <= self.maximum):
            raise ValueError(f"initial capacity {self.initial!r} must be within "
                             f"[{self.minimum}, {self.maximum}]")

    def clamp(self, count: int) -> int:
        return max(self.minimum, min(self.maximum, count))


@dataclass(frozen=True)
class ScalingContext:
    """Everything a policy may look at when deciding. Built by the caller from measurements.

    Attributes:
        time: current time in seconds (simulated time in the simulator).
        provisioned: workers counted against the limits: active + booting (draining excluded).
        active: workers currently serving traffic.
        booting: workers requested but not serving yet.
        utilization: mean CPU utilization of active workers over the last interval, in [0, 1].
        utilization_history: per-interval utilization, oldest first, current interval last.
        arrival_rate_history: per-interval demand (requests/s), oldest first, current last.
        last_scale_up_time: time of the last applied scale-up, or -inf if none yet.
        last_scale_down_time: time of the last applied scale-down, or -inf if none yet.
    """

    time: float
    provisioned: int
    active: int
    booting: int
    utilization: float
    utilization_history: Sequence[float] = ()
    arrival_rate_history: Sequence[float] = ()
    last_scale_up_time: float = -math.inf
    last_scale_down_time: float = -math.inf

    def __post_init__(self) -> None:
        if not (isinstance(self.time, (int, float)) and math.isfinite(self.time) and self.time >= 0):
            raise ValueError(f"time must be finite and >= 0, got {self.time!r}")
        for name in ("provisioned", "active", "booting"):
            value = getattr(self, name)
            if not (_is_int(value) and value >= 0):
                raise ValueError(f"{name} must be a whole number >= 0, got {value!r}")
        u = self.utilization
        if not (isinstance(u, (int, float)) and not math.isnan(u) and 0 <= u <= 1.0 + 1e-9):
            raise ValueError(f"utilization must be within [0, 1], got {u!r}")
        for name in ("last_scale_up_time", "last_scale_down_time"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or math.isnan(value):
                raise ValueError(f"{name} must be a number (-inf if never), got {value!r}")
        object.__setattr__(self, "utilization_history", tuple(self.utilization_history))
        object.__setattr__(self, "arrival_rate_history", tuple(self.arrival_rate_history))


@dataclass(frozen=True)
class ScalingDecision:
    """A policy's answer for one decision interval.

    Attributes:
        action: SCALE_UP, SCALE_DOWN or NONE.
        delta: workers to add (positive) or remove (negative); 0 for NONE.
        reason: human-readable justification, written to the decision log.
        trend: demand trend the policy observed (UNKNOWN if it ignores trends).
        relative_slope: the slope behind ``trend`` (0 if unused).
        cooldown_blocked: True if the policy wanted to act but a cooldown stopped it.
        spike: True if SCALIX saw a sudden jump in demand this interval.
    """

    action: Action
    delta: int
    reason: str
    trend: Trend = Trend.UNKNOWN
    relative_slope: float = 0.0
    cooldown_blocked: bool = False
    spike: bool = False

    def __post_init__(self) -> None:
        if not _is_int(self.delta):
            raise ValueError(f"delta must be a whole number, got {self.delta!r}")
        if self.action is Action.SCALE_UP and self.delta <= 0:
            raise ValueError("SCALE_UP needs a positive delta")
        if self.action is Action.SCALE_DOWN and self.delta >= 0:
            raise ValueError("SCALE_DOWN needs a negative delta")
        if self.action is Action.NONE and self.delta != 0:
            raise ValueError("NONE must have delta 0")
        if not self.reason:
            raise ValueError("every decision needs a reason")

    @classmethod
    def none(cls, reason: str, trend: Trend = Trend.UNKNOWN, slope: float = 0.0,
             cooldown_blocked: bool = False, spike: bool = False) -> "ScalingDecision":
        return cls(Action.NONE, 0, reason, trend, slope, cooldown_blocked, spike)

    @classmethod
    def up(cls, workers: int, reason: str, trend: Trend = Trend.UNKNOWN, slope: float = 0.0,
           spike: bool = False) -> "ScalingDecision":
        return cls(Action.SCALE_UP, workers, reason, trend, slope, False, spike)

    @classmethod
    def down(cls, workers: int, reason: str, trend: Trend = Trend.UNKNOWN, slope: float = 0.0,
             spike: bool = False) -> "ScalingDecision":
        return cls(Action.SCALE_DOWN, -workers, reason, trend, slope, False, spike)


class ScalingPolicy(ABC):
    """Common interface for every strategy, so all of them run under identical conditions.

    Implementations must be deterministic functions of the context plus their own settings, and
    must never return a decision that would leave the worker count outside :attr:`limits`.
    """

    NAME: ClassVar[str]

    @property
    def name(self) -> str:
        """Short identifier used in result files, e.g. ``"STATIC"``."""
        return self.NAME

    @property
    @abstractmethod
    def limits(self) -> CapacityLimits:
        """Worker-count bounds, including the count a run starts with."""

    @abstractmethod
    def decide(self, ctx: ScalingContext) -> ScalingDecision:
        """Evaluate one decision interval."""

    @abstractmethod
    def parameters(self) -> dict[str, Any]:
        """The policy's settings, recorded with every run."""


def limits_parameters(limits: CapacityLimits) -> dict[str, Any]:
    return {"min_capacity": limits.minimum, "initial_capacity": limits.initial,
            "max_capacity": limits.maximum}
