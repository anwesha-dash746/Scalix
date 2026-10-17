"""TEST-ONLY helpers that write small, schema-valid experiment directories.

The values written here are arbitrary fixture numbers used to exercise loading, validation and
aggregation code. They are never shown in the dashboard and are not simulation results.
"""

from __future__ import annotations

import csv
from pathlib import Path

from scalix_dash.results import EVENTS_SCHEMA, SUMMARY_SCHEMA, TIMESERIES_SCHEMA, VMS_SCHEMA, WORKLOADS_SCHEMA


def _row(schema: dict[str, str], **values) -> dict:
    defaults = {"str": "x", "float": "1.0", "int": "1", "bool": "false"}
    return {c: values.get(c, defaults[k]) for c, k in schema.items()}


def write_experiment(directory: Path, exp_id: str = "fx", workloads=("BURST",), policies=("STATIC", "THRESHOLD", "SCALIX"),
                     seed: int = 42, metric_values: dict | None = None, ticks: int = 30) -> Path:
    """Writes summary/timeseries/scaling_events/workloads/vms CSVs + config_used.properties.

    ``metric_values[(workload, policy)]`` may override summary columns for that run.
    """
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    metric_values = metric_values or {}
    summary, ts, ev, vms = [], [], [], []
    for w in workloads:
        for p in policies:
            run_id = f"{exp_id}-{w}-{p}"
            vals = {"run_id": run_id, "experiment_id": exp_id, "workload_type": w, "policy": p,
                    "master_seed": str(seed), "workload_seed": "-4767286540954276203", "workload_duration_s": "900",
                    "workload_params": "rate=2.0", "pricing_model": "price/VM-hour=0.1;granularity_s=1.0;minimum_s=60.0",
                    "policy_params": "vmCount=8", "requests_total": "100", "requests_completed": "100",
                    "requests_unfinished": "0", "drain_timed_out": "false", "decision_interval_s": "15",
                    "avg_response_time_s": "2.0", "p95_response_time_s": "3.0", "cpu_utilization": "0.5",
                    "avg_billable_vms": "8", "total_cost_usd": "0.2", "total_scaling_actions": "0",
                    "scale_up_actions": "0", "scale_down_actions": "0"}
            vals.update({k: str(v) for k, v in metric_values.get((w, p), {}).items()})
            summary.append(_row(SUMMARY_SCHEMA, **vals))
            for t in range(ticks):
                ts.append(_row(TIMESERIES_SCHEMA, run_id=run_id, workload_type=w, policy=p, time_s=str(t),
                               provisioned_vms="8", active_vms="8", booting_vms="0", draining_vms="0",
                               billable_vms="8", active_vm_utilization="" if t == 0 else "0.5",
                               arrivals_in_tick=str(2 + t % 3), requests_in_system="3"))
            for t in (15, 30):
                delta = 1 if (p != "STATIC" and t == 15) else 0
                ev.append(_row(EVENTS_SCHEMA, run_id=run_id, workload_type=w, policy=p, time_s=str(t),
                               action="SCALE_UP" if delta else "NONE", requested_delta=str(delta),
                               applied_delta=str(delta), fleet_changed="true" if delta else "false",
                               vms_before="8", vms_after=str(8 + delta), interval_utilization="0.8",
                               interval_arrival_rate="2.5", trend="RISING", relative_slope="0.1",
                               cooldown_blocked="false", reason="fixture reason, with comma"))
            vms.append(_row(VMS_SCHEMA, run_id=run_id, workload_type=w, policy=p))
    _write(directory / "summary.csv", SUMMARY_SCHEMA, summary)
    _write(directory / "timeseries.csv", TIMESERIES_SCHEMA, ts)
    _write(directory / "scaling_events.csv", EVENTS_SCHEMA, ev)
    _write(directory / "vms.csv", VMS_SCHEMA, vms)
    _write(directory / "workloads.csv", WORKLOADS_SCHEMA,
           [_row(WORKLOADS_SCHEMA, experiment_id=exp_id, workload_type=w, workload_seed="7", arrival_pattern="Poisson",
                 rate_params="rate=2.0") for w in workloads])
    (directory / "config_used.properties").write_text(
        f"# fixture\nsimulation.seed={seed}\nsimulation.durationSeconds=900\nexport.outputDir=results\n", encoding="utf-8")
    return directory


def _write(path: Path, schema: dict[str, str], rows: list[dict]) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(schema))
        w.writeheader()
        w.writerows(rows)
