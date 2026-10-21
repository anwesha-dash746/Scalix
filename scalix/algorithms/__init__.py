"""Scaling policies: STATIC, THRESHOLD and SCALIX, plus TARGET_TRACKING (a model of AWS's own policy).

Each policy is a pure function of a :class:`ScalingContext` (a measurement snapshot), with no
clock, network or AWS access. The same code can therefore drive the simulator and, later, the
optional AWS controller. Ported from the Java engine (``src/main/java/com/scalix/scaling``).

    >>> from scalix.algorithms import create_policy, ScalingContext
    >>> policy = create_policy("THRESHOLD")
    >>> policy.decide(ScalingContext(time=300, provisioned=4, active=4, booting=0,
    ...                              utilization=0.85)).action.value
    'SCALE_UP'
"""

from scalix.algorithms.base import (
    Action,
    CapacityLimits,
    ScalingContext,
    ScalingDecision,
    ScalingPolicy,
    Trend,
)
from scalix.algorithms.factory import POLICY_NAMES, create_policy
from scalix.algorithms.scalix_policy import ScalixPolicy
from scalix.algorithms.static import StaticPolicy
from scalix.algorithms.target_tracking import TargetTrackingPolicy
from scalix.algorithms.threshold import ThresholdPolicy
from scalix.algorithms.trend import TrendEstimate, estimate_trend, spike_ratio

__all__ = [
    "Action",
    "CapacityLimits",
    "POLICY_NAMES",
    "ScalingContext",
    "ScalingDecision",
    "ScalingPolicy",
    "ScalixPolicy",
    "StaticPolicy",
    "TargetTrackingPolicy",
    "ThresholdPolicy",
    "Trend",
    "TrendEstimate",
    "create_policy",
    "estimate_trend",
    "spike_ratio",
]
