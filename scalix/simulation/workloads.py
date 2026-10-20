"""Workload scenarios: how many requests arrive in each time step.

A workload has two layers:

1. An **expected rate** curve, λ(t) in requests/second. This is the scenario's shape.
2. **Random arrivals** around that curve. Each time step draws a Poisson-distributed count with
   mean ``λ(t) x step``. That's the standard model for independent requests arriving at random,
   and it gives realistic noise. Turn it off with ``poisson_noise=False`` to get the smooth
   expected curve.

Scenarios (``base_rate`` is the "demand intensity"; every rate below is a multiple of it):

* ``NORMAL``: moderate fluctuation around the base rate, a sine wave of +/- ``fluctuation``.
* ``RISING``: a gradual straight-line rise from ``rising_start`` x base to ``rising_end`` x base.
* ``SPIKE``: base rate, then a sudden jump to ``spike_multiplier`` x base, a short hold, a straight
  decline back to base, then base again.
* ``MIXED``: all of the above in sequence. In fractions of the duration:
  0-25% normal; 25-50% gradual rise to 1.5x; 50-55% sudden spike; 55-75% decline to 0.6x;
  75-100% quiet, fluctuating around 0.6x.

* ``NASA`` (Phase 7): **real traffic**, one day of the NASA Kennedy Space Center web log from
  July 1995, replayed in real time and scaled by ``nasa_scale``. Here ``seed`` is the day of July
  (1..27), ``base_rate``, ``poisson_noise`` and the shape settings are ignored, and the run lasts at
  most one day. See ``scalix.simulation.nasa``.

The same ``WorkloadSpec`` (including ``seed``) always produces exactly the same trace. Each
scenario mixes its own name into the seed, so NORMAL and SPIKE with seed 42 are independent
draws, but each is reproducible.
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass
from enum import Enum

import numpy as np
import pandas as pd

from scalix.simulation.nasa import DEFAULT_SCALE as NASA_DEFAULT_SCALE


class Scenario(str, Enum):
    NORMAL = "NORMAL"
    RISING = "RISING"
    SPIKE = "SPIKE"
    MIXED = "MIXED"
    NASA = "NASA"


#: The four generated (synthetic) scenarios; NASA is real traffic and is run separately.
SYNTHETIC_SCENARIOS = (Scenario.NORMAL, Scenario.RISING, Scenario.SPIKE, Scenario.MIXED)

_SCENARIO_SEED_SALT = {Scenario.NORMAL: 101, Scenario.RISING: 202, Scenario.SPIKE: 303, Scenario.MIXED: 404}


def parse_scenario(value: str | Scenario) -> Scenario:
    if isinstance(value, Scenario):
        return value
    try:
        return Scenario(str(value).strip().upper())
    except ValueError:
        raise ValueError(f"unknown scenario {value!r}; valid: {', '.join(s.value for s in Scenario)}") from None


def _positive(name: str, value: float) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0):
        raise ValueError(f"{name} must be a finite number > 0, got {value!r}")


def _non_negative(name: str, value: float) -> None:
    if not (isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value >= 0):
        raise ValueError(f"{name} must be a finite number >= 0, got {value!r}")


def _fraction(name: str, value: float) -> None:
    _non_negative(name, value)
    if value > 1:
        raise ValueError(f"{name} must be within [0, 1], got {value!r}")


@dataclass(frozen=True)
class WorkloadSpec:
    """Everything needed to generate one workload trace. All times are in seconds."""

    scenario: Scenario = Scenario.NORMAL
    duration_seconds: float = 1800.0
    step_seconds: float = 5.0
    base_rate: float = 5.0
    seed: int = 42
    poisson_noise: bool = True
    # NORMAL (and the normal parts of MIXED)
    fluctuation: float = 0.25
    fluctuation_period_seconds: float = 480.0
    # RISING
    rising_start: float = 0.5
    rising_end: float = 2.5
    # SPIKE (and the spike in MIXED)
    spike_multiplier: float = 3.0
    spike_start_fraction: float = 1 / 3
    spike_hold_fraction: float = 0.10
    spike_decline_fraction: float = 0.25
    # NASA (real traffic): multiplier applied to the real request counts
    nasa_scale: float = NASA_DEFAULT_SCALE

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenario", parse_scenario(self.scenario))
        _positive("duration_seconds", self.duration_seconds)
        _positive("step_seconds", self.step_seconds)
        steps = self.duration_seconds / self.step_seconds
        if abs(steps - round(steps)) > 1e-9:
            raise ValueError(f"duration_seconds ({self.duration_seconds}) must be a whole number of "
                             f"steps of {self.step_seconds} s")
        _non_negative("base_rate", self.base_rate)
        if not (isinstance(self.seed, int) and not isinstance(self.seed, bool) and self.seed >= 0):
            raise ValueError(f"seed must be a whole number >= 0, got {self.seed!r}")
        if not isinstance(self.poisson_noise, bool):
            raise ValueError("poisson_noise must be True or False")
        _non_negative("fluctuation", self.fluctuation)
        if self.fluctuation >= 1:
            raise ValueError("fluctuation must be < 1 so the rate never goes negative")
        _positive("fluctuation_period_seconds", self.fluctuation_period_seconds)
        _non_negative("rising_start", self.rising_start)
        _non_negative("rising_end", self.rising_end)
        _non_negative("spike_multiplier", self.spike_multiplier)
        for name in ("spike_start_fraction", "spike_hold_fraction", "spike_decline_fraction"):
            _fraction(name, getattr(self, name))
        if self.spike_start_fraction + self.spike_hold_fraction + self.spike_decline_fraction > 1 + 1e-9:
            raise ValueError("spike start + hold + decline fractions must fit within the duration (sum <= 1)")
        _positive("nasa_scale", self.nasa_scale)

    @property
    def steps(self) -> int:
        return int(round(self.duration_seconds / self.step_seconds))

    def to_dict(self) -> dict:
        d = asdict(self)
        d["scenario"] = self.scenario.value
        return d


@dataclass(frozen=True)
class WorkloadTrace:
    """A generated workload: one row per time step. Shared, unchanged, by every policy."""

    spec: WorkloadSpec
    times: np.ndarray          # start time of each step (s)
    expected_rate: np.ndarray  # λ(t), requests/s
    arrivals: np.ndarray       # requests arriving during each step

    @property
    def step_seconds(self) -> float:
        return self.spec.step_seconds

    @property
    def total_arrivals(self) -> float:
        return float(self.arrivals.sum())

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "data_source": "SIMULATED",
            "time_s": self.times,
            "expected_rate_rps": self.expected_rate,
            "arrivals": self.arrivals,
            "arrival_rate_rps": self.arrivals / self.spec.step_seconds,
        })


def _sine(t: np.ndarray, level: float, spec: WorkloadSpec) -> np.ndarray:
    return level * (1 + spec.fluctuation * np.sin(2 * np.pi * t / spec.fluctuation_period_seconds))


def _ramp(t: np.ndarray, t0: float, t1: float, r0: float, r1: float) -> np.ndarray:
    if t1 <= t0:
        return np.full_like(t, r1)
    return r0 + (r1 - r0) * np.clip((t - t0) / (t1 - t0), 0, 1)


def expected_rate(spec: WorkloadSpec, t: np.ndarray) -> np.ndarray:
    """The scenario's expected arrival rate λ(t) in requests/s, at the times ``t`` (seconds)."""
    t = np.asarray(t, dtype=float)
    if spec.scenario is Scenario.NASA:
        raise ValueError("the NASA scenario is real traffic and has no formula; use generate(spec).expected_rate")
    b = spec.base_rate
    T = spec.duration_seconds
    s = spec.scenario

    if s is Scenario.NORMAL:
        return _sine(t, b, spec)

    if s is Scenario.RISING:
        return _ramp(t, 0.0, T, spec.rising_start * b, spec.rising_end * b)

    if s is Scenario.SPIKE:
        t_jump = spec.spike_start_fraction * T
        t_hold_end = t_jump + spec.spike_hold_fraction * T
        t_decline_end = t_hold_end + spec.spike_decline_fraction * T
        peak = spec.spike_multiplier * b
        rate = np.full_like(t, b)
        rate = np.where((t >= t_jump) & (t < t_hold_end), peak, rate)
        declining = (t >= t_hold_end) & (t < t_decline_end)
        rate = np.where(declining, _ramp(t, t_hold_end, t_decline_end, peak, b), rate)
        return rate

    # MIXED
    q1, q2, q_spike, q3 = 0.25 * T, 0.50 * T, 0.55 * T, 0.75 * T
    peak = spec.spike_multiplier * b
    quiet = 0.6 * b
    return np.select(
        [t < q1, t < q2, t < q_spike, t < q3],
        [_sine(t, b, spec), _ramp(t, q1, q2, b, 1.5 * b), np.full_like(t, peak),
         _ramp(t, q_spike, q3, peak, quiet)],
        default=_sine(t, quiet, spec),
    )


def _nasa_trace(spec: WorkloadSpec) -> WorkloadTrace:
    from scalix.simulation.nasa import day_counts

    times = np.arange(spec.steps, dtype=float) * spec.step_seconds
    arrivals = day_counts(spec.seed, spec.duration_seconds, spec.step_seconds) * spec.nasa_scale
    window = max(1, int(round(60 / spec.step_seconds)))
    smooth = np.convolve(arrivals, np.ones(window) / window, mode="same") / spec.step_seconds
    for arr in (times, smooth, arrivals):
        arr.setflags(write=False)
    return WorkloadTrace(spec=spec, times=times, expected_rate=smooth, arrivals=arrivals)


def generate(spec: WorkloadSpec) -> WorkloadTrace:
    """Generate the trace for ``spec``. Same spec -> identical trace, on every run."""
    if spec.scenario is Scenario.NASA:
        return _nasa_trace(spec)
    times = np.arange(spec.steps, dtype=float) * spec.step_seconds
    rate = np.maximum(expected_rate(spec, times), 0.0)
    if spec.poisson_noise:
        rng = np.random.default_rng(np.random.SeedSequence([spec.seed, _SCENARIO_SEED_SALT[spec.scenario]]))
        arrivals = rng.poisson(rate * spec.step_seconds).astype(float)
    else:
        arrivals = rate * spec.step_seconds
    for arr in (times, rate, arrivals):
        arr.setflags(write=False)
    return WorkloadTrace(spec=spec, times=times, expected_rate=rate, arrivals=arrivals)
