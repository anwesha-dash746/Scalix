"""The evaluation suite and report: fixed in advance, reproducible, and every reported number traceable
to the experiment outputs (nothing typed in by hand)."""

from __future__ import annotations

import math
from dataclasses import replace
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from scalix import evaluation
from scalix.evaluation import SUITE, build_report, cell, run_suite, verdict

ROOT = Path(__file__).resolve().parent.parent


def tiny(spec):
    """Same experiment, much smaller: 2 seeds, 5-minute runs (for speed)."""
    return replace(spec, repetitions=2, workload=replace(spec.workload, duration_seconds=300))


SYNTHETIC_EXPERIMENTS = ["main", "slow-startup", "scalix-target-70", "scalix-no-additions", "queue-cap-0.99",
                         "tuned", "cpu-signal"]
NASA_EXPERIMENTS = ["nasa", "nasa-tuned", "nasa-cpu-signal", "nasa-aws"]


def test_suite_is_fixed_in_advance():
    assert list(SUITE) == SYNTHETIC_EXPERIMENTS + NASA_EXPERIMENTS
    for name in SYNTHETIC_EXPERIMENTS:
        spec = SUITE[name].spec()
        assert spec.seeds() == list(range(1, 11)), name
        assert {s.value for s in spec.scenarios} == {"NORMAL", "RISING", "SPIKE", "MIXED"}
    for name in NASA_EXPERIMENTS:
        spec = SUITE[name].spec()
        assert spec.seeds() == list(range(1, 28)), name  # every complete day of July 1995
        assert [s.value for s in spec.scenarios] == ["NASA"]
        assert spec.workload.duration_seconds == 86_400.0
        assert not SUITE[name].large_files
    for exp in SUITE.values():
        assert [p.name for p in exp.spec().policies] == ["STATIC", "THRESHOLD", "SCALIX", "TARGET_TRACKING"]
        assert exp.question


def test_test_seeds_never_overlap_training_seeds():
    from scalix.tuning import TRAINING_SEEDS

    for exp in SUITE.values():
        assert not set(exp.spec().seeds()) & set(TRAINING_SEEDS), exp.name


def test_strategy_experiments_share_identical_workloads():
    """These experiments differ only in SCALIX's settings or the model, never in the traces."""
    specs = {n: SUITE[n].spec() for n in ("main", "scalix-target-70", "scalix-no-additions", "queue-cap-0.99",
                                          "tuned", "cpu-signal")}
    assert len({repr(s.workload) for s in specs.values()}) == 1
    assert len({repr(specs[n].simulation) for n in ("main", "scalix-target-70", "scalix-no-additions", "tuned")}) == 1
    assert len({repr(specs[n].policies[:2]) for n in ("main", "scalix-target-70", "scalix-no-additions",
                                                     "queue-cap-0.99", "cpu-signal")}) == 1
    assert specs["scalix-target-70"].policies[2].target_utilization == 0.70
    assert specs["scalix-no-additions"].policies[2].spike_factor is None
    assert specs["queue-cap-0.99"].simulation.queueing_max_utilization == 0.99
    assert specs["cpu-signal"].simulation.demand_signal == "cpu"
    assert SUITE["slow-startup"].spec().simulation.startup_delay_seconds == 120.0
    aws = SUITE["nasa-aws"].spec().simulation
    assert (aws.decision_interval_seconds, aws.startup_delay_seconds, aws.demand_signal) == (300.0, 120.0, "cpu")


def test_tuned_experiments_use_the_committed_tuned_settings():
    from scalix.tuning import load_tuned

    tuned = load_tuned()
    for name in ("tuned", "nasa-tuned"):
        spec = SUITE[name].spec()
        assert not spec.same_start
        for policy in spec.policies:
            assert {k: policy.parameters()[k] for k in tuned[policy.name]} == tuned[policy.name]
        assert {p.limits.initial for p in spec.policies if p.name != "STATIC"} == {8}


@pytest.fixture(scope="module")
def small_run(tmp_path_factory):
    root = tmp_path_factory.mktemp("experiments")
    run_suite(["main", "scalix-target-70"], out_root=root, spec_override=tiny)
    docs = tmp_path_factory.mktemp("docs")
    tables, figures = build_report(root, docs / "results", docs / "figures")
    return root, tables, figures


def test_run_writes_outputs_and_report_is_generated(small_run):
    root, tables, figures = small_run
    for name in ("main", "scalix-target-70"):
        for f in ("summary.csv", "aggregate.csv", "experiment.json", "scale_down_holds.csv", "timeseries.csv"):
            assert (root / name / f).is_file()
    text = tables.read_text()
    assert "SIMULATED" in text and "## Experiment `main`" in text and "Why SCALIX keeps more workers" in text
    assert {p.name for p in figures} == {"workloads.png", "nasa_trace.png", "workers_over_time.png", "response.png",
                                         "cost.png", "overload.png", "tradeoff.png"}
    assert all(p.stat().st_size > 10_000 for p in figures)


def test_every_table_cell_is_computed_from_the_summary(small_run):
    root, tables, _ = small_run
    summary = pd.read_csv(root / "main" / "summary.csv")
    text = tables.read_text()
    for scenario in ("NORMAL", "SPIKE"):
        for metric, label, fmt, _ in evaluation.METRICS:
            row = " | ".join(cell(summary, scenario, p, metric, fmt) for p in evaluation.POLICY_ORDER)
            assert f"| {label} | {row} |" in text
    # and the cell itself matches a direct calculation
    vals = summary[(summary.scenario == "SPIKE") & (summary.policy == "SCALIX")]["worker_minutes"]
    assert cell(summary, "SPIKE", "SCALIX", "worker_minutes", ".0f").startswith(f"{vals.mean():.0f} ± ")


def test_runs_are_reproducible(tmp_path):
    a = run_suite(["main"], out_root=tmp_path / "a", spec_override=tiny)[0]
    b = run_suite(["main"], out_root=tmp_path / "b", spec_override=tiny)[0]
    pd.testing.assert_frame_equal(pd.read_csv(a / "summary.csv"), pd.read_csv(b / "summary.csv"))
    pd.testing.assert_frame_equal(pd.read_csv(a / "scale_down_holds.csv"), pd.read_csv(b / "scale_down_holds.csv"))


def _summary(values: dict[str, list[float]]) -> pd.DataFrame:
    rows = [{"scenario": "S", "policy": p, "m": v} for p, vs in values.items() for v in vs]
    return pd.DataFrame(rows)


def test_verdicts():
    clear = _summary({"STATIC": [10, 10.2, 9.8], "THRESHOLD": [5, 5.1, 4.9], "SCALIX": [1, 1.1, 0.9]})
    assert verdict(clear, "S", "m", "lower", ".2f") == "SCALIX (clear)"
    overlap = _summary({"STATIC": [10, 10, 10], "THRESHOLD": [2, 4, 6], "SCALIX": [1, 3, 5]})
    assert verdict(overlap, "S", "m", "lower", ".2f") == "SCALIX (overlapping CIs)"
    tie = _summary({"STATIC": [9, 9, 9], "THRESHOLD": [2.0001, 2.0, 2.0], "SCALIX": [2.0, 2.0, 2.0]})
    assert verdict(tie, "S", "m", "lower", ".2f") == "Threshold = SCALIX (tie)"
    assert verdict(clear, "S", "m", None) == "context"
    assert verdict(clear, "S", "m", "higher*", ".2f") == "Static* (clear)"


def test_committed_results_tables_match_committed_outputs(tmp_path):
    """The committed RESULTS_TABLES.md is exactly what the committed CSVs produce (no hand edits)."""
    committed = ROOT / "docs" / "results" / "RESULTS_TABLES.md"
    if not (ROOT / "results" / "experiments" / "main" / "summary.csv").exists() or not committed.exists():
        pytest.skip("evaluation outputs not present")
    datasets = {name: evaluation.load(ROOT / "results" / "experiments", name) for name in SUITE}
    regenerated = evaluation.write_tables(datasets, tmp_path)
    assert regenerated.read_text() == committed.read_text()


def test_committed_main_summary_has_ten_seeds_per_cell():
    path = ROOT / "results" / "experiments" / "main" / "summary.csv"
    if not path.exists():
        pytest.skip("evaluation outputs not present")
    s = pd.read_csv(path)
    counts = s.groupby(["scenario", "policy"]).size()
    assert (counts == 10).all() and len(counts) == 16
    assert (s["data_source"] == "SIMULATED").all()
    assert not s.isna().any().any() or math.isfinite(float(np.nanmax(s.select_dtypes("number").to_numpy())))


def test_paired_differences_are_variant_minus_main_by_seed():
    def summ(values):
        return pd.DataFrame([{"scenario": "SPIKE", "policy": "SCALIX", "seed": s, m: v}
                             for s, v in enumerate(values, 1) for m, _, _ in evaluation.PAIRED_METRICS])
    main = summ([10.0, 20.0, 30.0])
    variant = summ([12.0, 22.5, 33.0]).sample(frac=1, random_state=0)  # row order must not matter
    text = evaluation.paired_table({"main": {"summary": main}, "scalix-target-70": {"summary": variant}})
    row = next(line for line in text.splitlines() if line.startswith("| scalix-target-70 | main | Spike |"))
    mean, half, _ = evaluation._ci_stats(np.array([2.0, 2.5, 3.0]))
    assert f"+{mean:.1f} ± {half:.1f}" in row and "**" in row


def test_scale_down_hold_reasons_are_classified():
    events = pd.DataFrame([
        {"scenario": "S", "policy": "SCALIX", "utilization": 0.2, "applied_delta": -1, "reason": "x"},
        {"scenario": "S", "policy": "SCALIX", "utilization": 0.2, "applied_delta": 0, "reason": "scale-down suppressed by cooldown"},
        {"scenario": "S", "policy": "SCALIX", "utilization": 0.2, "applied_delta": 0, "reason": "waiting until utilization stays low"},
        {"scenario": "S", "policy": "SCALIX", "utilization": 0.30, "applied_delta": 0, "reason": "demand is RISING"},
        {"scenario": "S", "policy": "SCALIX", "utilization": 0.5, "applied_delta": 0, "reason": "in cooldown"},
        {"scenario": "S", "policy": "STATIC", "utilization": 0.1, "applied_delta": 0, "reason": "static"},
    ])
    holds = evaluation.scale_down_holds(events)
    counts = dict(zip(holds.outcome, holds.decisions))
    assert counts == {"scaled down": 1, "cooldown": 1, "not low for 2 intervals yet": 1, "demand rising": 1}


def test_committed_evaluation_doc_tables_match_committed_outputs():
    """docs/EVALUATION.md is exactly its template with tables rebuilt from the committed CSVs."""
    from scalix import evaluation_doc

    doc, template = ROOT / "docs" / "EVALUATION.md", ROOT / "docs" / "EVALUATION.template.md"
    if not (ROOT / "results" / "experiments" / "main" / "summary.csv").exists() or not template.exists():
        pytest.skip("evaluation outputs not present")
    datasets = {name: evaluation.load(ROOT / "results" / "experiments", name) for name in SUITE}
    assert doc.read_text() == evaluation_doc.render(template.read_text(), datasets)
    assert "{{" not in doc.read_text()


def test_paired_difference_is_a_minus_b_matched_by_seed():
    s = pd.DataFrame([{"scenario": "S", "policy": p, "seed": seed, "m": v}
                      for p, vals in {"SCALIX": [1.0, 2.0, 4.0], "THRESHOLD": [3.0, 3.0, 6.0]}.items()
                      for seed, v in zip((3, 1, 2), vals)])
    mean, half, n = evaluation.paired_difference(s, "S", "SCALIX", "THRESHOLD", "m")
    assert n == 3 and mean == pytest.approx(-5.0 / 3) and half > 0
