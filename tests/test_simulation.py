"""Simulation engine: model arithmetic, capacity limits, cooldowns, start-up delay, event-log
consistency, reproducibility and metrics.

None of these tests assume which strategy wins; they check that the simulator is correct.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

import numpy as np
import pandas as pd
import pytest

from scalix.algorithms import (
    CapacityLimits,
    ScalingDecision,
    ScalingPolicy,
    ScalixPolicy,
    StaticPolicy,
    ThresholdPolicy,
)
from scalix.metrics import aggregate, t_critical_95, weighted_percentile
from scalix.simulation import Scenario, SimulationConfig, WorkloadSpec, generate, simulate
from scalix.simulation.workloads import SYNTHETIC_SCENARIOS
from scalix.simulation.experiment import default_policies

CAP = CapacityLimits(2, 6, 12)
POLICIES = default_policies(CAP)


def trace(scenario=Scenario.NORMAL, **kw):
    return generate(WorkloadSpec(scenario=scenario, **kw))


@pytest.fixture(scope="module")
def all_runs():
    """Every policy on every scenario for two seeds (48 runs)."""
    runs = []
    for scenario in SYNTHETIC_SCENARIOS:
        for seed in (1, 2):
            tr = trace(scenario, seed=seed)
            runs.extend(simulate(p, tr) for p in POLICIES)
    return runs


# --------------------------------------------------------------------------- model arithmetic

def test_hand_checked_tiny_run():
    """1 worker (1 req/s), constant 2 req/s, 5 s steps, 60 s: queue grows by 5 every step."""
    tr = generate(WorkloadSpec(scenario=Scenario.RISING, duration_seconds=60, step_seconds=5, base_rate=2.0,
                               rising_start=1.0, rising_end=1.0, poisson_noise=False))
    cfg = SimulationConfig(price_per_worker_hour=0.36, service_time_seconds=2.0, charge_cpu_credits=False)
    r = simulate(StaticPolicy(worker_count=1), tr, cfg)
    ts = r.timeseries
    assert ts["served"].tolist() == [5.0] * 12
    assert ts["queue"].tolist() == [5.0 * k for k in range(1, 13)]
    assert ts["utilization"].tolist() == [1.0] * 12
    assert ts["response_time_proxy_s"].iloc[0] == pytest.approx(2.0 + 5.0 / 1.0)
    m = r.summary
    assert m["total_arrivals"] == 120 and m["total_served"] == 60 and m["unserved_at_end"] == 60
    assert m["served_fraction"] == pytest.approx(0.5)
    assert m["worker_seconds"] == 60 and m["worker_minutes"] == 1
    assert m["estimated_cost"] == pytest.approx(60 / 3600 * 0.36) and m["cost_cpu_credits"] == 0
    # Little's law: sum(queue x step) / arrivals = 5*(1+...+12)*5 / 120 = 16.25 s
    assert m["response_time_proxy_avg_s"] == pytest.approx(2.0 + 16.25)
    assert m["time_above_threshold_s"] == 60 and m["time_with_queue_s"] == 60
    assert m["peak_queue_requests"] == 60


def test_light_load_utilization_is_demand_over_capacity():
    tr = generate(WorkloadSpec(scenario=Scenario.RISING, duration_seconds=60, base_rate=2.0, rising_start=1.0,
                               rising_end=1.0, poisson_noise=False))
    r = simulate(StaticPolicy(worker_count=4), tr)
    assert np.allclose(r.timeseries["utilization"], 0.5)  # 2 req/s on 4 x 1 req/s
    assert r.summary["response_time_proxy_avg_s"] == pytest.approx(2.0)
    assert r.summary["unserved_at_end"] == 0


def test_minimum_billing_applies_per_worker():
    tr = generate(WorkloadSpec(duration_seconds=30, step_seconds=5, poisson_noise=False))
    r = simulate(StaticPolicy(worker_count=2), tr)
    assert r.summary["worker_seconds"] == 60
    assert r.summary["billed_worker_seconds"] == 120  # 2 workers x 60 s minimum


# --------------------------------------------------------------------------- static

@pytest.mark.parametrize("scenario", SYNTHETIC_SCENARIOS)
def test_static_never_changes_capacity(scenario):
    r = simulate(StaticPolicy(worker_count=6), trace(scenario))
    assert (r.timeseries["provisioned"] == 6).all() and (r.timeseries["active"] == 6).all()
    assert (r.events["applied_delta"] == 0).all() and (r.events["action"] == "NONE").all()
    assert r.summary["scale_up_events"] == r.summary["scale_down_events"] == 0
    assert len(r.workers) == 6


# --------------------------------------------------------------------------- limits and invariants

def test_all_strategies_respect_capacity_limits(all_runs):
    for r in all_runs:
        lim = next(p for p in POLICIES if p.name == r.policy).limits
        ts = r.timeseries
        assert ts["provisioned"].between(lim.minimum, lim.maximum).all(), r.policy
        assert (ts["active"] >= 1).all() and (ts["booting"] >= 0).all()
        assert r.events["workers_after"].between(lim.minimum, lim.maximum).all()


def test_physical_invariants(all_runs):
    for r in all_runs:
        ts = r.timeseries
        assert (ts["queue"] >= 0).all()
        assert (ts["served"] <= ts["capacity_rps"] * 5.0 + 1e-9).all()
        assert ts["utilization"].between(0, 1).all()
        assert (ts["provisioned"] == ts["active"] + ts["booting"]).all()
        # queue conservation, step by step: queue = previous queue + arrivals - served
        prev = ts["queue"].shift(fill_value=0.0)
        assert np.allclose(ts["queue"], prev + ts["arrivals"] - ts["served"], atol=1e-9)
        # served = everything available, up to capacity
        assert np.allclose(ts["served"], np.minimum(prev + ts["arrivals"], ts["capacity_rps"] * 5.0))
        # anything left over means the serving workers were completely busy
        assert (ts.loc[ts["queue"] > 0, "utilization"] == 1.0).all()
        m = r.summary
        assert m["total_arrivals"] == pytest.approx(m["total_served"] + m["unserved_at_end"])
        # every provisioned worker, booting or serving, is paid for in every step
        assert m["worker_seconds"] == pytest.approx(ts["provisioned"].sum() * 5.0)


def test_event_log_is_consistent(all_runs):
    for r in all_runs:
        ev, ts = r.events, r.timeseries.set_index("time_s")
        assert (ev["time_s"] % 15 == 0).all() and (ev["time_s"] > 0).all()
        assert len(ev) == 1800 // 15 - 1
        assert (ev["workers_after"] == ev["workers_before"] + ev["applied_delta"]).all()
        assert (ev["workers_before"].iloc[1:].to_numpy() == ev["workers_after"].iloc[:-1].to_numpy()).all()
        assert (ev["workers_before"].iloc[0] == 6)
        # what the log says happened is what the time series shows
        assert (ts.loc[ev["time_s"], "provisioned"].to_numpy() == ev["workers_after"].to_numpy()).all()
        up, down, none = (ev["action"] == a for a in ("SCALE_UP", "SCALE_DOWN", "NONE"))
        assert (ev.loc[up, "applied_delta"] >= 0).all() and (ev.loc[down, "applied_delta"] <= 0).all()
        assert (ev.loc[none, "applied_delta"] == 0).all() and (ev.loc[none, "requested_delta"] == 0).all()
        assert (ev["applied_delta"].abs() <= ev["requested_delta"].abs()).all()
        assert ev["reason"].str.len().gt(0).all()
        assert not ev.loc[ev["cooldown_blocked"], "action"].ne("NONE").any()


def test_cooldowns_are_respected(all_runs):
    for r in all_runs:
        ev = r.events[r.events["applied_delta"] != 0]
        if r.policy == "THRESHOLD":
            assert (ev["time_s"].diff().dropna() >= 60).all()
        if r.policy == "SCALIX":
            ups = ev[ev["applied_delta"] > 0]["time_s"]
            assert (ups.diff().dropna() >= 30).all()
            for t in ev[ev["applied_delta"] < 0]["time_s"]:
                earlier = ev[ev["time_s"] < t]["time_s"]
                assert earlier.empty or t - earlier.max() >= 90


def test_scaling_actually_happens_somewhere(all_runs):
    """Guard against a silently broken engine that never scales: both auto-scalers act."""
    for name in ("THRESHOLD", "SCALIX"):
        runs = [r for r in all_runs if r.policy == name]
        assert sum(r.summary["scale_up_events"] for r in runs) > 0
        assert sum(r.summary["scale_down_events"] for r in runs) > 0


# --------------------------------------------------------------------------- start-up delay

def _first_scale_up(r):
    ev = r.events[r.events["applied_delta"] > 0]
    return float(ev["time_s"].iloc[0]), int(ev["applied_delta"].iloc[0])


@pytest.mark.parametrize("delay", [0.0, 30.0, 60.0])
def test_new_workers_serve_only_after_the_startup_delay(delay):
    tr = trace(Scenario.SPIKE, seed=3)
    r = simulate(ThresholdPolicy(capacity=CAP), tr, SimulationConfig(startup_delay_seconds=delay))
    t_up, added = _first_scale_up(r)
    ts = r.timeseries.set_index("time_s")
    before = ts.loc[t_up - 5, "active"]
    if delay == 0:
        assert ts.loc[t_up, "active"] == before + added
    else:
        assert ts.loc[t_up, "active"] == before and ts.loc[t_up, "booting"] >= added
        assert ts.loc[t_up + delay - 5, "active"] == before
        assert ts.loc[t_up + delay, "active"] >= before + added - 0  # ready now (unless removed)
    new = r.workers[r.workers["requested_s"] == t_up]
    assert (new["ready_s"] - new["requested_s"] == delay).all()


def test_booting_workers_are_billed():
    tr = trace(Scenario.SPIKE, seed=3)
    r = simulate(ThresholdPolicy(capacity=CAP), tr, SimulationConfig(startup_delay_seconds=60))
    ts = r.timeseries
    assert (ts["booting"] > 0).any()
    assert r.summary["avg_workers"] == pytest.approx(ts["provisioned"].mean())


# --------------------------------------------------------------------------- engine safety net

@dataclass(frozen=True)
class Greedy(ScalingPolicy):
    """A deliberately badly behaved policy that ignores the limits."""

    NAME: ClassVar[str] = "GREEDY"
    sign: int = 1

    @property
    def limits(self):
        return CAP

    def decide(self, ctx):
        if self.sign > 0:
            return ScalingDecision.up(100, "always wants more")
        return ScalingDecision.down(100, "always wants fewer")

    def parameters(self):
        return {}


@pytest.mark.parametrize("sign, bound", [(1, 12), (-1, 2)])
def test_engine_clamps_a_policy_that_ignores_limits(sign, bound):
    r = simulate(Greedy(sign), trace())
    assert (r.timeseries["provisioned"].iloc[3:] == bound).all()
    assert r.events["reason"].str.contains("engine: clamped").all()
    assert r.summary["engine_clamps"] == len(r.events)


def test_scale_down_removes_booting_workers_first():
    @dataclass(frozen=True)
    class UpThenDown(ScalingPolicy):
        NAME: ClassVar[str] = "UPDOWN"

        @property
        def limits(self):
            return CAP

        def decide(self, ctx):
            if ctx.time == 15:
                return ScalingDecision.up(3, "add 3")
            if ctx.time == 30:
                return ScalingDecision.down(2, "remove 2")
            return ScalingDecision.none("hold")

        def parameters(self):
            return {}

    r = simulate(UpThenDown(), trace(), SimulationConfig(startup_delay_seconds=60))
    ts = r.timeseries.set_index("time_s")
    assert (ts.loc[30, "active"], ts.loc[30, "booting"]) == (6, 1)
    removed = r.workers.dropna(subset=["removed_s"])
    assert (removed["requested_s"] == 15).all() and len(removed) == 2


# --------------------------------------------------------------------------- reproducibility

@pytest.mark.parametrize("policy", POLICIES, ids=lambda p: p.name)
def test_runs_are_reproducible(policy):
    a = simulate(policy, trace(Scenario.MIXED, seed=11))
    b = simulate(policy, trace(Scenario.MIXED, seed=11))
    pd.testing.assert_frame_equal(a.timeseries, b.timeseries)
    pd.testing.assert_frame_equal(a.events, b.events)
    assert a.summary == b.summary


def test_different_seeds_give_different_runs():
    a = simulate(POLICIES[2], trace(Scenario.MIXED, seed=11))
    b = simulate(POLICIES[2], trace(Scenario.MIXED, seed=12))
    assert a.summary["total_arrivals"] != b.summary["total_arrivals"]


# --------------------------------------------------------------------------- scenario sanity

def test_model_sanity_per_scenario():
    """The model reacts to each scenario the way its arithmetic says it must."""
    static = StaticPolicy(worker_count=6)
    normal = simulate(static, trace(Scenario.NORMAL, seed=5)).summary
    rising = simulate(static, trace(Scenario.RISING, seed=5)).summary
    spike = simulate(static, trace(Scenario.SPIKE, seed=5)).summary
    # NORMAL peaks at 6.25 req/s, just above 6 workers: little or no lasting queue.
    assert normal["peak_queue_requests"] < 50
    # RISING ends at 12.5 req/s on 6 req/s of capacity: a large queue builds up.
    assert rising["unserved_at_end"] > 1000
    # SPIKE: 15 req/s for 3 minutes on 6 req/s: queue peaks during the spike.
    assert spike["peak_queue_requests"] > 1000
    assert spike["time_with_queue_s"] > 180


# --------------------------------------------------------------------------- configuration

@pytest.mark.parametrize("kwargs", [
    dict(decision_interval_seconds=0), dict(worker_capacity_rps=0), dict(worker_capacity_rps=-1),
    dict(startup_delay_seconds=-1), dict(utilization_threshold=1.5), dict(price_per_worker_hour=-0.1),
    dict(minimum_billed_seconds=math.inf), dict(currency=""), dict(service_time_seconds=math.nan),
])
def test_invalid_simulation_settings(kwargs):
    with pytest.raises(ValueError):
        SimulationConfig(**kwargs)


def test_decision_interval_must_fit_the_step():
    with pytest.raises(ValueError, match="whole number"):
        simulate(POLICIES[1], trace(step_seconds=4.0, duration_seconds=1800),
                 SimulationConfig(decision_interval_seconds=15))
    with pytest.raises(ValueError):
        simulate(POLICIES[1], trace(), SimulationConfig(decision_interval_seconds=2.5))


def test_capacity_per_worker_changes_the_load():
    tr = trace(Scenario.NORMAL, seed=9)
    slow = simulate(StaticPolicy(worker_count=6), tr, SimulationConfig(worker_capacity_rps=0.5)).summary
    fast = simulate(StaticPolicy(worker_count=6), tr, SimulationConfig(worker_capacity_rps=2.0)).summary
    assert slow["avg_utilization"] > fast["avg_utilization"]


# --------------------------------------------------------------------------- statistics helpers

def test_weighted_percentile():
    assert weighted_percentile(np.array([1, 2, 3, 4]), np.array([1, 1, 1, 1]), 50) == 2
    assert weighted_percentile(np.array([1, 100]), np.array([99, 1]), 95) == 1
    assert weighted_percentile(np.array([1, 100]), np.array([94, 6]), 95) == 100
    assert math.isnan(weighted_percentile(np.array([1.0]), np.array([0.0]), 95))


def test_aggregate_statistics():
    df = pd.DataFrame({"scenario": ["S"] * 3 + ["T"], "policy": ["P"] * 4, "m": [1.0, 2.0, 3.0, 5.0]})
    agg = aggregate(df, ["m"]).set_index("scenario")
    s = agg.loc["S"]
    assert (s["n"], s["mean"], s["std"], s["min"], s["max"]) == (3, 2.0, 1.0, 1.0, 3.0)
    assert s["ci95_high"] - s["mean"] == pytest.approx(4.303 * 1.0 / math.sqrt(3))
    t = agg.loc["T"]
    assert t["n"] == 1 and math.isnan(t["std"]) and math.isnan(t["ci95_low"])  # n/a, not zero
    assert t_critical_95(1) == 12.706 and t_critical_95(500) == 1.96 and math.isnan(t_critical_95(0))
