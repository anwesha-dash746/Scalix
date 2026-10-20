"""Parameter tuning on training seeds, kept separate from the test seeds (Phase 7).

Why: the original comparison used every strategy's *starting* settings. A fairer question is
"what is the cheapest each strategy can do while still meeting the same response-time target?"
Tuning on the same seeds that are then reported would flatter whichever strategy has more knobs,
so tuning uses its own seeds and the evaluation reports the tuned settings on unseen ones.

The rules, fixed here before running:

* **Training data:** the four synthetic scenarios with seeds 101-110. The evaluation's test
  seeds (1-10) and the NASA real-traffic days are never used here.
* **Target (service-level objective):** the average p95 response time from the queueing model
  must be at most ``SLO_P95_SECONDS`` (10 s) in *every* scenario. An idle system already has a
  p95 of about 6 s (exponential service times), so this allows about 4 s of extra waiting at the
  slowest 5% of requests.
* **Objective:** among settings that meet the target, the lowest average estimated cost (AWS list
  prices, T3 credits included) across the four scenarios. Ties go to the lower average p95. If no
  setting meets the target, the one with the smallest worst-case p95 is chosen and flagged.
* **Search space:** a grid around each strategy's defaults (``GRIDS``): every fleet size for
  Static, 92 settings for Threshold (deliberately wide, as it has only three knobs) and 126 for
  SCALIX. Threshold and SCALIX keep the capacity limits of 1-20 workers starting at 8; Static's
  fleet size is its only setting, so it is tuned.

Run ``python -m scalix.tuning run`` (a few minutes). It writes ``results/tuning/grid.csv`` (every
setting's training results) and ``results/tuning/tuned.json`` (the chosen settings). The evaluation
reads ``tuned.json``. Everything is SIMULATED.
"""

from __future__ import annotations

import argparse
import itertools
import json
import math
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pandas as pd

from scalix.algorithms import CapacityLimits, ScalixPolicy, StaticPolicy, ThresholdPolicy
from scalix.config import PROJECT_ROOT
from scalix.simulation import SimulationConfig, WorkloadSpec, generate, simulate
from scalix.simulation.workloads import SYNTHETIC_SCENARIOS

TUNING_ROOT = PROJECT_ROOT / "results" / "tuning"
TRAINING_SEEDS = tuple(range(101, 111))
SLO_P95_SECONDS = 10.0
CAPACITY = CapacityLimits()


def _threshold_grid() -> list[dict[str, Any]]:
    # Threshold has only three knobs, so its grid is wide (down to scaling up at 40% busy and a 15 s
    # cooldown, i.e. one decision interval) to give it every chance to meet the target.
    return [dict(upper=u, lower=lo, cooldown_seconds=cd)
            for u, lo, cd in itertools.product((0.4, 0.5, 0.6, 0.7, 0.8, 0.9), (0.1, 0.2, 0.3, 0.4),
                                               (15.0, 30.0, 60.0, 120.0))
            if lo < u]


def _scalix_grid() -> list[dict[str, Any]]:
    grid = []
    for upper in (0.7, 0.8):
        for target in (0.5, 0.6, 0.7, 0.8):
            if target > upper:
                continue
            for pre, down_cd, low in itertools.product((0.45, 0.55, 0.65), (30.0, 60.0, 90.0), (1, 2)):
                grid.append(dict(upper=upper, target_utilization=target, preemptive_upper=pre,
                                 scale_down_cooldown_seconds=down_cd, low_intervals_required=low))
    return grid


GRIDS: dict[str, list[dict[str, Any]]] = {
    "STATIC": [dict(worker_count=n) for n in range(CAPACITY.minimum, CAPACITY.maximum + 1)],
    "THRESHOLD": _threshold_grid(),
    "SCALIX": _scalix_grid(),
}


def make_policy(strategy: str, params: dict[str, Any], capacity: CapacityLimits = CAPACITY):
    if strategy == "STATIC":
        return StaticPolicy(worker_count=int(params["worker_count"]))
    if strategy == "THRESHOLD":
        return ThresholdPolicy(capacity=capacity, **params)
    if strategy == "SCALIX":
        return ScalixPolicy(capacity=capacity, **params)
    raise ValueError(f"unknown strategy {strategy!r}")


def evaluate_grid(seeds=TRAINING_SEEDS, scenarios=SYNTHETIC_SCENARIOS, grids=None,
                  simulation: SimulationConfig | None = None, workload: WorkloadSpec | None = None,
                  progress=None) -> pd.DataFrame:
    """Every setting x scenario x seed: one row per run with the metrics the selection needs."""
    grids = grids or GRIDS
    workload = workload or WorkloadSpec()
    rows = []
    for scenario in scenarios:
        for seed in seeds:
            trace = generate(replace(workload, scenario=scenario, seed=seed))
            for strategy, grid in grids.items():
                for index, params in enumerate(grid):
                    s = simulate(make_policy(strategy, params), trace, simulation).summary
                    rows.append({"strategy": strategy, "setting": index, "params": json.dumps(params, sort_keys=True),
                                 "scenario": scenario.value, "seed": seed,
                                 "p95_s": s["response_time_model_p95_s"], "avg_s": s["response_time_model_avg_s"],
                                 "cost": s["estimated_cost"], "worker_minutes": s["worker_minutes"]})
            if progress:
                progress(f"{scenario.value} seed {seed} done")
    return pd.DataFrame(rows)


def select(runs: pd.DataFrame, slo: float = SLO_P95_SECONDS) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Average over seeds, apply the target, pick the cheapest qualifying setting per strategy."""
    per_scenario = (runs.groupby(["strategy", "setting", "params", "scenario"], sort=False)
                    [["p95_s", "avg_s", "cost", "worker_minutes"]].mean().reset_index())
    table = (per_scenario.groupby(["strategy", "setting", "params"], sort=False)
             .agg(worst_p95_s=("p95_s", "max"), mean_p95_s=("p95_s", "mean"), mean_cost=("cost", "mean"),
                  mean_worker_minutes=("worker_minutes", "mean")).reset_index())
    table["meets_slo"] = table["worst_p95_s"] <= slo
    chosen: dict[str, Any] = {}
    for strategy, g in table.groupby("strategy", sort=False):
        ok = g[g.meets_slo]
        if len(ok):
            best = ok.sort_values(["mean_cost", "mean_p95_s", "setting"]).iloc[0]
        else:
            best = g.sort_values(["worst_p95_s", "mean_cost", "setting"]).iloc[0]
        chosen[strategy] = {"params": json.loads(best["params"]), "meets_slo": bool(best["meets_slo"]),
                            "training_worst_p95_s": float(best["worst_p95_s"]),
                            "training_mean_cost": float(best["mean_cost"]),
                            "settings_tried": int(len(g)), "settings_meeting_slo": int(len(ok))}
    table["chosen"] = [chosen[s]["params"] == json.loads(p) for s, p in zip(table.strategy, table.params)]
    return table, chosen


def run(out: Path = TUNING_ROOT, progress=print) -> dict[str, Any]:
    runs = evaluate_grid(progress=progress)
    table, chosen = select(runs)
    out.mkdir(parents=True, exist_ok=True)
    table.to_csv(out / "grid.csv", index=False, float_format="%.6g")
    manifest = {
        "data_source": "SIMULATED",
        "training_seeds": list(TRAINING_SEEDS),
        "scenarios": [s.value for s in SYNTHETIC_SCENARIOS],
        "slo": f"average p95 response time (queueing model) <= {SLO_P95_SECONDS} s in every scenario",
        "objective": "lowest average estimated cost across scenarios among settings meeting the SLO",
        "capacity": {"minimum": CAPACITY.minimum, "initial": CAPACITY.initial, "maximum": CAPACITY.maximum},
        "chosen": chosen,
    }
    (out / "tuned.json").write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return manifest


def load_tuned(path: Path = TUNING_ROOT / "tuned.json") -> dict[str, dict[str, Any]]:
    """The chosen settings per strategy, as written by ``run``."""
    if not path.is_file():
        raise FileNotFoundError(f"{path} not found; run: python -m scalix.tuning run")
    return {k: v["params"] for k, v in json.loads(path.read_text())["chosen"].items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m scalix.tuning", description=__doc__.split("\n")[0])
    parser.add_argument("command", choices=["run", "show"])
    args = parser.parse_args(argv)
    if args.command == "run":
        manifest = run()
    else:
        manifest = json.loads((TUNING_ROOT / "tuned.json").read_text())
    for strategy, info in manifest["chosen"].items():
        flag = "meets target" if info["meets_slo"] else "DOES NOT meet target (closest chosen)"
        print(f"{strategy:9s} {flag}: {info['params']}  training worst p95 {info['training_worst_p95_s']:.2f} s, "
              f"mean cost {info['training_mean_cost']:.4f} ({info['settings_meeting_slo']}/{info['settings_tried']} "
              f"settings met the target)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
