"""Workload scenarios: shapes, reproducibility and validation."""

from __future__ import annotations

from dataclasses import replace

import numpy as np
import pytest

from scalix.simulation.workloads import SYNTHETIC_SCENARIOS, Scenario, WorkloadSpec, expected_rate, generate

SMOOTH = dict(poisson_noise=False)


def spec(scenario, **kw) -> WorkloadSpec:
    return WorkloadSpec(scenario=scenario, **kw)


@pytest.mark.parametrize("scenario", SYNTHETIC_SCENARIOS)
def test_every_scenario_generates_a_valid_trace(scenario):
    tr = generate(spec(scenario))
    assert len(tr.times) == len(tr.arrivals) == len(tr.expected_rate) == 360  # 1800 s / 5 s
    assert tr.times[0] == 0 and tr.times[-1] == 1795
    assert (tr.arrivals >= 0).all() and (tr.expected_rate >= 0).all()
    assert np.all(tr.arrivals == np.round(tr.arrivals))  # Poisson counts are whole requests
    assert tr.to_frame().shape == (360, 5)
    assert (tr.to_frame()["data_source"] == "SIMULATED").all()


def test_normal_fluctuates_moderately_around_the_base():
    r = generate(spec(Scenario.NORMAL, **SMOOTH)).expected_rate
    assert r.min() >= 5 * 0.75 - 1e-9 and r.max() <= 5 * 1.25 + 1e-9
    assert r.mean() == pytest.approx(5.0, abs=0.1)
    assert r.max() - r.min() > 2  # it really fluctuates


def test_rising_increases_gradually():
    r = generate(spec(Scenario.RISING, **SMOOTH)).expected_rate
    assert np.all(np.diff(r) >= 0)
    assert r[0] == pytest.approx(2.5) and r[-1] == pytest.approx(12.5, abs=0.05)
    assert np.max(np.diff(r)) < 0.05  # no sudden jumps


def test_spike_jumps_suddenly_then_declines_back():
    s = spec(Scenario.SPIKE, **SMOOTH)
    r = generate(s).expected_rate
    t = np.arange(360) * 5.0
    jump = int(np.argmax(r > 5.0))
    assert t[jump] == pytest.approx(600, abs=5)  # one third of 1800 s
    assert r[jump - 1] == pytest.approx(5.0) and r[jump] == pytest.approx(15.0)  # sudden: one step
    after = r[jump:]
    peak_end = jump + int(np.argmax(after < 15.0))
    assert np.all(np.diff(r[peak_end - 1:]) <= 1e-12)  # then only declines / flat
    assert r[-1] == pytest.approx(5.0)


def test_mixed_contains_every_pattern():
    s = spec(Scenario.MIXED, **SMOOTH)
    at = lambda sec: float(expected_rate(s, np.array([sec]))[0])  # noqa: E731
    assert 3.75 <= at(100) <= 6.25              # normal
    assert at(500) < at(800)                    # rising part
    assert at(899) == pytest.approx(7.5, abs=0.05)  # rise ends at 1.5 x base
    assert at(900) == pytest.approx(15.0)       # spike
    assert at(1100) < at(1000)                  # decline
    assert 2.25 - 1e-9 <= at(1500) <= 3.75 + 1e-9  # quiet normal around 0.6 x base


def test_same_spec_same_trace_and_seed_changes_it():
    a, b = generate(spec(Scenario.SPIKE, seed=7)), generate(spec(Scenario.SPIKE, seed=7))
    assert np.array_equal(a.arrivals, b.arrivals)
    assert not np.array_equal(a.arrivals, generate(spec(Scenario.SPIKE, seed=8)).arrivals)


def test_scenarios_with_the_same_seed_are_independent_draws():
    n = generate(spec(Scenario.NORMAL, seed=1)).arrivals[:50]
    s = generate(spec(Scenario.SPIKE, seed=1)).arrivals[:50]  # both at base rate here
    assert not np.array_equal(n, s)


def test_noise_off_gives_the_expected_curve():
    tr = generate(spec(Scenario.RISING, **SMOOTH))
    assert np.allclose(tr.arrivals, tr.expected_rate * 5.0)


def test_intensity_scales_demand():
    low = generate(spec(Scenario.MIXED, base_rate=2.0, **SMOOTH)).expected_rate
    high = generate(spec(Scenario.MIXED, base_rate=6.0, **SMOOTH)).expected_rate
    assert np.allclose(high, 3 * low)


def test_duration_and_step_are_configurable():
    tr = generate(spec(Scenario.NORMAL, duration_seconds=600, step_seconds=10))
    assert len(tr.times) == 60 and tr.step_seconds == 10


def test_traces_are_read_only():
    tr = generate(spec(Scenario.NORMAL))
    with pytest.raises(ValueError):
        tr.arrivals[0] = 999


@pytest.mark.parametrize("kwargs", [
    dict(duration_seconds=1000, step_seconds=3), dict(step_seconds=0), dict(duration_seconds=-5),
    dict(base_rate=-1), dict(base_rate=float("nan")), dict(seed=-1), dict(seed=True), dict(seed=1.5),
    dict(fluctuation=1.0), dict(fluctuation=-0.1), dict(spike_multiplier=-2),
    dict(spike_start_fraction=0.8, spike_hold_fraction=0.2, spike_decline_fraction=0.2),
    dict(scenario="WEEKEND"), dict(poisson_noise="yes"),
])
def test_invalid_workload_settings_are_rejected(kwargs):
    with pytest.raises(ValueError):
        WorkloadSpec(**kwargs)


def test_scenario_names_are_case_insensitive():
    assert WorkloadSpec(scenario=" spike ").scenario is Scenario.SPIKE
    assert replace(WorkloadSpec(), scenario="mixed").scenario is Scenario.MIXED
