"""Command line: run a simulated experiment and save the results.

Examples (from the project root):

    python -m scalix.simulation
    python -m scalix.simulation --scenarios SPIKE,MIXED --repetitions 3 --seed 7
    python -m scalix.simulation --duration 3600 --step 5 --rate 8 --startup-delay 60 --out results/simulations/my-run

Nothing here contacts AWS. All outputs are simulated.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

import pandas as pd

from scalix.algorithms import CapacityLimits
from scalix.config import PROJECT_ROOT
from scalix.simulation.engine import SimulationConfig
from scalix.simulation.experiment import (
    DATA_LABEL,
    ExperimentSpec,
    default_policies,
    headline_table,
    run_experiment,
    save_experiment,
)
from scalix.simulation.workloads import Scenario, WorkloadSpec


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m scalix.simulation",
                                description="Run STATIC, THRESHOLD and SCALIX on identical simulated workloads.")
    p.add_argument("--scenarios", default=",".join(s.value for s in Scenario),
                   help="comma-separated: NORMAL,RISING,SPIKE,MIXED (default: all)")
    p.add_argument("--repetitions", type=int, default=5, help="runs per scenario, seeds base..base+n-1 (default 5)")
    p.add_argument("--seed", type=int, default=42, help="base random seed (default 42)")
    p.add_argument("--duration", type=float, default=1800.0, help="seconds per run (default 1800)")
    p.add_argument("--step", type=float, default=5.0, help="time step in seconds (default 5)")
    p.add_argument("--rate", type=float, default=5.0, help="base demand in requests/s (default 5)")
    p.add_argument("--decision-interval", type=float, default=15.0, help="seconds between decisions (default 15)")
    p.add_argument("--startup-delay", type=float, default=30.0, help="worker start-up delay in seconds (default 30)")
    p.add_argument("--capacity", type=float, default=1.0, help="requests/s one worker can serve (default 1.0)")
    p.add_argument("--min-workers", type=int, default=1)
    p.add_argument("--initial-workers", type=int, default=8)
    p.add_argument("--max-workers", type=int, default=20)
    p.add_argument("--price", type=float, default=0.10, help="illustrative price per worker-hour (default 0.10)")
    p.add_argument("--currency", default="USD", help="label for the illustrative price (default USD)")
    p.add_argument("--out", type=Path, default=None,
                   help="output folder (default results/simulations/sim-<timestamp>)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        capacity = CapacityLimits(args.min_workers, args.initial_workers, args.max_workers)
        spec = ExperimentSpec(
            scenarios=tuple(s for s in args.scenarios.split(",") if s.strip()),
            repetitions=args.repetitions,
            base_seed=args.seed,
            workload=WorkloadSpec(duration_seconds=args.duration, step_seconds=args.step, base_rate=args.rate),
            simulation=SimulationConfig(decision_interval_seconds=args.decision_interval,
                                        startup_delay_seconds=args.startup_delay,
                                        worker_capacity_rps=args.capacity,
                                        price_per_worker_hour=args.price, currency=args.currency),
            policies=default_policies(capacity),
        )
        result = run_experiment(spec)
    except ValueError as exc:
        print(f"Invalid setting: {exc}", file=sys.stderr)
        return 2

    out = args.out or PROJECT_ROOT / "results" / "simulations" / f"sim-{datetime.now():%Y%m%d-%H%M%S}"
    save_experiment(result, out)
    with pd.option_context("display.width", 200, "display.max_columns", 20, "display.precision", 3):
        print(headline_table(result))
    print(f"\n{DATA_LABEL}")
    print(f"Saved to {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
