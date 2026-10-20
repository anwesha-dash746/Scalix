"""Per-run metrics computed from a simulation's time series, decision log and worker list.

Every metric is SIMULATED. The column-by-column definitions are in docs/SIMULATION.md:

* **Utilization** is *estimated* by the model (served / capacity per step); it is not measured CPU.
* **Response time** is a *proxy*: a documented formula, not a measured latency (see below).
* **Cost** is *illustrative*: worker-time x a configurable price; it is not an AWS charge.

Response-time proxy:

* ``response_time_proxy_avg_s = service_time + (sum over steps of queue x step) / total_arrivals``.
  The second term is the average queueing delay given by Little's law (average queue length =
  arrival rate x average wait).
* ``response_time_proxy_p95_s`` is the arrival-weighted 95th percentile of the per-step estimate
  ``service_time + queue / serving_capacity``, which is the time a request arriving at the end
  of that step would wait for the queue ahead of it, plus the service time.
* Below saturation the queue is empty, so the proxy is just ``service_time``. It deliberately
  ignores the smaller queueing delays that real systems show at high, but not full, utilization.
"""

from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def weighted_percentile(values: np.ndarray, weights: np.ndarray, q: float) -> float:
    """Smallest value v such that at least q% of the total weight is at or below v."""
    values = np.asarray(values, dtype=float)
    weights = np.asarray(weights, dtype=float)
    mask = weights > 0
    if not mask.any():
        return math.nan
    order = np.argsort(values[mask], kind="stable")
    v, w = values[mask][order], weights[mask][order]
    cumulative = np.cumsum(w)
    idx = int(np.searchsorted(cumulative, q / 100.0 * cumulative[-1], side="left"))
    return float(v[min(idx, len(v) - 1)])


def summarize(timeseries: pd.DataFrame, events: pd.DataFrame, workers: pd.DataFrame, config: Any,
              step_seconds: float, end_time: float, total_arrivals: float) -> dict[str, Any]:
    ts = timeseries
    n_steps = len(ts)
    duration = n_steps * step_seconds
    util = ts["utilization"].to_numpy(dtype=float)
    queue = ts["queue"].to_numpy(dtype=float)
    arrivals = ts["arrivals"].to_numpy(dtype=float)
    served_total = float(ts["served"].sum())

    lifetimes = workers["lifetime_s"].to_numpy(dtype=float)
    worker_seconds = float(lifetimes.sum())
    billed = np.maximum(config.minimum_billed_seconds, np.ceil(lifetimes - 1e-9))
    billed_seconds = float(billed.sum())

    applied = events["applied_delta"].to_numpy(dtype=int) if len(events) else np.array([], dtype=int)
    queue_integral = float((queue * step_seconds).sum())
    avg_wait = queue_integral / total_arrivals if total_arrivals > 0 else 0.0

    return {
        "duration_s": duration,
        "steps": n_steps,
        "total_arrivals": float(total_arrivals),
        "total_served": served_total,
        "unserved_at_end": float(queue[-1]) if n_steps else 0.0,
        "served_fraction": served_total / total_arrivals if total_arrivals > 0 else 1.0,
        # utilization (estimated)
        "avg_utilization": float(util.mean()) if n_steps else math.nan,
        "peak_utilization": float(util.max()) if n_steps else math.nan,
        "time_above_threshold_s": float((util > config.utilization_threshold).sum() * step_seconds),
        "time_above_threshold_fraction": float((util > config.utilization_threshold).mean()) if n_steps else 0.0,
        # capacity
        "avg_workers": float(ts["provisioned"].mean()) if n_steps else math.nan,
        "peak_workers": int(ts["provisioned"].max()) if n_steps else 0,
        "min_workers": int(ts["provisioned"].min()) if n_steps else 0,
        "worker_seconds": worker_seconds,
        "worker_minutes": worker_seconds / 60.0,
        "billed_worker_seconds": billed_seconds,
        # scaling activity
        "decisions": int(len(events)),
        "scale_up_events": int((applied > 0).sum()),
        "scale_down_events": int((applied < 0).sum()),
        "workers_added": int(applied[applied > 0].sum()),
        "workers_removed": int(-applied[applied < 0].sum()),
        "cooldown_blocks": int(events["cooldown_blocked"].sum()) if len(events) else 0,
        "engine_clamps": int(events["reason"].str.contains("engine: clamped").sum()) if len(events) else 0,
        # overload / unserved demand
        "time_with_queue_s": float((queue > 0).sum() * step_seconds),
        "peak_queue_requests": float(queue.max()) if n_steps else 0.0,
        "queued_request_seconds": queue_integral,
        # simulated response-time proxy
        "response_time_proxy_avg_s": config.service_time_seconds + avg_wait,
        "response_time_proxy_p95_s": weighted_percentile(ts["response_time_proxy_s"].to_numpy(), arrivals, 95),
        # illustrative cost
        "illustrative_cost": billed_seconds / 3600.0 * config.price_per_worker_hour,
        "cost_currency": config.currency,
        "price_per_worker_hour": config.price_per_worker_hour,
    }


#: Metrics where a lower value is better and a higher value is better; used by reports.
LOWER_IS_BETTER = ("response_time_proxy_avg_s", "response_time_proxy_p95_s", "illustrative_cost",
                   "worker_minutes", "time_above_threshold_s", "time_with_queue_s", "peak_queue_requests",
                   "unserved_at_end", "scale_up_events", "scale_down_events")
HIGHER_IS_BETTER = ("avg_utilization", "served_fraction")
