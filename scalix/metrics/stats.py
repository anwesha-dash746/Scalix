"""Statistics across repetitions (different seeds): n, mean, std, min, max and a 95% CI.

* std is the sample standard deviation (divides by n - 1).
* The 95% confidence interval is mean +/- t x std / sqrt(n), using Student's t with n - 1
  degrees of freedom. With one repetition, std and the CI are undefined (NaN), not zero.
"""

from __future__ import annotations

import math
from typing import Iterable

import numpy as np
import pandas as pd

# Two-sided 95% Student-t critical values for 1..30 degrees of freedom.
_T95 = [12.706, 4.303, 3.182, 2.776, 2.571, 2.447, 2.365, 2.306, 2.262, 2.228,
        2.201, 2.179, 2.160, 2.145, 2.131, 2.120, 2.110, 2.101, 2.093, 2.086,
        2.080, 2.074, 2.069, 2.064, 2.060, 2.056, 2.052, 2.048, 2.045, 2.042]


def t_critical_95(dof: int) -> float:
    if dof < 1:
        return math.nan
    if dof <= 30:
        return _T95[dof - 1]
    if dof <= 60:
        return 2.000
    if dof <= 120:
        return 1.980
    return 1.960


def aggregate(summary: pd.DataFrame, metrics: Iterable[str],
              by: tuple[str, ...] = ("scenario", "policy")) -> pd.DataFrame:
    """One row per group and metric: n, mean, std, min, max, ci95_low, ci95_high."""
    rows = []
    for keys, group in summary.groupby(list(by), sort=False):
        keys = keys if isinstance(keys, tuple) else (keys,)
        for metric in metrics:
            values = group[metric].to_numpy(dtype=float)
            values = values[~np.isnan(values)]
            n = len(values)
            mean = float(values.mean()) if n else math.nan
            std = float(values.std(ddof=1)) if n > 1 else math.nan
            half = t_critical_95(n - 1) * std / math.sqrt(n) if n > 1 else math.nan
            rows.append({**dict(zip(by, keys)), "metric": metric, "n": n, "mean": mean, "std": std,
                         "min": float(values.min()) if n else math.nan,
                         "max": float(values.max()) if n else math.nan,
                         "ci95_low": mean - half if n > 1 else math.nan,
                         "ci95_high": mean + half if n > 1 else math.nan})
    return pd.DataFrame(rows)
