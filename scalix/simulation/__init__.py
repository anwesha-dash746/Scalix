"""Workload generator and discrete-time simulation engine.

Runs entirely on the local machine with NumPy and pandas and never touches AWS.

    >>> from scalix.simulation import ExperimentSpec, run_experiment
    >>> result = run_experiment(ExperimentSpec(repetitions=1))   # all scenarios x all policies

Command line: ``python -m scalix.simulation --help``.
"""

from scalix.simulation.engine import RunResult, SimulationConfig, simulate
from scalix.simulation.experiment import (
    DATA_LABEL,
    ExperimentResult,
    ExperimentSpec,
    default_policies,
    run_experiment,
    save_experiment,
)
from scalix.simulation.workloads import Scenario, WorkloadSpec, WorkloadTrace, expected_rate, generate

__all__ = [
    "DATA_LABEL",
    "ExperimentResult",
    "ExperimentSpec",
    "RunResult",
    "Scenario",
    "SimulationConfig",
    "WorkloadSpec",
    "WorkloadTrace",
    "default_policies",
    "expected_rate",
    "generate",
    "run_experiment",
    "save_experiment",
    "simulate",
]
