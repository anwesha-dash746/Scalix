"""Phase 7: the M/M/c response-time model and the AWS-list-price cost estimate."""

from __future__ import annotations

import math

import numpy as np
import pytest

from scalix import pricing
from scalix.algorithms import CapacityLimits, StaticPolicy, ThresholdPolicy
from scalix.metrics.queueing import erlang_c, response_model
from scalix.simulation import SimulationConfig, simulate
from scalix.simulation.workloads import Scenario, WorkloadSpec, generate


def direct_erlang_c(c: int, rho: float) -> float:
    a = c * rho
    top = a ** c / math.factorial(c) / (1 - rho)
    return top / (sum(a ** k / math.factorial(k) for k in range(c)) + top)


# --------------------------------------------------------------------------- Erlang C

def test_erlang_c_textbook_values():
    assert erlang_c(np.array([1]), np.array([0.7]))[0] == pytest.approx(0.7)          # M/M/1: C = rho
    assert erlang_c(np.array([2]), np.array([0.5]))[0] == pytest.approx(1 / 3)        # a = 1, c = 2
    assert erlang_c(np.array([5]), np.array([0.0]))[0] == pytest.approx(0.0)


def test_erlang_c_matches_the_direct_formula():
    rng = np.random.default_rng(0)
    c = rng.integers(1, 41, 300)
    rho = rng.uniform(0, 0.99, 300)
    got = erlang_c(c, rho)
    want = np.array([direct_erlang_c(int(ci), float(ri)) for ci, ri in zip(c, rho)])
    assert np.allclose(got, want, rtol=1e-9, atol=1e-12)


def test_erlang_c_rejects_bad_input():
    with pytest.raises(ValueError):
        erlang_c(np.array([0]), np.array([0.5]))
    with pytest.raises(ValueError):
        erlang_c(np.array([2]), np.array([1.0]))
    with pytest.raises(ValueError):
        erlang_c(np.array([2, 3]), np.array([0.5]))


# --------------------------------------------------------------------------- response model

def one_step(util: float, active: int = 4, queue: float = 0.0, **kw):
    return response_model(np.array([active]), np.array([util]), np.array([queue]), np.array([10.0]),
                          capacity_rps=1.0, service_time=2.0, step_seconds=5.0, **kw)


def test_idle_system_is_service_time_and_exponential_p95():
    r = one_step(1e-6)
    assert r["mean_s"] == pytest.approx(2.0, abs=1e-6)
    assert r["p95_s"] == pytest.approx(2.0 * math.log(20), rel=1e-6)   # exponential service, 2 s mean


def test_mean_is_proxy_plus_erlang_wait():
    util = 0.8
    r = one_step(util, active=4)
    c, mu = 8, 0.5                                      # 4 workers x 2 slots, 1 / 2 s
    expected_wait = direct_erlang_c(c, util) / (c * mu * (1 - util))
    assert r["mean_queueing_wait_s"] == pytest.approx(expected_wait)
    assert r["mean_s"] == pytest.approx(2.0 + expected_wait)


def test_waiting_grows_with_load_and_shrinks_with_more_workers():
    means = [one_step(u)["mean_s"] for u in (0.3, 0.6, 0.8, 0.9)]
    assert means == sorted(means) and means[-1] > means[0]
    assert one_step(0.8, active=10)["mean_s"] < one_step(0.8, active=2)["mean_s"]


def test_utilization_is_capped():
    assert one_step(1.0)["mean_s"] == pytest.approx(one_step(0.95)["mean_s"])
    assert one_step(1.0, max_utilization=0.99)["mean_s"] > one_step(1.0)["mean_s"]


def test_p95_matches_monte_carlo():
    util, active = 0.85, 3
    r = one_step(util, active=active, queue=6.0)
    c, mu = active * 2, 0.5
    pw = direct_erlang_c(c, util)
    theta = c * mu * (1 - util)
    rng = np.random.default_rng(1)
    n = 400_000
    waits = np.where(rng.random(n) < pw, rng.exponential(1 / theta, n), 0.0)
    samples = 6.0 / active + waits + rng.exponential(1 / mu, n)
    assert r["p95_s"] == pytest.approx(np.quantile(samples, 0.95), rel=0.01)


def test_mixture_p95_is_arrival_weighted():
    # 95% of arrivals see an idle system, 5% see a 100 s backlog: p95 sits at the boundary.
    r = response_model(np.array([4, 4]), np.array([0.0, 1.0]), np.array([0.0, 400.0]), np.array([95.0, 5.0]),
                       capacity_rps=1.0, service_time=2.0)
    idle_only = one_step(0.0)["p95_s"]
    assert idle_only < r["p95_s"] < 100.0 + 1.0


def test_response_model_validation_and_empty():
    with pytest.raises(ValueError):
        one_step(0.5, max_utilization=1.0)
    empty = response_model(np.array([1]), np.array([0.0]), np.array([0.0]), np.array([0.0]), 1.0, 2.0)
    assert math.isnan(empty["mean_s"])


def test_model_is_never_below_the_proxy_in_a_full_run():
    tr = generate(WorkloadSpec(scenario=Scenario.MIXED, duration_seconds=1800, seed=3))
    r = simulate(ThresholdPolicy(capacity=CapacityLimits()), tr)
    s = r.summary
    assert s["response_time_model_avg_s"] == pytest.approx(s["response_time_proxy_avg_s"] + s["queueing_wait_avg_s"])
    assert s["queueing_wait_avg_s"] > 0
    assert s["response_time_model_p95_s"] >= 2.0 * math.log(20) - 1e-6


# --------------------------------------------------------------------------- pricing

def test_worker_hourly_price_is_the_sum_of_its_cited_parts():
    assert pricing.EBS_HOURLY == pytest.approx(8 * 0.08 / 730)
    assert pricing.WORKER_HOURLY == pytest.approx(0.0104 + 0.005 + 8 * 0.08 / 730)
    cfg = SimulationConfig()
    assert cfg.price_per_worker_hour == pricing.WORKER_HOURLY and cfg.charge_cpu_credits
    assert (cfg.vcpus_per_worker, cfg.cpu_baseline_per_vcpu, cfg.cpu_credit_price_per_vcpu_hour) == (2, 0.10, 0.05)


def constant(rate: float, seconds: float = 60):
    return generate(WorkloadSpec(scenario=Scenario.RISING, duration_seconds=seconds, step_seconds=5, base_rate=rate,
                                 rising_start=1.0, rising_end=1.0, poisson_noise=False))


def test_cpu_credits_hand_checked():
    # 1 worker, saturated for 60 s: spends 2 vCPU x 60 s, earns 0.2 vCPU x 60 s -> 108 vCPU-s surplus.
    r = simulate(StaticPolicy(worker_count=1), constant(2.0), SimulationConfig(price_per_worker_hour=0.36))
    s = r.summary
    assert s["surplus_vcpu_hours"] == pytest.approx(108 / 3600)
    assert s["cost_cpu_credits"] == pytest.approx(108 / 3600 * 0.05)
    assert s["estimated_cost"] == pytest.approx(60 / 3600 * 0.36 + 108 / 3600 * 0.05)


def test_no_credits_below_baseline_and_none_when_switched_off():
    # 4 workers at 5% utilization: below the 10% baseline, so nothing extra is charged.
    quiet = simulate(StaticPolicy(worker_count=4), constant(0.2)).summary
    assert quiet["cost_cpu_credits"] == 0
    busy_flat = simulate(StaticPolicy(worker_count=1), constant(2.0), SimulationConfig(charge_cpu_credits=False)).summary
    assert busy_flat["cost_cpu_credits"] == 0 and busy_flat["estimated_cost"] == busy_flat["cost_hourly_part"]


def test_credits_follow_served_work_in_a_full_run():
    # With every serving worker above baseline, surplus = 2 x served work - 0.2 x worker-time (vCPU-seconds).
    tr = generate(WorkloadSpec(scenario=Scenario.NORMAL, duration_seconds=1800, seed=5))
    r = simulate(StaticPolicy(worker_count=8), tr)
    s = r.summary
    expected = 2 * s["total_served"] / 1.0 - 0.2 * s["worker_seconds"]
    assert s["surplus_vcpu_hours"] * 3600 == pytest.approx(expected)


def test_a_worker_removed_while_booting_pays_no_credits():
    from scalix.metrics.summary import cpu_credit_surplus_vcpu_seconds
    import pandas as pd
    ts = pd.DataFrame({"time_s": [0.0, 5.0, 10.0], "utilization": [1.0, 1.0, 1.0]})
    workers = pd.DataFrame({"ready_s": [0.0, 30.0], "removed_s": [math.nan, 10.0], "lifetime_s": [15.0, 10.0]})
    cfg = SimulationConfig()
    got = cpu_credit_surplus_vcpu_seconds(ts, workers, cfg, 5.0, 15.0)
    assert got == pytest.approx(2 * 15 - 0.2 * 15)        # only the first worker spends


def test_config_validation():
    for bad in (dict(queueing_max_utilization=1.0), dict(queueing_max_utilization=0), dict(vcpus_per_worker=0),
                dict(cpu_baseline_per_vcpu=1.5), dict(cpu_credit_price_per_vcpu_hour=-1),
                dict(charge_cpu_credits="yes")):
        with pytest.raises(ValueError):
            SimulationConfig(**bad)
