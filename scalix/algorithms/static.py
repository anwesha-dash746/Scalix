"""Strategy A: static allocation, the baseline.

Runs the same number of workers for the whole experiment and ignores demand. It shows what
happens with no auto-scaling at all: cheap and predictable when the fixed size is right, slow
when demand outgrows it, and wasteful when demand is low.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from scalix.algorithms.base import (
    CapacityLimits,
    ScalingContext,
    ScalingDecision,
    ScalingPolicy,
    check_int_at_least,
)


@dataclass(frozen=True)
class StaticPolicy(ScalingPolicy):
    """Keep exactly ``worker_count`` workers. Never scales."""

    NAME: ClassVar[str] = "STATIC"

    worker_count: int = 8

    def __post_init__(self) -> None:
        check_int_at_least("worker_count", self.worker_count, 1)

    @property
    def limits(self) -> CapacityLimits:
        return CapacityLimits(self.worker_count, self.worker_count, self.worker_count)

    def decide(self, ctx: ScalingContext) -> ScalingDecision:
        return ScalingDecision.none(
            f"static allocation: fleet size is fixed at {self.worker_count}")

    def parameters(self) -> dict[str, Any]:
        return {"worker_count": self.worker_count}
