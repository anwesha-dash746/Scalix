"""Discovers experiments on disk and loads them (all valid repetitions combined).

Two kinds of experiment are recognised:

* ``dashboard`` — a batch created by the dashboard (``results/dashboard/<id>/batch.yaml``), with
  one engine output directory per repetition;
* ``cli`` — an experiment produced directly by the Java CLI (``results/<id>/summary.csv``),
  treated as a single repetition.

Only repetitions whose files pass validation are loaded; every skipped one is reported with the
reason, so the dashboard never silently mixes failed output into the results.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from .results import ResultValidationError, is_experiment_dir, load_experiment
from .runner import MANIFEST, SUCCESS


@dataclass
class CatalogEntry:
    id: str
    path: Path
    kind: str                       # "dashboard" or "cli"
    created: str
    status: str
    rep_dirs: list[Path]
    params: dict[str, Any] | None = None
    rep_seeds: dict[str, int] = field(default_factory=dict)
    rep_status: dict[str, str] = field(default_factory=dict)

    @property
    def label(self) -> str:
        return f"{self.id}  [{self.kind}, {len(self.rep_dirs)} rep, {self.status}]"


@dataclass
class LoadedExperiment:
    entry: CatalogEntry
    summary: pd.DataFrame
    timeseries: pd.DataFrame
    events: pd.DataFrame
    workloads: pd.DataFrame
    configs: dict[str, dict[str, str]]
    skipped: list[str]

    @property
    def n_reps(self) -> int:
        return int(self.summary["rep"].nunique()) if not self.summary.empty else 0


def discover(results_dir: Path) -> list[CatalogEntry]:
    """Lists experiments under ``results_dir``, newest first."""
    results_dir = Path(results_dir)
    entries: list[CatalogEntry] = []
    if not results_dir.is_dir():
        return entries

    dash_dir = results_dir / "dashboard"
    if dash_dir.is_dir():
        for batch in sorted(dash_dir.iterdir()):
            if (batch / MANIFEST).is_file():
                entry = _dashboard_entry(batch)
                if entry:
                    entries.append(entry)

    for d in sorted(results_dir.iterdir()):
        if d.is_dir() and d.name != "dashboard" and is_experiment_dir(d):
            created = _mtime(d / "summary.csv")
            entries.append(CatalogEntry(id=d.name, path=d, kind="cli", created=created, status="COMPLETED",
                                        rep_dirs=[d]))
    entries.sort(key=lambda e: e.created, reverse=True)
    return entries


def _dashboard_entry(batch: Path) -> CatalogEntry | None:
    try:
        manifest = yaml.safe_load((batch / MANIFEST).read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return CatalogEntry(id=batch.name, path=batch, kind="dashboard", created=_mtime(batch / MANIFEST),
                            status="UNREADABLE MANIFEST", rep_dirs=[])
    reps = manifest.get("repetitions") or []
    rep_status = {r.get("rep", "?"): r.get("status", "?") for r in reps}
    rep_seeds = {r.get("rep", "?"): r.get("seed") for r in reps}
    rep_dirs = [batch / r for r, s in rep_status.items() if s == SUCCESS and (batch / r).is_dir()]
    return CatalogEntry(id=batch.name, path=batch, kind="dashboard",
                        created=str(manifest.get("started_at") or _mtime(batch / MANIFEST)),
                        status=str(manifest.get("status", "?")), rep_dirs=rep_dirs,
                        params=manifest.get("params"), rep_seeds=rep_seeds, rep_status=rep_status)


def load(entry: CatalogEntry) -> LoadedExperiment:
    """Loads every valid repetition of ``entry`` into combined tables with ``rep`` and ``seed`` columns."""
    frames: dict[str, list[pd.DataFrame]] = {"summary": [], "timeseries": [], "events": [], "workloads": []}
    configs: dict[str, dict[str, str]] = {}
    skipped = [f"{rep}: status {st}" for rep, st in entry.rep_status.items() if st != SUCCESS]

    for rep_dir in entry.rep_dirs:
        rep = rep_dir.name if entry.kind == "dashboard" else "rep-01"
        try:
            data = load_experiment(rep_dir)
        except ResultValidationError as e:
            skipped.append(f"{rep}: invalid output ({e})")
            continue
        seed = int(data.summary["master_seed"].iloc[0])
        for key, df in (("summary", data.summary), ("timeseries", data.timeseries),
                        ("events", data.events), ("workloads", data.workloads)):
            df = df.copy()
            df.insert(0, "rep", rep)
            df.insert(1, "seed", seed)
            frames[key].append(df)
        configs[rep] = data.config

    def cat(key: str) -> pd.DataFrame:
        return pd.concat(frames[key], ignore_index=True) if frames[key] else pd.DataFrame()

    return LoadedExperiment(entry=entry, summary=cat("summary"), timeseries=cat("timeseries"),
                            events=cat("events"), workloads=cat("workloads"), configs=configs, skipped=skipped)


def _mtime(p: Path) -> str:
    from datetime import datetime
    try:
        return datetime.fromtimestamp(p.stat().st_mtime).isoformat(timespec="seconds")
    except OSError:
        return ""
