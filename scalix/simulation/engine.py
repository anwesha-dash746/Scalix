"""Discrete-time simulator: run one scaling policy against one workload trace.

The model, kept simple on purpose so every number can be explained:

* **Time** advances in fixed steps (``trace.step_seconds``, default 5 s). Every
  ``decision_interval_seconds`` (default 15 s, a whole number of steps) the policy is asked
  for a decision.
* **Workers.** Each serving worker can process ``worker_capacity_rps`` requests per second
  (default 1.0). A newly added worker is billed straight away but only starts serving after
  ``startup_delay_seconds`` (default 30 s). Removed workers stop serving and stop being billed
  immediately (there is no draining phase).
* **Requests** queue in a single shared queue, first come, first served. In each step:
  ``available = queue + arrivals``, ``served = min(available, capacity x step)`` and
  ``queue = available - served``. Nothing is dropped; demand that can't be served yet waits.
* **Estimated utilization** of the serving workers in a step is ``served / (capacity x step)``,
  between 0 and 1. With a queue it is 1.0 (saturated), even if demand is far above capacity.
  The policy sees the average over the last decision interval, like a monitoring system would.
* **Demand signal.** The policy also sees the arrival rate per interval (requests/s). SCALIX uses
  it for its trend and spike checks; the other strategies ignore it.

The policy only decides. This engine applies the decision, enforces the capacity limits again as
a safety net (any clamp is written into the reason), and records everything. Cooldowns are each
policy's own rule; the event log lets tests check that they were respected.

All outputs are SIMULATED. Utilization is estimated from the model above, the response time is a
documented proxy, and cost uses an illustrative price. None of them are AWS measurements or charges.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from typing import Any

import numpy as np
import pandas as pd

from scalix.algorithms import Action, ScalingContext, ScalingPolicy
from scalix.sources import DataSource
from scalix.simulation.workloads import WorkloadTrace


def _positive(name: str, value: float) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}")


def _non_negative(name: str, value: float) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0):
        raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")


@dataclass(frozen=True)
class SimulationConfig:
    """Model settings shared by every policy in an experiment. Times in seconds."""

    decision_interval_seconds: float = 15.0
    worker_capacity_rps: float = 1.0
    startup_delay_seconds: float = 30.0
    service_time_seconds: float = 2.0
    utilization_threshold: float = 0.70
    price_per_worker_hour: float = 0.10
    minimum_billed_seconds: float = 60.0
    currency: str = "USD"

    def __post_init__(self) -> None:
        _positive("decision_interval_seconds", self.decision_interval_seconds)
        _positive("worker_capacity_rps", self.worker_capacity_rps)
        _non_negative("startup_delay_seconds", self.startup_delay_seconds)
        _non_negative("service_time_seconds", self.service_time_seconds)
        _non_negative("utilization_threshold", self.utilization_threshold)
        if self.utilization_threshold > 1:
            raise ValueError("utilization_threshold must be within [0, 1]")
        _non_negative("price_per_worker_hour", self.price_per_worker_hour)
        _non_negative("minimum_billed_seconds", self.minimum_billed_seconds)
        if not (isinstance(self.currency, str) and self.currency.strip()):
            raise ValueError("currency must be a non-empty label, e.g. 'USD'")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class _Worker:
    id: int
    requested_at: float
    ready_at: float
    removed_at: float | None = None

    def alive(self, t: float) -> bool:
        return self.removed_at is None or self.removed_at > t

    def serving(self, t: float) -> bool:
        return self.alive(t) and self.ready_at <= t


@dataclass(frozen=True)
class RunResult:
    """Everything one (policy, workload) run produced."""

    policy: str
    policy_parameters: dict[str, Any]
    workload: dict[str, Any]
    config: SimulationConfig
    timeseries: pd.DataFrame   # one row per step
    events: pd.DataFrame       # one row per decision
    workers: pd.DataFrame      # one row per worker ever started
    summary: dict[str, Any]    # metrics, see scalix.metrics.summary


EVENT_COLUMNS = ["data_source", "time_s", "action", "requested_delta", "applied_delta", "workers_before", "workers_after",
                 "active", "booting", "utilization", "arrival_rate_rps", "trend", "relative_slope", "spike",
                 "cooldown_blocked", "reason"]


def simulate(policy: ScalingPolicy, trace: WorkloadTrace, config: SimulationConfig | None = None) -> RunResult:
    """Run ``policy`` on ``trace``. Deterministic: same inputs, same result."""
    from scalix.metrics.summary import summarize  # local import keeps the module graph simple

    config = config or SimulationConfig()
    step = trace.step_seconds
    ratio = config.decision_interval_seconds / step
    if abs(ratio - round(ratio)) > 1e-9 or round(ratio) < 1:
        raise ValueError(f"decision_interval_seconds ({config.decision_interval_seconds}) must be a whole "
                         f"number (>= 1) of workload steps ({step} s)")
    steps_per_decision = int(round(ratio))
    limits = policy.limits
    mu = config.worker_capacity_rps

    workers = [_Worker(i, 0.0, 0.0) for i in range(limits.initial)]
    next_id = limits.initial
    queue = 0.0
    last_up = last_down = -math.inf
    util_history: list[float] = []
    rate_history: list[float] = []
    interval_utils: list[float] = []
    interval_arrivals = 0.0
    rows: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []

    def counts(t: float) -> tuple[int, int]:
        active = sum(1 for w in workers if w.serving(t))
        booting = sum(1 for w in workers if w.alive(t) and w.ready_at > t)
        return active, booting

    for k, t in enumerate(trace.times):
        t = float(t)

        # ---- decision at the start of the step, using the interval that just ended ----------
        if k > 0 and k % steps_per_decision == 0:
            util = float(np.mean(interval_utils))
            rate = interval_arrivals / config.decision_interval_seconds
            util_history.append(util)
            rate_history.append(rate)
            interval_utils, interval_arrivals = [], 0.0

            active, booting = counts(t)
            before = active + booting
            ctx = ScalingContext(time=t, provisioned=before, active=active, booting=booting, utilization=util,
                                 utilization_history=util_history, arrival_rate_history=rate_history,
                                 last_scale_up_time=last_up, last_scale_down_time=last_down)
            d = policy.decide(ctx)
            reason, applied = d.reason, 0
            if d.action is Action.SCALE_UP:
                allowed = min(d.delta, limits.maximum - before)
                if allowed < d.delta:
                    reason += f" | engine: clamped to max {limits.maximum}"
                for _ in range(max(0, allowed)):
                    workers.append(_Worker(next_id, t, t + config.startup_delay_seconds))
                    next_id += 1
                if allowed > 0:
                    applied, last_up = allowed, t
            elif d.action is Action.SCALE_DOWN:
                allowed = min(-d.delta, before - limits.minimum)
                if allowed < -d.delta:
                    reason += f" | engine: clamped to min {limits.minimum}"
                # Remove workers that are still booting first (newest first), then serving ones.
                alive = [w for w in workers if w.alive(t)]
                order = sorted(alive, key=lambda w: (w.serving(t), -w.requested_at, -w.id))
                for w in order[:max(0, allowed)]:
                    w.removed_at = t
                if allowed > 0:
                    applied, last_down = -allowed, t
            active_after, booting_after = counts(t)
            events.append({
                "data_source": DataSource.SIMULATED.value, "time_s": t, "action": d.action.value, "requested_delta": d.delta, "applied_delta": applied,
                "workers_before": before, "workers_after": active_after + booting_after,
                "active": active, "booting": booting, "utilization": util, "arrival_rate_rps": rate,
                "trend": d.trend.value, "relative_slope": d.relative_slope, "spike": d.spike,
                "cooldown_blocked": d.cooldown_blocked, "reason": reason,
            })

        # ---- serve this step ----------------------------------------------------------------
        active, booting = counts(t)
        provisioned = active + booting
        if active < 1 or not limits.minimum <= provisioned <= limits.maximum:
            raise RuntimeError(f"engine invariant violated at t={t}: active={active}, provisioned={provisioned}")
        arrivals = float(trace.arrivals[k])
        capacity = active * mu * step
        available = queue + arrivals
        served = min(available, capacity)
        queue = available - served
        if queue < 1e-9:
            queue = 0.0
        utilization = served / capacity
        interval_utils.append(utilization)
        interval_arrivals += arrivals
        rows.append({
            "data_source": DataSource.SIMULATED.value,
            "time_s": t,
            "expected_rate_rps": float(trace.expected_rate[k]),
            "arrivals": arrivals,
            "active": active,
            "booting": booting,
            "provisioned": provisioned,
            "capacity_rps": active * mu,
            "served": served,
            "queue": queue,
            "utilization": utilization,
            "response_time_proxy_s": config.service_time_seconds + queue / (active * mu),
        })

    end_time = float(trace.times[-1]) + step if len(trace.times) else 0.0
    timeseries = pd.DataFrame(rows)
    events_df = pd.DataFrame(events, columns=EVENT_COLUMNS)
    workers_df = pd.DataFrame([{
        "worker_id": w.id,
        "requested_s": w.requested_at,
        "ready_s": w.ready_at,
        "removed_s": w.removed_at if w.removed_at is not None else math.nan,
        "lifetime_s": (w.removed_at if w.removed_at is not None else end_time) - w.requested_at,
    } for w in workers])
    summary = summarize(timeseries, events_df, workers_df, config, step, end_time, trace.total_arrivals)
    return RunResult(policy=policy.name, policy_parameters=policy.parameters(), workload=trace.spec.to_dict(),
                     config=config, timeseries=timeseries, events=events_df, workers=workers_df, summary=summary)
