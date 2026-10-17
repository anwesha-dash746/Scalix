"""Runs the Part 1 Java engine through its command-line interface.

One *batch* = one dashboard submission. Each repetition is a separate engine process with its own
seed; inside a repetition every selected policy runs on the identical request sequence. Layout::

    <results_dir>/dashboard/<batch-id>/
        batch.yaml            parameters, engine command, per-repetition status (manifest)
        rep-01.properties     exact engine configuration of repetition 1
        rep-01.log            engine stdout/stderr
        rep-01/               engine output (summary.csv, timeseries.csv, ...)

A repetition only counts as SUCCESS if the process exits with 0 *and* its output passes schema
validation *and* contains every requested workload x policy run.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Callable

import yaml

from .params import ExperimentParams, properties_text
from .results import ResultValidationError, load_experiment
from .settings import Settings

SUCCESS, FAILED, TIMEOUT, ERROR = "SUCCESS", "FAILED", "TIMEOUT", "ERROR"
MANIFEST = "batch.yaml"


@dataclass
class RepResult:
    index: int
    seed: int
    status: str
    message: str
    returncode: int | None = None
    duration_s: float = 0.0
    output_dir: str = ""
    log_file: str = ""
    config_file: str = ""

    @property
    def ok(self) -> bool:
        return self.status == SUCCESS


@dataclass
class BatchResult:
    batch_id: str
    batch_dir: Path
    params: ExperimentParams
    reps: list[RepResult] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    @property
    def status(self) -> str:
        if self.problems or not self.reps:
            return FAILED
        ok = sum(r.ok for r in self.reps)
        if ok == len(self.reps):
            return SUCCESS
        return "PARTIAL" if ok else FAILED


ProgressFn = Callable[[int, int, str], None]


class SimulationRunner:
    def __init__(self, settings: Settings):
        self.settings = settings

    # ------------------------------------------------------------------ checks

    def preflight(self) -> list[str]:
        """Problems that would prevent any run (missing Java, missing jar)."""
        problems: list[str] = []
        exe = self.settings.engine_command[0]
        if not (Path(exe).is_file() or shutil.which(exe)):
            problems.append(f"Engine executable '{exe}' was not found. Install a JDK 17+ and make sure it is on PATH.")
        jar = self.settings.engine_jar
        if jar is not None and not jar.is_file():
            problems.append(f"Engine jar not found at {jar}. Build it with: ./mvnw -B package  (Windows: mvnw.cmd -B package)")
        return problems

    # ------------------------------------------------------------------ execution

    def run_batch(self, params: ExperimentParams, batch_id: str | None = None,
                  progress: ProgressFn | None = None) -> BatchResult:
        batch_id = batch_id or new_batch_id(self.settings.dashboard_runs_dir)
        batch_dir = self.settings.dashboard_runs_dir / batch_id
        result = BatchResult(batch_id, batch_dir, params)

        errors = params.validate()
        if errors:
            result.problems = errors
            return result          # nothing written: invalid input never reaches the engine
        problems = self.preflight()
        if problems:
            result.problems = problems
            return result

        batch_dir.mkdir(parents=True, exist_ok=False)
        started = _now()
        self._write_manifest(result, started, finished=None)
        seeds = params.seeds()
        for i, seed in enumerate(seeds, start=1):
            if progress:
                progress(i - 1, len(seeds), f"Repetition {i}/{len(seeds)} (seed {seed}) running...")
            rep = self._run_rep(params, batch_dir, i, seed)
            result.reps.append(rep)
            self._write_manifest(result, started, finished=None)
            if progress:
                progress(i, len(seeds), f"Repetition {i}/{len(seeds)}: {rep.status} - {rep.message}")
        self._write_manifest(result, started, finished=_now())
        return result

    def _run_rep(self, params: ExperimentParams, batch_dir: Path, index: int, seed: int) -> RepResult:
        rep_id = f"rep-{index:02d}"
        cfg_file = batch_dir / f"{rep_id}.properties"
        log_file = batch_dir / f"{rep_id}.log"
        out_dir = batch_dir / rep_id
        rep = RepResult(index=index, seed=seed, status=ERROR, message="", output_dir=str(out_dir),
                        log_file=str(log_file), config_file=str(cfg_file))

        cfg_file.write_text(properties_text(params.to_engine_properties(seed),
                                            header=f"SCALIX dashboard batch {batch_dir.name}, {rep_id}"), encoding="utf-8")
        cmd = [*self.settings.engine_command, "--config", str(cfg_file), "--out", str(batch_dir),
               "--experiment-id", rep_id]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=self.settings.project_root, capture_output=True, text=True,
                                  timeout=self.settings.timeout_seconds, env=_engine_env())
        except FileNotFoundError as e:
            rep.status, rep.message = ERROR, f"could not start engine: {e}"
            log_file.write_text(_log_text(cmd, None, "", str(e)), encoding="utf-8")
            return rep
        except subprocess.TimeoutExpired as e:
            rep.duration_s = time.monotonic() - t0
            rep.status = TIMEOUT
            rep.message = f"engine did not finish within {self.settings.timeout_seconds:.0f} s and was stopped"
            log_file.write_text(_log_text(cmd, None, _text(e.stdout), _text(e.stderr)), encoding="utf-8")
            return rep
        except OSError as e:
            rep.status, rep.message = ERROR, f"could not start engine: {e}"
            log_file.write_text(_log_text(cmd, None, "", str(e)), encoding="utf-8")
            return rep

        rep.duration_s = time.monotonic() - t0
        rep.returncode = proc.returncode
        log_file.write_text(_log_text(cmd, proc.returncode, proc.stdout, proc.stderr), encoding="utf-8")
        if proc.returncode != 0:
            kind = "rejected the configuration" if proc.returncode == 2 else "failed"
            rep.status = FAILED
            rep.message = f"engine {kind} (exit code {proc.returncode}): {_last_line(proc.stderr) or _last_line(proc.stdout)}"
            return rep

        try:
            data = load_experiment(out_dir)
        except ResultValidationError as e:
            rep.status, rep.message = FAILED, f"engine exited 0 but its output is invalid: {e}"
            return rep
        expected = {(w, p) for w in params.workloads for p in params.policies}
        got = set(zip(data.summary["workload_type"], data.summary["policy"]))
        if got != expected:
            missing = sorted(expected - got)
            rep.status, rep.message = FAILED, f"engine output is incomplete; missing runs {missing}"
            return rep
        unfinished = int(data.summary["drain_timed_out"].sum())
        rep.status = SUCCESS
        rep.message = f"{len(data.summary)} runs completed in {rep.duration_s:.1f} s" + (
            f"; {unfinished} run(s) hit the drain limit with unfinished requests" if unfinished else "")
        return rep

    # ------------------------------------------------------------------ manifest

    def _write_manifest(self, result: BatchResult, started: str, finished: str | None) -> None:
        manifest = {
            "batch_id": result.batch_id,
            "status": result.status if finished else "RUNNING",
            "started_at": started,
            "finished_at": finished,
            "engine_command": self.settings.engine_command,
            "params": result.params.to_dict(),
            "repetitions": [
                {"rep": f"rep-{r.index:02d}", "seed": r.seed, "status": r.status, "message": r.message,
                 "returncode": r.returncode, "duration_s": round(r.duration_s, 3)}
                for r in result.reps
            ],
        }
        (result.batch_dir / MANIFEST).write_text(yaml.safe_dump(manifest, sort_keys=False), encoding="utf-8")


def new_batch_id(parent: Path) -> str:
    base = "dash-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    candidate, n = base, 2
    while (parent / candidate).exists():
        candidate, n = f"{base}-{n}", n + 1
    return candidate


def _engine_env() -> dict[str, str]:
    # The engine needs no secrets; pass a copy of the environment without cloud credentials.
    return {k: v for k, v in os.environ.items() if not k.upper().startswith("CLOUDSTACK_")}


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _text(b: bytes | str | None) -> str:
    if b is None:
        return ""
    return b.decode("utf-8", "replace") if isinstance(b, bytes) else b


def _last_line(s: str) -> str:
    lines = [ln for ln in (s or "").splitlines() if ln.strip()]
    return lines[-1].strip() if lines else ""


def _log_text(cmd: list[str], code: int | None, out: str, err: str) -> str:
    return (f"$ {' '.join(cmd)}\nexit code: {code}\n\n--- stdout ---\n{out}\n--- stderr ---\n{err}\n")
