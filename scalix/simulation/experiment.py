"""Run every policy on identical workloads, repeat over seeds, and save the results.

For each scenario and each repetition ``i`` (seed = ``base_seed + i``), one workload trace is
generated **once** and handed unchanged to every policy. The policies all start from the same
number of workers and share one SimulationConfig, so the decision logic is the only thing that
differs between them.

Output folder (default ``results/simulations/<experiment_id>/``):

* ``summary.csv``: one row per run (scenario x policy x seed) with every metric
* ``aggregate.csv``: n / mean / std / min / max / 95% CI per scenario x policy x metric
* ``timeseries.csv``: one row per step per run
* ``events.csv``: the decision log, one row per decision per run, with reasons
* ``workloads.csv``: the exact arrivals each repetition used
* ``experiment.json``: every setting needed to reproduce the run

Everything in these files is SIMULATED, and cost is an illustrative estimate, not an AWS charge.
"""

from __future__ import annotations

import json
import platform
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import numpy as np
import pandas as pd

import scalix
from scalix.algorithms import CapacityLimits, ScalingPolicy, ScalixPolicy, StaticPolicy, ThresholdPolicy
from scalix.metrics.stats import aggregate
from scalix.simulation.engine import RunResult, SimulationConfig, simulate
from scalix.simulation.workloads import Scenario, WorkloadSpec, generate, parse_scenario

#: Metrics included in aggregate.csv.
REPORT_METRICS = (
    "avg_utilization", "peak_utilization", "time_above_threshold_s", "avg_workers", "peak_workers",
    "worker_minutes", "scale_up_events", "scale_down_events", "cooldown_blocks", "time_with_queue_s",
    "peak_queue_requests", "unserved_at_end", "served_fraction", "response_time_proxy_avg_s",
    "response_time_proxy_p95_s", "illustrative_cost",
)

DATA_LABEL = ("SIMULATED: utilization is estimated, response time is a documented proxy, and cost is an "
              "illustrative estimate. None of these are AWS measurements or AWS charges.")


def default_policies(capacity: CapacityLimits | None = None) -> tuple[ScalingPolicy, ...]:
    """The three strategies with their default settings, all starting from the same capacity."""
    capacity = capacity or CapacityLimits()
    return (StaticPolicy(worker_count=capacity.initial), ThresholdPolicy(capacity=capacity),
            ScalixPolicy(capacity=capacity))


@dataclass(frozen=True)
class ExperimentSpec:
    scenarios: tuple[Scenario, ...] = tuple(Scenario)
    repetitions: int = 5
    base_seed: int = 42
    workload: WorkloadSpec = field(default_factory=WorkloadSpec)  # scenario and seed are overridden
    simulation: SimulationConfig = field(default_factory=SimulationConfig)
    policies: tuple[ScalingPolicy, ...] = field(default_factory=default_policies)

    def __post_init__(self) -> None:
        object.__setattr__(self, "scenarios", tuple(parse_scenario(s) for s in self.scenarios))
        object.__setattr__(self, "policies", tuple(self.policies))
        if not self.scenarios:
            raise ValueError("choose at least one scenario")
        if len(set(self.scenarios)) != len(self.scenarios):
            raise ValueError("each scenario may appear only once")
        if not (isinstance(self.repetitions, int) and not isinstance(self.repetitions, bool)
                and 1 <= self.repetitions <= 100):
            raise ValueError(f"repetitions must be a whole number from 1 to 100, got {self.repetitions!r}")
        if not (isinstance(self.base_seed, int) and not isinstance(self.base_seed, bool) and self.base_seed >= 0):
            raise ValueError(f"base_seed must be a whole number >= 0, got {self.base_seed!r}")
        if not self.policies:
            raise ValueError("choose at least one policy")
        names = [p.name for p in self.policies]
        if len(set(names)) != len(names):
            raise ValueError(f"each policy may appear only once, got {names}")
        initial = {p.limits.initial for p in self.policies}
        if len(initial) != 1:
            raise ValueError(f"all policies must start with the same number of workers for a fair "
                             f"comparison, got {sorted(initial)}")

    def seeds(self) -> list[int]:
        return [self.base_seed + i for i in range(self.repetitions)]

    def to_dict(self) -> dict[str, Any]:
        return {
            "scenarios": [s.value for s in self.scenarios],
            "repetitions": self.repetitions,
            "base_seed": self.base_seed,
            "seeds": self.seeds(),
            "workload": {k: v for k, v in self.workload.to_dict().items() if k not in ("scenario", "seed")},
            "simulation": self.simulation.to_dict(),
            "policies": [{"name": p.name, "parameters": p.parameters()} for p in self.policies],
        }


@dataclass(frozen=True)
class ExperimentResult:
    spec: ExperimentSpec
    summary: pd.DataFrame
    aggregate: pd.DataFrame
    timeseries: pd.DataFrame
    events: pd.DataFrame
    workloads: pd.DataFrame
    runs: tuple[RunResult, ...]


def run_experiment(spec: ExperimentSpec | None = None) -> ExperimentResult:
    spec = spec or ExperimentSpec()
    summaries, series, events, workloads, runs = [], [], [], [], []
    for scenario in spec.scenarios:
        for rep, seed in enumerate(spec.seeds(), start=1):
            trace = generate(replace(spec.workload, scenario=scenario, seed=seed))
            keys = {"scenario": scenario.value, "repetition": rep, "seed": seed}
            workloads.append(trace.to_frame().assign(**keys))
            for policy in spec.policies:
                result = simulate(policy, trace, spec.simulation)
                runs.append(result)
                run_keys = {**keys, "policy": policy.name}
                summaries.append({**run_keys, **result.summary})
                series.append(result.timeseries.assign(**run_keys))
                events.append(result.events.assign(**run_keys))

    def front(df: pd.DataFrame, cols: Sequence[str]) -> pd.DataFrame:
        return df[list(cols) + [c for c in df.columns if c not in cols]]

    run_cols = ("scenario", "policy", "repetition", "seed")
    summary = front(pd.DataFrame(summaries), run_cols)
    return ExperimentResult(
        spec=spec,
        summary=summary,
        aggregate=aggregate(summary, REPORT_METRICS),
        timeseries=front(pd.concat(series, ignore_index=True), run_cols),
        events=front(pd.concat(events, ignore_index=True), run_cols),
        workloads=front(pd.concat(workloads, ignore_index=True), ("scenario", "repetition", "seed")),
        runs=tuple(runs),
    )


def save_experiment(result: ExperimentResult, out_dir: str | Path, experiment_id: str | None = None) -> Path:
    """Write the result files to ``out_dir`` (created if needed) and return the folder."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    result.summary.to_csv(out / "summary.csv", index=False)
    result.aggregate.to_csv(out / "aggregate.csv", index=False)
    result.timeseries.to_csv(out / "timeseries.csv", index=False)
    result.events.to_csv(out / "events.csv", index=False)
    result.workloads.to_csv(out / "workloads.csv", index=False)
    manifest = {
        "experiment_id": experiment_id or out.name,
        "data_label": DATA_LABEL,
        "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "scalix_version": scalix.__version__,
        "python": platform.python_version(),
        "numpy": np.__version__,
        "pandas": pd.__version__,
        "spec": result.spec.to_dict(),
        "files": ["summary.csv", "aggregate.csv", "timeseries.csv", "events.csv", "workloads.csv"],
    }
    (out / "experiment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return out


def headline_table(result: ExperimentResult) -> pd.DataFrame:
    """A compact mean-per-scenario-and-policy table for printing."""
    cols = {"response_time_proxy_avg_s": "resp_proxy_avg_s", "response_time_proxy_p95_s": "resp_proxy_p95_s",
            "avg_utilization": "avg_util", "worker_minutes": "worker_min", "illustrative_cost": "illus_cost",
            "scale_up_events": "ups", "scale_down_events": "downs", "time_above_threshold_s": "above_thr_s",
            "unserved_at_end": "unserved_end"}
    table = (result.summary.groupby(["scenario", "policy"], sort=False)[list(cols)].mean()
             .rename(columns=cols))
    return table
