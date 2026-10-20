"""Phase 7: the NASA real-traffic trace, tuning, and the CPU-derived demand signal."""

from __future__ import annotations

import gzip
import json

import numpy as np
import pandas as pd
import pytest

from scalix import tuning
from scalix.algorithms import CapacityLimits, ScalixPolicy, StaticPolicy
from scalix.config import ConfigError, ControllerSettings
from scalix.simulation import ExperimentSpec, SimulationConfig, WorkloadSpec, generate, simulate
from scalix.simulation import nasa
from scalix.simulation.workloads import SYNTHETIC_SCENARIOS, Scenario, expected_rate

# Daily totals of the raw July 1995 log (counted independently while exploring the data).
DAY_TOTALS = {1: 64714, 2: 60265, 13: 134203, 27: 61680}


# --------------------------------------------------------------------------- NASA trace

def test_committed_trace_has_every_request_of_the_log():
    counts = nasa.load_counts()
    assert int(counts.sum()) == 1_891_714          # 1,891,715 lines; the last one is truncated
    assert len(counts) * 5 >= 27 * 86_400 and (counts >= 0).all()


@pytest.mark.parametrize("day,total", DAY_TOTALS.items())
def test_day_counts_match_the_log(day, total):
    assert nasa.day_counts(day, 86_400, 5).sum() == total
    assert nasa.day_counts(day, 86_400, 15).sum() == total       # coarser steps just add bins


def test_nasa_scenario_is_the_real_day_scaled():
    spec = WorkloadSpec(scenario=Scenario.NASA, duration_seconds=86_400, seed=13)
    tr = generate(spec)
    assert tr.total_arrivals == pytest.approx(134_203 * 2.37)
    assert np.array_equal(tr.arrivals, generate(spec).arrivals)          # deterministic
    assert np.array_equal(tr.arrivals, nasa.day_counts(13, 86_400, 5) * 2.37)
    assert tr.expected_rate.mean() == pytest.approx(tr.arrivals.mean() / 5, rel=0.01)
    half = generate(WorkloadSpec(scenario=Scenario.NASA, duration_seconds=43_200, seed=13, nasa_scale=1.0))
    assert half.total_arrivals == nasa.day_counts(13, 43_200, 5).sum()


@pytest.mark.parametrize("bad", [dict(seed=0), dict(seed=28), dict(duration_seconds=90_000),
                                 dict(step_seconds=3, duration_seconds=86_400)])
def test_nasa_scenario_rejects_invalid_requests(bad):
    spec = dict(scenario=Scenario.NASA, duration_seconds=86_400, seed=1) | bad
    with pytest.raises(ValueError):
        generate(WorkloadSpec(**spec))


def test_nasa_has_no_formula_and_is_not_a_default_scenario():
    with pytest.raises(ValueError):
        expected_rate(WorkloadSpec(scenario=Scenario.NASA, seed=1), np.array([0.0]))
    assert Scenario.NASA not in SYNTHETIC_SCENARIOS
    assert Scenario.NASA not in ExperimentSpec().scenarios


def test_convert_log_counts_timestamps_only(tmp_path):
    lines = [
        b'host1 - - [01/Jul/1995:00:00:01 -0400] "GET /a HTTP/1.0" 200 10',
        b'host2 - - [01/Jul/1995:00:00:04 -0400] "GET /b HTTP/1.0" 200 10',
        b'host3 - - [01/Jul/1995:00:00:05 -0400] "GET /c HTTP/1.0" 404 0',
        b'garbage line with no timestamp',
        b'host4 - - [02/Jul/1995:00:00:00 -0400] "GET /d HTTP/1.0" 200 10',
    ]
    log = tmp_path / "log.gz"
    log.write_bytes(gzip.compress(b"\n".join(lines) + b"\n"))
    out = tmp_path / "counts.csv.gz"
    assert nasa.convert_log(log, out) == (4, 1)
    first = out.read_bytes()
    nasa.convert_log(log, out)
    assert out.read_bytes() == first                                   # byte-identical rebuild
    text = gzip.decompress(first).decode()
    assert "host" not in text and "GET" not in text
    rows = text.split()
    assert rows[0] == "requests" and rows[1:3] == ["2", "1"]
    assert int(rows[1 + 86_400 // 5]) == 1 and sum(map(int, rows[1:])) == 4


# --------------------------------------------------------------------------- CPU-derived demand signal

def constant(rate, seconds=300):
    return generate(WorkloadSpec(scenario=Scenario.RISING, duration_seconds=seconds, base_rate=rate,
                                 rising_start=1.0, rising_end=1.0, poisson_noise=False))


def test_cpu_signal_equals_demand_below_saturation():
    r = simulate(StaticPolicy(worker_count=8), constant(4.0), SimulationConfig(demand_signal="cpu"))
    ev = r.events
    assert np.allclose(ev["demand_signal_rps"], ev["arrival_rate_rps"])


def test_cpu_signal_under_reports_demand_when_saturated():
    r = simulate(StaticPolicy(worker_count=2), constant(4.0), SimulationConfig(demand_signal="cpu"))
    ev = r.events
    assert np.allclose(ev["demand_signal_rps"], 2.0) and np.allclose(ev["arrival_rate_rps"], 4.0)


def test_arrivals_signal_is_the_default_and_unchanged():
    r = simulate(StaticPolicy(worker_count=2), constant(4.0))
    assert np.allclose(r.events["demand_signal_rps"], r.events["arrival_rate_rps"])


def test_scalix_sees_the_cpu_signal():
    # The spike saturates the workers for a while, so served load (CPU) falls short of demand there.
    tr = generate(WorkloadSpec(scenario=Scenario.SPIKE, seed=4))
    full = simulate(ScalixPolicy(capacity=CapacityLimits()), tr)
    cpu = simulate(ScalixPolicy(capacity=CapacityLimits()), tr, SimulationConfig(demand_signal="cpu"))
    ev = cpu.events
    # Under-reports while requests queue, over-reports while the backlog drains afterwards...
    assert (ev["demand_signal_rps"] < ev["arrival_rate_rps"] - 1e-6).any()
    assert (ev["demand_signal_rps"] > ev["arrival_rate_rps"] + 1e-6).any()
    # ...but over the whole run it serves exactly what arrived minus what is still waiting.
    served = (ev["demand_signal_rps"] * 15).sum()
    arrived = (ev["arrival_rate_rps"] * 15).sum()
    assert served <= arrived + 1e-6
    assert not full.events["demand_signal_rps"].equals(ev["demand_signal_rps"])
    assert (ev["trend"] != "UNKNOWN").sum() > 0.9 * len(ev)


def test_demand_signal_validation():
    with pytest.raises(ValueError):
        SimulationConfig(demand_signal="loadbalancer")
    assert ControllerSettings().demand_signal == "CPU"
    assert ControllerSettings(demand_signal="none").demand_signal == "NONE"
    with pytest.raises(ConfigError):
        ControllerSettings(demand_signal="ALB")


# --------------------------------------------------------------------------- tuning

def fake_runs(rows):
    return pd.DataFrame([dict(strategy=s, setting=i, params=json.dumps({"k": i}), scenario=sc, seed=1,
                              p95_s=p, avg_s=p / 2, cost=c, worker_minutes=c * 100)
                         for s, i, sc, p, c in rows])


def test_select_picks_the_cheapest_setting_that_meets_the_target_everywhere():
    runs = fake_runs([
        ("X", 0, "A", 8.0, 1.0), ("X", 0, "B", 9.0, 1.0),     # meets, cost 1.0
        ("X", 1, "A", 7.0, 0.5), ("X", 1, "B", 11.0, 0.5),    # cheaper but misses in B
        ("X", 2, "A", 6.0, 2.0), ("X", 2, "B", 6.0, 2.0),     # meets, dearer
    ])
    table, chosen = tuning.select(runs, slo=10.0)
    assert chosen["X"]["params"] == {"k": 0} and chosen["X"]["meets_slo"]
    assert chosen["X"]["settings_meeting_slo"] == 2 and table["chosen"].sum() == 1


def test_select_falls_back_to_the_fastest_and_flags_it():
    runs = fake_runs([("X", 0, "A", 30.0, 1.0), ("X", 1, "A", 12.0, 5.0)])
    _, chosen = tuning.select(runs, slo=10.0)
    assert chosen["X"]["params"] == {"k": 1} and not chosen["X"]["meets_slo"]


def test_training_and_test_seeds_never_overlap_and_grids_are_valid():
    from scalix.evaluation import BASE_SEED, SEEDS
    assert not set(tuning.TRAINING_SEEDS) & set(range(BASE_SEED, BASE_SEED + SEEDS))
    for strategy, grid in tuning.GRIDS.items():
        assert len(grid) == len({json.dumps(p, sort_keys=True) for p in grid})
        for params in grid:
            tuning.make_policy(strategy, params)


def test_small_tuning_run_is_reproducible():
    grids = {"STATIC": [dict(worker_count=6), dict(worker_count=12)],
             "THRESHOLD": [dict(upper=0.7, lower=0.3, cooldown_seconds=60.0)]}
    kw = dict(seeds=(101,), scenarios=(Scenario.SPIKE,), grids=grids)
    a, b = tuning.evaluate_grid(**kw), tuning.evaluate_grid(**kw)
    pd.testing.assert_frame_equal(a, b)
    _, chosen = tuning.select(a)
    assert set(chosen) == {"STATIC", "THRESHOLD"}


def test_committed_tuning_result_is_consistent():
    path = tuning.TUNING_ROOT / "tuned.json"
    if not path.is_file():
        pytest.skip("tuning output not present")
    manifest = json.loads(path.read_text())
    assert manifest["training_seeds"] == list(tuning.TRAINING_SEEDS) and manifest["data_source"] == "SIMULATED"
    grid = pd.read_csv(tuning.TUNING_ROOT / "grid.csv")
    for strategy, info in manifest["chosen"].items():
        tuning.make_policy(strategy, info["params"])
        row = grid[(grid.strategy == strategy) & grid.chosen]
        assert len(row) == 1 and json.loads(row.params.iloc[0]) == info["params"]
        assert len(grid[grid.strategy == strategy]) == len(tuning.GRIDS[strategy])


def test_nasa_scale_is_one_constant_and_maps_the_busiest_minute_to_80_percent_of_max():
    from scalix.algorithms import CapacityLimits

    assert WorkloadSpec().nasa_scale == nasa.DEFAULT_SCALE
    counts = nasa.load_counts()[: nasa.FULL_DAYS * 17_280].astype(float)
    busiest_minute_rps = counts.reshape(-1, 12).sum(axis=1).max() / 60
    assert busiest_minute_rps == pytest.approx(6.75)
    scaled = busiest_minute_rps * nasa.DEFAULT_SCALE
    assert scaled == pytest.approx(0.80 * CapacityLimits().maximum, abs=0.1)
