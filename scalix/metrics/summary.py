"""Per-run metrics computed from a simulation's time series, decision log and worker list.

Every metric is SIMULATED. The column-by-column definitions are in docs/SIMULATION.md:

* **Utilization** is *estimated* by the model (served / capacity per step); it is not measured CPU.
* **Response time** comes from documented formulas, not measured latency: the original *proxy*
  (below) and, since Phase 7, the M/M/c *queueing model* in ``scalix.metrics.queueing``.
* **Cost** is *estimated* from AWS list prices (``scalix.pricing``): billed worker-time x an hourly
  price, plus T3 Unlimited surplus CPU credits. It is not an AWS bill.

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

from scalix.metrics.queueing import response_model


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


def cpu_credit_surplus_vcpu_seconds(timeseries: pd.DataFrame, workers: pd.DataFrame, config: Any,
                                    step_seconds: float, end_time: float) -> float:
    """Total surplus vCPU-seconds over all workers under T3 Unlimited (see scalix.pricing).

    Each worker earns ``baseline x vcpus`` for its whole lifetime (booting included) and spends
    ``utilization x vcpus`` in every step it serves. AWS charges the surplus at termination, so a
    worker's charge is ``max(0, spent - earned)``; credits earned by one worker can't pay for another.
    """
    if len(workers) == 0 or len(timeseries) == 0:
        return 0.0
    times = timeseries["time_s"].to_numpy(dtype=float)
    util = timeseries["utilization"].to_numpy(dtype=float)
    cumulative = np.concatenate([[0.0], np.cumsum(util * step_seconds)])   # util-seconds before step k
    ready = workers["ready_s"].to_numpy(dtype=float)
    removed = workers["removed_s"].to_numpy(dtype=float)
    removed = np.where(np.isnan(removed), end_time, removed)
    start_idx = np.searchsorted(times, ready - 1e-9, side="left")
    end_idx = np.searchsorted(times, removed - 1e-9, side="left")
    end_idx = np.maximum(end_idx, start_idx)
    spent = config.vcpus_per_worker * (cumulative[end_idx] - cumulative[start_idx])
    earned = config.cpu_baseline_per_vcpu * config.vcpus_per_worker * workers["lifetime_s"].to_numpy(dtype=float)
    return float(np.maximum(0.0, spent - earned).sum())


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

    surplus_vcpu_seconds = (cpu_credit_surplus_vcpu_seconds(ts, workers, config, step_seconds, end_time)
                            if config.charge_cpu_credits else 0.0)
    cost_hourly = billed_seconds / 3600.0 * config.price_per_worker_hour
    cost_credits = surplus_vcpu_seconds / 3600.0 * config.cpu_credit_price_per_vcpu_hour
    model = response_model(ts["active"].to_numpy(), util, queue, arrivals, config.worker_capacity_rps,
                           config.service_time_seconds, config.queueing_max_utilization, step_seconds,
                           total_arrivals) if config.service_time_seconds > 0 else {
        "mean_s": math.nan, "p95_s": math.nan, "mean_queueing_wait_s": math.nan, "mean_backlog_wait_s": math.nan}

    applied = events["applied_delta"].to_numpy(dtype=int) if len(events) else np.array([], dtype=int)
    queue_integral = float((queue * step_seconds).sum())
    avg_wait = queue_integral / total_arrivals if total_arrivals > 0 else 0.0

    return {
        "data_source": "SIMULATED",
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
        # simulated response time: queueing model (headline, Phase 7) and the original proxy
        "response_time_model_avg_s": model["mean_s"],
        "response_time_model_p95_s": model["p95_s"],
        "queueing_wait_avg_s": model["mean_queueing_wait_s"],
        "response_time_proxy_avg_s": config.service_time_seconds + avg_wait,
        "response_time_proxy_p95_s": weighted_percentile(ts["response_time_proxy_s"].to_numpy(), arrivals, 95),
        # estimated cost from AWS list prices
        "estimated_cost": cost_hourly + cost_credits,
        "cost_hourly_part": cost_hourly,
        "cost_cpu_credits": cost_credits,
        "surplus_vcpu_hours": surplus_vcpu_seconds / 3600.0,
        "cost_currency": config.currency,
        "price_per_worker_hour": config.price_per_worker_hour,
    }


#: Metrics where a lower value is better and a higher value is better; used by reports.
LOWER_IS_BETTER = ("response_time_model_avg_s", "response_time_model_p95_s", "queueing_wait_avg_s",
                   "response_time_proxy_avg_s", "response_time_proxy_p95_s", "estimated_cost", "cost_cpu_credits",
                   "worker_minutes", "time_above_threshold_s", "time_with_queue_s", "peak_queue_requests",
                   "unserved_at_end", "scale_up_events", "scale_down_events")
HIGHER_IS_BETTER = ("avg_utilization", "served_fraction")
