"""Demand-trend and spike detection used by the SCALIX policy.

Two separate signals:

* **Sustained trend.** Fit a straight line (ordinary least squares) through the last ``window``
  demand samples. Divide its slope by the mean of those samples to get a *relative* slope: the
  fractional change per interval. One threshold then works for small and large workloads.
  The trend is RISING above ``+threshold``, FALLING below ``-threshold``, STABLE otherwise, and
  UNKNOWN with fewer than 2 samples.
* **Sudden spike.** Compare the newest sample with the one just before it. A ratio of at least
  ``spike_factor`` (for example 1.5, meaning demand jumped 50% or more in one interval) counts
  as a spike. A steady ramp (8 -> 9 requests/s is only 1.125x) is not a spike. This catches
  jumps that the line fit can miss, for example a sudden rebound after a period of falling
  demand, where the fitted line still points down.

Ported from ``TrendAnalyzer.java`` (the sustained trend). Spike detection is new in the Python
edition.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

from scalix.algorithms.base import Trend

_EPS = 1e-12


@dataclass(frozen=True)
class TrendEstimate:
    trend: Trend
    relative_slope: float
    samples: int


def _window(series: Sequence[float], window: int) -> list[float]:
    values = list(series[-window:]) if series else []
    for v in values:
        if not isinstance(v, (int, float)) or not math.isfinite(v):
            raise ValueError(f"demand series contains a non-finite value: {v!r}")
    return values


def relative_slope(values: Sequence[float]) -> float:
    """Least-squares slope per sample divided by the mean of the samples.

    If the mean is zero, the result is 0 for a flat series and +/-infinity otherwise.
    """
    n = len(values)
    if n < 2:
        raise ValueError("relative_slope needs at least 2 values")
    x_mean = (n - 1) / 2.0
    y_mean = 0.0
    for y in values:
        y_mean += y
    y_mean /= n
    sxy = 0.0
    sxx = 0.0
    for i, y in enumerate(values):
        dx = i - x_mean
        sxy += dx * (y - y_mean)
        sxx += dx * dx
    slope = sxy / sxx
    if abs(y_mean) < _EPS:
        if abs(slope) < _EPS:
            return 0.0
        return math.inf if slope > 0 else -math.inf
    return slope / abs(y_mean)


def estimate_trend(series: Sequence[float], window: int, threshold: float) -> TrendEstimate:
    """Classify the last ``window`` samples as RISING, FALLING, STABLE or UNKNOWN."""
    if not (isinstance(window, int) and window >= 2):
        raise ValueError(f"trend window must be >= 2, got {window!r}")
    if not (isinstance(threshold, (int, float)) and math.isfinite(threshold) and threshold >= 0):
        raise ValueError(f"trend threshold must be finite and >= 0, got {threshold!r}")
    if series is None or len(series) < 2:
        return TrendEstimate(Trend.UNKNOWN, 0.0, 0 if series is None else len(series))
    values = _window(series, window)
    rel = relative_slope(values)
    if rel > threshold:
        trend = Trend.RISING
    elif rel < -threshold:
        trend = Trend.FALLING
    else:
        trend = Trend.STABLE
    return TrendEstimate(trend, rel, len(values))


def spike_ratio(series: Sequence[float]) -> float | None:
    """Newest sample divided by the sample just before it.

    Returns None with fewer than 2 samples, +inf if demand jumps from zero, and 1.0 if both are zero.
    """
    if series is None or len(series) < 2:
        return None
    previous, latest = _window(series, 2)
    if previous < _EPS:
        return math.inf if latest > _EPS else 1.0
    return latest / previous
