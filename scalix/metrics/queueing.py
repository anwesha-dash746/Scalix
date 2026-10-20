"""Queueing model for the simulated response time (M/M/c, Erlang C), added in Phase 7.

Why: the original proxy (``service_time + backlog wait``) is exactly the service time whenever no
backlog forms, so it can't show the waiting that real servers have at 60-90% load. Queueing theory
can. This module adds that waiting time on top of the backlog wait the simulator already models.

The model, per simulation step (5 s):

* Each serving worker is treated as ``slots = round(capacity_rps x service_time)`` parallel
  servers (default 1 req/s x 2 s = 2 slots), each serving one request at a time with an
  exponentially distributed service time of mean ``service_time``. So the group is an M/M/c queue
  with ``c = active_workers x slots`` servers and per-server rate ``mu = 1 / service_time``.
* The load is the step's estimated utilization ``rho`` (served / capacity, which the simulator
  already computes). It is capped at ``max_utilization`` (default 0.95): the M/M/c formula is a
  long-run average and grows without bound as ``rho -> 1``, which a 5 s step cannot reach. Any
  overload beyond the cap is already captured by the backlog the simulator queues explicitly.
* Waiting time ``Wq`` for a request arriving in that step: with probability ``C`` (Erlang C, the
  chance that all ``c`` servers are busy) it waits an exponential time with rate
  ``theta = c x mu x (1 - rho)``; otherwise it doesn't wait. Its response time is
  ``backlog_delay + Wq + S``, where ``backlog_delay = queue / serving_capacity`` is the original
  proxy's waiting term and ``S`` is its own exponential service time.

Outputs (arrival-weighted over the run):

* ``mean_response``: ``proxy_mean + mean(C / theta)``, i.e. the original proxy plus the queueing
  delay below saturation. So the model is never below the old proxy.
* ``p95_response``: the 95th percentile of the arrival-weighted mixture of the per-step response
  distributions, solved numerically. Because service times are exponential, even an idle system has
  a p95 of ``service_time x ln(20)`` (about 6.0 s for 2 s), not 2 s.

It is still a model, not a measurement: it assumes Poisson arrivals and exponential service, treats
each step as steady state, and ignores network and per-request size differences.
"""

from __future__ import annotations

import math

import numpy as np


def erlang_c(servers: np.ndarray, rho: np.ndarray) -> np.ndarray:
    """Probability that an arriving request has to wait in an M/M/c queue.

    ``servers`` (c, integers >= 1) and ``rho`` (per-server utilization in [0, 1)) are arrays of the
    same shape. Uses the numerically stable Erlang B recursion, then converts B to C.
    """
    c = np.asarray(servers, dtype=int)
    rho = np.asarray(rho, dtype=float)
    if c.shape != rho.shape:
        raise ValueError("servers and rho must have the same shape")
    if (c < 1).any():
        raise ValueError("servers must be >= 1")
    if ((rho < 0) | (rho >= 1)).any():
        raise ValueError("rho must be in [0, 1)")
    a = rho * c                        # offered load in Erlangs
    b = np.ones_like(a)                # Erlang B with 0 servers
    for k in range(1, int(c.max()) + 1 if c.size else 1):
        active = k <= c
        nb = a * b / (k + a * b)
        b = np.where(active, nb, b)
    return b / (1.0 - rho * (1.0 - b))


def _survival(t: np.ndarray, shift: np.ndarray, p_wait: np.ndarray, theta: np.ndarray, mu: float) -> np.ndarray:
    """P(response > t) for each step: shift + Wq + S (see module docstring). Shapes broadcast."""
    x = np.maximum(t - shift, 0.0)
    no_wait = np.exp(-mu * x)
    # Sum of two exponentials (rates theta and mu): hypoexponential survival.
    close = np.abs(theta - mu) < 1e-9 * mu
    safe = np.where(close, theta + 1.0, theta)    # avoid division by zero; replaced below
    hypo = (safe * np.exp(-mu * x) - mu * np.exp(-safe * x)) / (safe - mu)
    erlang2 = np.exp(-mu * x) * (1.0 + mu * x)    # theta == mu case
    waits = np.where(close, erlang2, hypo)
    s = (1.0 - p_wait) * no_wait + p_wait * waits
    return np.where(t < shift, 1.0, s)


def response_model(active: np.ndarray, utilization: np.ndarray, queue: np.ndarray, arrivals: np.ndarray,
                   capacity_rps: float, service_time: float, max_utilization: float = 0.95,
                   step_seconds: float = 5.0, total_arrivals: float | None = None) -> dict[str, float]:
    """Arrival-weighted mean and p95 response time of a run under the M/M/c model.

    ``active``, ``utilization``, ``queue`` and ``arrivals`` are per-step arrays from the simulator's
    time series. Returns ``mean_s``, ``p95_s``, ``mean_queueing_wait_s`` (the Erlang C part only) and
    ``mean_backlog_wait_s`` (the original proxy's waiting part, from Little's law).
    """
    if not (service_time > 0 and capacity_rps > 0):
        raise ValueError("service_time and capacity_rps must be > 0 for the queueing model")
    if not 0 < max_utilization < 1:
        raise ValueError("max_utilization must be in (0, 1)")
    active = np.asarray(active, dtype=float)
    utilization = np.asarray(utilization, dtype=float)
    queue = np.asarray(queue, dtype=float)
    arrivals = np.asarray(arrivals, dtype=float)
    total = float(arrivals.sum()) if total_arrivals is None else float(total_arrivals)
    if total <= 0 or arrivals.sum() <= 0:
        return {"mean_s": math.nan, "p95_s": math.nan, "mean_queueing_wait_s": math.nan,
                "mean_backlog_wait_s": math.nan}

    slots = max(1, int(round(capacity_rps * service_time)))
    mu = 1.0 / service_time
    c = np.maximum(1, np.round(active).astype(int) * slots)
    rho = np.clip(utilization, 0.0, max_utilization)
    p_wait = erlang_c(c, rho)
    theta = c * mu * (1.0 - rho)
    queueing_wait = p_wait / theta
    shift = queue / (active * capacity_rps)                 # backlog delay, as in the original proxy

    w = arrivals / arrivals.sum()
    backlog_wait = float((queue * step_seconds).sum()) / total     # Little's law, as in the proxy
    mean_queueing = float((w * queueing_wait).sum())
    mean = service_time + backlog_wait + mean_queueing

    # p95 of the arrival-weighted mixture: bisection on the mixture survival function.
    mask = w > 0
    sh, pw, th, ww = shift[mask], p_wait[mask], theta[mask], w[mask]

    def tail(t: float) -> float:
        return float((ww * _survival(np.full_like(sh, t), sh, pw, th, mu)).sum())

    lo, hi = 0.0, float(sh.max() + (1.0 / th.min()) * 40 + service_time * 40)
    for _ in range(80):
        mid = 0.5 * (lo + hi)
        if tail(mid) > 0.05:
            lo = mid
        else:
            hi = mid
    return {"mean_s": mean, "p95_s": hi, "mean_queueing_wait_s": mean_queueing, "mean_backlog_wait_s": backlog_wait}
