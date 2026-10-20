"""Metrics (per run) and statistics (across repetitions). All values are simulated."""

from scalix.metrics.stats import aggregate, t_critical_95
from scalix.metrics.summary import HIGHER_IS_BETTER, LOWER_IS_BETTER, summarize, weighted_percentile

__all__ = ["HIGHER_IS_BETTER", "LOWER_IS_BETTER", "aggregate", "summarize", "t_critical_95",
           "weighted_percentile"]
