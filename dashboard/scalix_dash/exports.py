"""Builds downloadable files from loaded (real) experiment data."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path

import pandas as pd

from .analysis import METRICS, aggregate
from .catalog import LoadedExperiment


def csv_bytes(df: pd.DataFrame) -> bytes:
    """UTF-8 CSV with '.' decimals and no index, as bytes for a download button."""
    return df.to_csv(index=False, lineterminator="\n").encode("utf-8")


def aggregated_summary(exp: LoadedExperiment) -> pd.DataFrame:
    """Per workload x policy x metric statistics, with metric metadata for interpretation."""
    agg = aggregate(exp.summary, list(METRICS))
    if agg.empty:
        return agg
    agg.insert(0, "experiment", exp.entry.id)
    agg["metric_label"] = agg["metric"].map(lambda m: METRICS[m].label)
    agg["unit"] = agg["metric"].map(lambda m: METRICS[m].unit)
    agg["better"] = agg["metric"].map(lambda m: METRICS[m].better)
    agg["kind"] = agg["metric"].map(lambda m: METRICS[m].kind)
    return agg


def export_files(exp: LoadedExperiment) -> dict[str, bytes]:
    """All downloadable CSVs for an experiment, keyed by file name."""
    files = {
        "runs_summary.csv": csv_bytes(exp.summary),
        "aggregated_summary.csv": csv_bytes(aggregated_summary(exp)),
        "timeseries.csv": csv_bytes(exp.timeseries),
        "scaling_events.csv": csv_bytes(exp.events),
        "workloads.csv": csv_bytes(exp.workloads),
    }
    return files


def zip_bundle(exp: LoadedExperiment) -> bytes:
    """A zip with the derived CSVs plus the raw engine files of every loaded repetition."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in export_files(exp).items():
            z.writestr(f"{exp.entry.id}/{name}", data)
        for rep_dir in exp.entry.rep_dirs:
            for f in sorted(Path(rep_dir).iterdir()):
                if f.is_file():
                    z.write(f, f"{exp.entry.id}/raw/{rep_dir.name}/{f.name}")
        manifest = exp.entry.path / "batch.yaml"
        if manifest.is_file():
            z.write(manifest, f"{exp.entry.id}/raw/batch.yaml")
    return buf.getvalue()
