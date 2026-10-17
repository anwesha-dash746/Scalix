"""Loading and validating the CSV files written by the Java engine.

Schemas mirror the headers produced by ``com.scalix.export.ResultExporter`` (Part 1). Loading is
strict: a missing file, a missing column, or a value that cannot be parsed as its declared type
raises :class:`ResultValidationError` with every problem listed, instead of silently producing
wrong charts.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

# Column kinds: "str", "float" (empty -> NaN allowed), "int" (no empties), "bool" (true/false).
SUMMARY_SCHEMA: dict[str, str] = {
    "run_id": "str", "experiment_id": "str", "workload_type": "str", "policy": "str",
    "master_seed": "int", "workload_seed": "int", "workload_duration_s": "float", "workload_params": "str",
    "decision_interval_s": "float", "tick_s": "float", "vm_pes": "int", "vm_mips_per_pe": "float",
    "vm_boot_s": "float", "request_mean_length_mi": "float", "pricing_model": "str", "policy_params": "str",
    "requests_total": "int", "requests_completed": "int", "requests_unfinished": "int",
    "avg_response_time_s": "float", "p50_response_time_s": "float", "p95_response_time_s": "float",
    "p99_response_time_s": "float", "max_response_time_s": "float", "avg_wait_time_s": "float",
    "avg_service_time_s": "float", "cpu_utilization": "float", "mean_active_vm_utilization": "float",
    "avg_billable_vms": "float", "peak_billable_vms": "int", "min_provisioned_vms": "int",
    "vm_seconds": "float", "billed_vm_seconds": "float", "total_cost_usd": "float",
    "scale_up_actions": "int", "scale_down_actions": "int", "total_scaling_actions": "int",
    "vms_added": "int", "vms_removed": "int", "cooldown_suppressions": "int", "sim_end_time_s": "float",
    "drain_timed_out": "bool", "wall_clock_ms": "int",
}

TIMESERIES_SCHEMA: dict[str, str] = {
    "run_id": "str", "workload_type": "str", "policy": "str", "time_s": "float",
    "provisioned_vms": "int", "active_vms": "int", "booting_vms": "int", "draining_vms": "int",
    "billable_vms": "int", "active_vm_utilization": "float", "arrivals_in_tick": "int",
    "requests_in_system": "int",
}

EVENTS_SCHEMA: dict[str, str] = {
    "run_id": "str", "workload_type": "str", "policy": "str", "time_s": "float", "action": "str",
    "requested_delta": "int", "applied_delta": "int", "fleet_changed": "bool", "vms_before": "int",
    "vms_after": "int", "interval_utilization": "float", "interval_arrival_rate": "float", "trend": "str",
    "relative_slope": "float", "cooldown_blocked": "bool", "reason": "str",
}

WORKLOADS_SCHEMA: dict[str, str] = {
    "experiment_id": "str", "workload_type": "str", "workload_seed": "int", "duration_s": "float",
    "arrival_pattern": "str", "rate_params": "str", "max_rate": "float", "request_mean_length_mi": "float",
    "request_length_variation": "float", "request_pes": "int", "requests_generated": "int",
    "expected_requests": "float",
}

VMS_SCHEMA: dict[str, str] = {
    "run_id": "str", "workload_type": "str", "policy": "str", "vm_id": "int", "pes": "int",
    "request_time_s": "float", "activated_time_s": "float", "drain_start_time_s": "float",
    "termination_time_s": "float", "lifetime_s": "float", "billed_s": "float", "cost_usd": "float",
}

# Files every experiment must contain, and their schemas.
REQUIRED_FILES: dict[str, dict[str, str]] = {
    "summary.csv": SUMMARY_SCHEMA,
    "timeseries.csv": TIMESERIES_SCHEMA,
    "scaling_events.csv": EVENTS_SCHEMA,
    "workloads.csv": WORKLOADS_SCHEMA,
}
OPTIONAL_FILES: dict[str, dict[str, str]] = {"vms.csv": VMS_SCHEMA}


class ResultValidationError(ValueError):
    """A result file is missing, unreadable, or does not match its schema."""

    def __init__(self, problems: Iterable[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


@dataclass
class ExperimentData:
    """All validated tables of one experiment directory (one repetition)."""

    path: Path
    summary: pd.DataFrame
    timeseries: pd.DataFrame
    events: pd.DataFrame
    workloads: pd.DataFrame
    vms: pd.DataFrame | None = None
    config: dict[str, str] = field(default_factory=dict)


def load_csv(path: Path, schema: dict[str, str]) -> pd.DataFrame:
    """Reads ``path`` as strings, checks required columns, then converts each column to its kind."""
    path = Path(path)
    if not path.is_file():
        raise ResultValidationError([f"{path.name}: file not found ({path})"])
    try:
        raw = pd.read_csv(path, dtype=str, keep_default_na=False)
    except pd.errors.EmptyDataError:
        raise ResultValidationError([f"{path.name}: file is empty"]) from None
    except (pd.errors.ParserError, UnicodeDecodeError) as e:
        raise ResultValidationError([f"{path.name}: not a readable CSV ({e})"]) from None

    missing = [c for c in schema if c not in raw.columns]
    if missing:
        raise ResultValidationError([f"{path.name}: missing column(s) {', '.join(missing)}"])

    problems: list[str] = []
    out = pd.DataFrame(index=raw.index)
    for col in raw.columns:
        kind = schema.get(col, "str")
        values = raw[col]
        if kind == "str":
            out[col] = values.astype(str)
        elif kind == "int":
            # Parsed with Python int, not via float, so 64-bit seeds keep every digit.
            parsed = [_parse_int(s) for s in values]
            bad = [s for s, p in zip(values, parsed) if p is None]
            if bad:
                problems.append(f"{path.name}: column {col} has empty or non-integer value(s) e.g. {bad[0]!r}")
                continue
            out[col] = pd.Series(parsed, index=raw.index, dtype="int64")
        elif kind == "float":
            blank = values.str.strip() == ""
            nums = pd.to_numeric(values.where(~blank, "nan"), errors="coerce")
            bad = nums.isna() & ~blank & (values.str.strip().str.lower() != "nan")
            if bad.any():
                problems.append(f"{path.name}: column {col} has non-numeric value(s) e.g. {values[bad].iloc[0]!r}")
                continue
            out[col] = nums.astype("float64")
        elif kind == "bool":
            low = values.str.strip().str.lower()
            bad = ~low.isin(["true", "false"])
            if bad.any():
                problems.append(f"{path.name}: column {col} has non-boolean value(s) e.g. {values[bad].iloc[0]!r}")
                continue
            out[col] = low == "true"
        else:  # pragma: no cover - schema typo guard
            raise ValueError(f"Unknown column kind {kind}")
    if problems:
        raise ResultValidationError(problems)
    return out


def _parse_int(s: str) -> int | None:
    s = str(s).strip()
    try:
        return int(s)
    except ValueError:
        try:
            f = float(s)
        except ValueError:
            return None
        return int(f) if f.is_integer() else None


def load_properties(path: Path) -> dict[str, str]:
    """Reads a simple Java .properties file (as written by the engine: key=value per line)."""
    props: dict[str, str] = {}
    if not Path(path).is_file():
        return props
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith(("#", "!")) or "=" not in line:
            continue
        k, v = line.split("=", 1)
        props[k.strip()] = v.strip()
    return props


def load_experiment(path: Path | str) -> ExperimentData:
    """Loads and validates every file of one engine output directory."""
    path = Path(path)
    if not path.is_dir():
        raise ResultValidationError([f"experiment directory not found: {path}"])
    problems: list[str] = []
    tables: dict[str, pd.DataFrame] = {}
    for name, schema in REQUIRED_FILES.items():
        try:
            tables[name] = load_csv(path / name, schema)
        except ResultValidationError as e:
            problems.extend(e.problems)
    vms = None
    if (path / "vms.csv").is_file():
        try:
            vms = load_csv(path / "vms.csv", VMS_SCHEMA)
        except ResultValidationError as e:
            problems.extend(e.problems)
    if problems:
        raise ResultValidationError(problems)

    summary = tables["summary.csv"]
    if summary.empty:
        raise ResultValidationError(["summary.csv: contains no runs"])
    if summary["run_id"].duplicated().any():
        raise ResultValidationError(["summary.csv: duplicate run_id values"])
    known_runs = set(summary["run_id"])
    for name in ("timeseries.csv", "scaling_events.csv"):
        unknown = set(tables[name]["run_id"]) - known_runs
        if unknown:
            problems.append(f"{name}: rows reference run_id(s) absent from summary.csv, e.g. {sorted(unknown)[0]}")
    if problems:
        raise ResultValidationError(problems)

    return ExperimentData(
        path=path,
        summary=summary,
        timeseries=tables["timeseries.csv"],
        events=tables["scaling_events.csv"],
        workloads=tables["workloads.csv"],
        vms=vms,
        config=load_properties(path / "config_used.properties"),
    )


def is_experiment_dir(path: Path) -> bool:
    return (Path(path) / "summary.csv").is_file()
