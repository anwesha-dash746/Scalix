"""Final evaluation: a fixed experiment suite, run reproducibly, and a report generated from its outputs.

Run from the project root:

    python -m scalix.evaluation run       # runs every experiment in SUITE (about 10-20 s)
    python -m scalix.evaluation report    # tables + figures from the saved outputs

Rules that keep the evaluation honest:

* The suite, its settings and its seeds (1-10) are fixed here in code, before anything runs.
  Nothing is filtered or re-run to pick better-looking seeds.
* Every policy sees the identical workload trace per (scenario, seed). Experiments that share
  the workload settings also share traces, so their SCALIX variants can be compared run for run.
* The report reads only the saved CSV files and copies their numbers into tables and figures.
  Everything is SIMULATED; nothing here measures or contacts AWS.

Outputs: ``results/experiments/<name>/`` (summary.csv, aggregate.csv, scale_down_holds.csv and
experiment.json are committed; the large time series, event and workload files are regenerated
identically by ``run`` and git-ignored). Report: ``docs/results/RESULTS_TABLES.md`` and ``docs/figures/*.png``.
"""

from __future__ import annotations

import argparse
import json
import math
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from scalix.algorithms import CapacityLimits, ScalixPolicy, StaticPolicy, ThresholdPolicy
from scalix.config import PROJECT_ROOT
from scalix.simulation import ExperimentSpec, SimulationConfig, run_experiment, save_experiment
from scalix.simulation.experiment import DATA_LABEL
from scalix.simulation.workloads import Scenario, WorkloadSpec, expected_rate

RESULTS_ROOT = PROJECT_ROOT / "results" / "experiments"
DOCS_RESULTS = PROJECT_ROOT / "docs" / "results"
DOCS_FIGURES = PROJECT_ROOT / "docs" / "figures"

SEEDS = 10
BASE_SEED = 1
POLICY_ORDER = ("STATIC", "THRESHOLD", "SCALIX")
POLICY_NAMES = {"STATIC": "Static", "THRESHOLD": "Threshold", "SCALIX": "SCALIX"}
POLICY_COLORS = {"STATIC": "#2a78d6", "THRESHOLD": "#eb6834", "SCALIX": "#1baf7a"}
SCENARIO_NAMES = {"NORMAL": "Normal", "RISING": "Rising", "SPIKE": "Spike", "MIXED": "Mixed"}

#: (column, label, number format, which direction is better or None if it is context only)
METRICS = [
    ("avg_utilization", "Average utilization", ".1%", "higher*"),
    ("peak_utilization", "Peak utilization", ".0%", None),
    ("worker_minutes", "Worker-minutes", ".0f", "lower"),
    ("scale_up_events", "Scale-ups", ".1f", None),
    ("scale_down_events", "Scale-downs", ".1f", None),
    ("time_above_threshold_s", "Time above 70% (s)", ".0f", "lower"),
    ("time_with_queue_s", "Overload: time with requests waiting (s)", ".0f", "lower"),
    ("peak_queue_requests", "Peak requests waiting", ".0f", "lower"),
    ("unserved_at_end", "Requests unserved at end", ".0f", "lower"),
    ("response_time_proxy_avg_s", "Response proxy, average (s)", ".2f", "lower"),
    ("response_time_proxy_p95_s", "Response proxy, p95 (s)", ".2f", "lower"),
    ("illustrative_cost", "Illustrative cost (USD)", ".3f", "lower"),
]


def _policies(scalix: ScalixPolicy | None = None, capacity: CapacityLimits | None = None):
    capacity = capacity or CapacityLimits()
    return (StaticPolicy(worker_count=capacity.initial), ThresholdPolicy(capacity=capacity),
            scalix or ScalixPolicy(capacity=capacity))


@dataclass(frozen=True)
class Experiment:
    name: str
    question: str
    spec: Callable[[], ExperimentSpec]


SUITE: dict[str, Experiment] = {e.name: e for e in [
    Experiment("main", "How do the three strategies compare with the default settings?",
               lambda: ExperimentSpec(repetitions=SEEDS, base_seed=BASE_SEED, policies=_policies())),
    Experiment("slow-startup", "Does the comparison hold when new workers take 120 s to start (closer to real EC2)?",
               lambda: ExperimentSpec(repetitions=SEEDS, base_seed=BASE_SEED, policies=_policies(),
                                      simulation=SimulationConfig(startup_delay_seconds=120.0))),
    Experiment("scalix-target-70", "Is SCALIX's extra cost caused by its 60% target? (SCALIX target set to 70%)",
               lambda: ExperimentSpec(repetitions=SEEDS, base_seed=BASE_SEED,
                                      policies=_policies(ScalixPolicy(target_utilization=0.70)))),
    Experiment("scalix-no-additions", "What do SCALIX's two Python additions contribute? (spike detection off, "
               "scale-down after 1 low interval, i.e. the original Java behaviour)",
               lambda: ExperimentSpec(repetitions=SEEDS, base_seed=BASE_SEED,
                                      policies=_policies(ScalixPolicy(spike_factor=None, low_intervals_required=1)))),
]}

LARGE_FILES = ("timeseries.csv", "events.csv", "workloads.csv")


# --------------------------------------------------------------------------- running

def run_suite(names: list[str] | None = None, out_root: Path = RESULTS_ROOT, spec_override=None) -> list[Path]:
    """Run the named experiments (default: all) and save their outputs. Returns the folders."""
    folders = []
    for name in names or list(SUITE):
        exp = SUITE[name]
        spec = spec_override(exp.spec()) if spec_override else exp.spec()
        result = run_experiment(spec)
        folder = save_experiment(result, Path(out_root) / name, experiment_id=name)
        manifest = json.loads((folder / "experiment.json").read_text())
        manifest["question"] = exp.question
        (folder / "experiment.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        # Small derived table (committed), so the report can be regenerated without the large events.csv.
        scale_down_holds(result.events).to_csv(folder / "scale_down_holds.csv", index=False)
        folders.append(folder)
    return folders


# --------------------------------------------------------------------------- report

def load(out_root: Path, name: str) -> dict[str, pd.DataFrame]:
    folder = Path(out_root) / name
    data = {"summary": pd.read_csv(folder / "summary.csv"), "aggregate": pd.read_csv(folder / "aggregate.csv"),
            "manifest": json.loads((folder / "experiment.json").read_text())}
    if (folder / "timeseries.csv").exists():
        data["timeseries"] = pd.read_csv(folder / "timeseries.csv")
    if (folder / "scale_down_holds.csv").exists():
        data["holds"] = pd.read_csv(folder / "scale_down_holds.csv")
    return data


def _ci_stats(values: np.ndarray) -> tuple[float, float, int]:
    from scalix.metrics.stats import t_critical_95

    values = values[~np.isnan(values)]
    n = len(values)
    mean = float(values.mean()) if n else math.nan
    half = t_critical_95(n - 1) * float(values.std(ddof=1)) / math.sqrt(n) if n > 1 else math.nan
    return mean, half, n


def cell(summary: pd.DataFrame, scenario: str, policy: str, metric: str, fmt: str) -> str:
    vals = summary[(summary.scenario == scenario) & (summary.policy == policy)][metric].to_numpy(dtype=float)
    mean, half, n = _ci_stats(vals)
    if n == 0:
        return "-"
    if n < 2 or math.isnan(half):
        return format(mean, fmt)
    return f"{format(mean, fmt)} ± {format(half, fmt)}"


def verdict(summary: pd.DataFrame, scenario: str, metric: str, direction: str | None, fmt: str = ".2f") -> str:
    """Which strategy is best on one metric, and whether its 95% CI separates it from the others."""
    if direction is None:
        return "context"
    lower = direction.startswith("lower")
    stats = {}
    for p in POLICY_ORDER:
        vals = summary[(summary.scenario == scenario) & (summary.policy == p)][metric].to_numpy(dtype=float)
        stats[p] = _ci_stats(vals)
    means = {p: s[0] for p, s in stats.items()}
    best = min(means, key=means.get) if lower else max(means, key=means.get)
    shown = {p: format(m, fmt) for p, m in means.items()}
    tied = [p for p in POLICY_ORDER if shown[p] == shown[best]]
    if len(tied) > 1:  # equal at the precision shown: no winner is named
        return " = ".join(POLICY_NAMES[p] for p in tied) + " (tie)"
    b_mean, b_half, n = stats[best]
    if n < 2:
        return f"{POLICY_NAMES[best]} (1 run)"
    clear = True
    for p, (m, h, _) in stats.items():
        if p == best:
            continue
        if lower and not (b_mean + b_half < m - h):
            clear = False
        if not lower and not (b_mean - b_half > m + h):
            clear = False
    mark = "*" if direction.endswith("*") else ""
    return f"{POLICY_NAMES[best]}{mark}" + (" (clear)" if clear else " (overlapping CIs)")


HOLD_PATTERNS = [
    ("would raise utilization", "would overload the remaining workers"),
    ("waiting until utilization", "not low for 2 intervals yet"),
    ("suppressed by cooldown", "cooldown"), ("in cooldown", "cooldown"),
    ("just spiked", "demand just spiked"), ("demand is RISING", "demand rising"),
    ("demand is UNKNOWN", "demand trend unknown"), ("already at min", "already at minimum"),
]


def scale_down_holds(events: pd.DataFrame, lower: float = 0.30) -> pd.DataFrame:
    """For decisions taken at low utilization (<= lower): did the strategy scale down, and if not, why?"""
    low = events[(events.utilization <= lower) & (events.policy != "STATIC")]

    def category(row) -> str:
        if row.applied_delta < 0:
            return "scaled down"
        for pattern, label in HOLD_PATTERNS:
            if pattern in row.reason:
                return label
        return "other (e.g. exactly at the threshold)"

    return (low.assign(outcome=low.apply(category, axis=1))
            .groupby(["scenario", "policy", "outcome"]).size().rename("decisions").reset_index())


def scale_down_table(holds: pd.DataFrame) -> str:
    lines = ["## Why SCALIX keeps more workers: decisions at low utilization (experiment `main`)", "",
             "Every decision taken while utilization was at or below 30%, over all 10 seeds, counted by what "
             "happened. Counted from the decision log (`events.csv`) and saved as `scale_down_holds.csv`.", "",
             "| Scenario | Strategy | Low-utilization decisions | Scaled down | Held: reason (count) |",
             "|---|---|---|---|---|"]
    for (scenario, policy), g in holds.groupby(["scenario", "policy"], sort=False):
        counts = dict(zip(g.outcome, g.decisions))
        down = counts.pop("scaled down", 0)
        held = ", ".join(f"{k} ({v})" for k, v in sorted(counts.items(), key=lambda kv: -kv[1])) or "-"
        lines.append(f"| {SCENARIO_NAMES[scenario]} | {POLICY_NAMES[policy]} | {g.decisions.sum()} | {down} | {held} |")
    return "\n".join(lines) + "\n"


def experiment_tables(data: dict, name: str) -> str:
    s = data["summary"]
    m = data["manifest"]
    lines = [f"## Experiment `{name}`", "", f"**Question:** {m.get('question', '')}", "",
             f"Seeds {m['spec']['seeds'][0]}–{m['spec']['seeds'][-1]} ({m['spec']['repetitions']} runs per "
             f"scenario and strategy). Cells: mean ± 95% CI half-width (Student t). "
             f"'clear' = the best strategy's interval does not overlap any other; 'context' = no better direction.",
             ""]
    for scenario in m["spec"]["scenarios"]:
        lines += [f"### {SCENARIO_NAMES[scenario]} ({scenario})", "",
                  "| Metric | Static | Threshold | SCALIX | Best |", "|---|---|---|---|---|"]
        for metric, label, fmt, direction in METRICS:
            row = [cell(s, scenario, p, metric, fmt) for p in POLICY_ORDER]
            lines.append(f"| {label} | " + " | ".join(row) + f" | {verdict(s, scenario, metric, direction, fmt)} |")
        lines.append("")
        lines.append("\\* Higher utilization means less idle capacity, but near 100% it also means requests are "
                     "waiting: read it together with the overload and response rows.")
        lines.append("")
    return "\n".join(lines)


def cross_experiment_table(datasets: dict[str, dict]) -> str:
    """SCALIX and Threshold across experiments (same seeds, same traces where workload settings match)."""
    lines = ["## SCALIX variants and settings side by side", "",
             "Mean over the same 10 seeds. `main`, `scalix-target-70` and `scalix-no-additions` use identical "
             "workload traces, so differences between their SCALIX rows come from the SCALIX settings alone. "
             "`slow-startup` changes the model, not the strategy.", "",
             "| Scenario | Experiment | Strategy | Worker-min | Cost (USD) | Avg util | Overload (s) | "
             "Resp. avg (s) | Resp. p95 (s) | Ups | Downs |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for scenario in ("NORMAL", "RISING", "SPIKE", "MIXED"):
        for name, data in datasets.items():
            s = data["summary"]
            for p in ("THRESHOLD", "SCALIX"):
                g = s[(s.scenario == scenario) & (s.policy == p)]
                if g.empty:
                    continue
                mm = g.mean(numeric_only=True)
                lines.append(f"| {SCENARIO_NAMES[scenario]} | {name} | {POLICY_NAMES[p]} | {mm.worker_minutes:.0f} | "
                             f"{mm.illustrative_cost:.3f} | {mm.avg_utilization:.1%} | {mm.time_with_queue_s:.0f} | "
                             f"{mm.response_time_proxy_avg_s:.2f} | {mm.response_time_proxy_p95_s:.2f} | "
                             f"{mm.scale_up_events:.1f} | {mm.scale_down_events:.1f} |")
    return "\n".join(lines) + "\n"


PAIRED_METRICS = [("worker_minutes", "Worker-min", ".1f"), ("illustrative_cost", "Cost (USD)", ".3f"),
                  ("time_with_queue_s", "Overload (s)", ".1f"), ("response_time_proxy_p95_s", "Resp. p95 (s)", ".2f"),
                  ("scale_down_events", "Scale-downs", ".1f")]


def paired_table(datasets: dict[str, dict]) -> str:
    """SCALIX variant minus default SCALIX, paired by seed (identical traces), with a 95% CI."""
    main = datasets["main"]["summary"]
    lines = ["## Paired differences: SCALIX variant minus default SCALIX", "",
             "Same seeds and identical workload traces, so each seed gives one paired difference. Cells: mean "
             "difference ± 95% CI; **bold** = the interval excludes 0 (a real difference in this simulation).", "",
             "| Variant | Scenario | " + " | ".join(label for _, label, _ in PAIRED_METRICS) + " |",
             "|---|---|" + "---|" * len(PAIRED_METRICS)]
    for variant in ("scalix-target-70", "scalix-no-additions"):
        if variant not in datasets:
            continue
        other = datasets[variant]["summary"]
        for scenario in ("NORMAL", "RISING", "SPIKE", "MIXED"):
            a = main[(main.scenario == scenario) & (main.policy == "SCALIX")].set_index("seed")
            b = other[(other.scenario == scenario) & (other.policy == "SCALIX")].set_index("seed")
            cells = []
            for metric, _, fmt in PAIRED_METRICS:
                diff = (b[metric] - a[metric]).dropna().to_numpy(dtype=float)
                mean, half, n = _ci_stats(diff)
                text = format(mean, "+" + fmt) + ("" if math.isnan(half) else f" ± {format(half, fmt)}")
                significant = not math.isnan(half) and (mean - half > 0 or mean + half < 0)
                cells.append(f"**{text}**" if significant else text)
            lines.append(f"| {variant} | {SCENARIO_NAMES[scenario]} | " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_tables(datasets: dict[str, dict], out: Path = DOCS_RESULTS) -> Path:
    out.mkdir(parents=True, exist_ok=True)
    parts = ["# Experiment results (generated)", "",
             f"> {DATA_LABEL}", "",
             "Generated by `python -m scalix.evaluation report` from the CSV files in `results/experiments/`. "
             "Do not edit by hand; re-run the command instead.", ""]
    for name, data in datasets.items():
        parts.append(experiment_tables(data, name))
    parts.append(cross_experiment_table(datasets))
    parts.append(paired_table(datasets))
    if "holds" in datasets.get("main", {}):
        parts.append(scale_down_table(datasets["main"]["holds"]))
    path = out / "RESULTS_TABLES.md"
    path.write_text("\n".join(parts), encoding="utf-8")
    return path


# --------------------------------------------------------------------------- figures (matplotlib)

def _style(ax, title: str, ylabel: str):
    ax.set_title(title, loc="left", fontsize=11, color="#1f2328")
    ax.set_ylabel(ylabel, color="#52514e")
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("left", "bottom"):
        ax.spines[side].set_color("#d8d4ca")
    ax.tick_params(colors="#52514e", labelsize=9)
    ax.grid(axis="y", color="#e9e5dc", linewidth=0.8)
    ax.set_axisbelow(True)


def write_figures(datasets: dict[str, dict], out: Path = DOCS_FIGURES) -> list[Path]:
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.ticker import MaxNLocator

    plt.rcParams.update({"font.family": "sans-serif", "font.size": 10, "figure.facecolor": "#fbfaf7",
                         "axes.facecolor": "#fbfaf7", "savefig.facecolor": "#fbfaf7"})
    out.mkdir(parents=True, exist_ok=True)
    paths = []
    main = datasets["main"]
    scenarios = ["NORMAL", "RISING", "SPIKE", "MIXED"]

    # 1. the four workload shapes (expected demand, no noise)
    fig, axes = plt.subplots(1, 4, figsize=(13, 2.8), sharey=True)
    spec = WorkloadSpec()
    t = np.arange(spec.steps) * spec.step_seconds
    for ax, sc in zip(axes, scenarios):
        from dataclasses import replace

        rate = expected_rate(replace(spec, scenario=Scenario(sc)), t)
        ax.plot(t / 60, rate, color="#3d3c39", linewidth=2)
        _style(ax, SCENARIO_NAMES[sc], "requests/s" if sc == "NORMAL" else "")
        ax.set_xlabel("minutes", color="#52514e")
        ax.set_ylim(0, 16)
    fig.suptitle("Workload scenarios: expected demand (random arrivals are drawn around these curves)",
                 x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    paths.append(out / "workloads.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)

    # 2. workers over time, seed 1, every scenario (small multiples)
    if "timeseries" in main:
        ts = main["timeseries"]
        seed = int(ts["seed"].min())
        fig, axes = plt.subplots(2, 2, figsize=(12, 6.5), sharex=True)
        for ax, sc in zip(axes.flat, scenarios):
            d = ts[(ts.scenario == sc) & (ts.seed == seed)]
            for p in POLICY_ORDER:
                dp = d[d.policy == p]
                ax.step(dp.time_s / 60, dp.provisioned, where="post", color=POLICY_COLORS[p], linewidth=2,
                        label=POLICY_NAMES[p])
            _style(ax, f"{SCENARIO_NAMES[sc]} (seed {seed})", "workers paid for")
            ax.set_ylim(0, 21)
            ax.yaxis.set_major_locator(MaxNLocator(integer=True))
        axes[1, 0].set_xlabel("minutes", color="#52514e")
        axes[1, 1].set_xlabel("minutes", color="#52514e")
        axes[0, 1].legend(frameon=False, loc="upper left")
        fig.suptitle("Workers over time, experiment 'main' (SIMULATED)", x=0.01, ha="left", fontsize=12)
        fig.tight_layout()
        paths.append(out / "workers_over_time.png")
        fig.savefig(paths[-1], dpi=150)
        plt.close(fig)

    # 3-5. grouped bars with 95% CI per scenario
    s = main["summary"]
    bars = [("response_time_proxy_avg_s", "Response-time proxy, average (s): lower is better", "response.png"),
            ("worker_minutes", "Worker-minutes (resource-time): lower is cheaper", "worker_minutes.png"),
            ("time_with_queue_s", "Overload: seconds with requests waiting: lower is better", "overload.png")]
    for metric, title, fname in bars:
        fig, ax = plt.subplots(figsize=(10, 3.6))
        width = 0.26
        x = np.arange(len(scenarios))
        log_scale = metric == "response_time_proxy_avg_s"  # spans 2 s to ~140 s
        for i, p in enumerate(POLICY_ORDER):
            means, halves = [], []
            for sc in scenarios:
                mean, half, _ = _ci_stats(s[(s.scenario == sc) & (s.policy == p)][metric].to_numpy(dtype=float))
                means.append(mean)
                halves.append(0 if math.isnan(half) else half)
            xs = x + (i - 1) * (width + 0.02)
            if log_scale:  # bar length is meaningless on a log axis, so use dots with CI whiskers
                ax.errorbar(xs, means, yerr=halves, fmt="o", markersize=8, color=POLICY_COLORS[p],
                            ecolor="#52514e", elinewidth=1, capsize=3, label=POLICY_NAMES[p],
                            markeredgecolor="#fbfaf7", markeredgewidth=1.5, zorder=3)
            else:
                ax.bar(xs, means, width, color=POLICY_COLORS[p], label=POLICY_NAMES[p],
                       yerr=halves, error_kw=dict(ecolor="#52514e", elinewidth=1, capsize=3))
        ax.set_xticks(x, [SCENARIO_NAMES[sc] for sc in scenarios])
        _style(ax, title + " · mean ± 95% CI, 10 seeds (SIMULATED)", "")
        ax.legend(frameon=False, ncols=3, loc="upper left")
        if log_scale:
            ax.set_yscale("log")
            ax.set_ylabel("seconds (log scale)", color="#52514e")
        fig.tight_layout()
        paths.append(out / fname)
        fig.savefig(paths[-1], dpi=150)
        plt.close(fig)

    # 6. trade-off: cost vs p95 response, one panel per scenario
    fig, axes = plt.subplots(1, 4, figsize=(13, 3.4))
    for ax, sc in zip(axes, scenarios):
        for p in POLICY_ORDER:
            g = s[(s.scenario == sc) & (s.policy == p)]
            ax.scatter(g.illustrative_cost, g.response_time_proxy_p95_s, s=28, color=POLICY_COLORS[p],
                       edgecolor="#fbfaf7", linewidth=1, label=POLICY_NAMES[p], zorder=3)
        _style(ax, SCENARIO_NAMES[sc], "p95 response proxy (s, log)" if sc == "NORMAL" else "")
        ax.set_yscale("log")
        ax.set_xlabel("illustrative cost (USD)", color="#52514e")
    axes[0].legend(frameon=False, loc="upper right", fontsize=8)
    fig.suptitle("Trade-off per run: cost vs slow responses (each dot = one seed; lower-left is better; SIMULATED)",
                 x=0.01, ha="left", fontsize=11)
    fig.tight_layout()
    paths.append(out / "tradeoff.png")
    fig.savefig(paths[-1], dpi=150)
    plt.close(fig)
    return paths


def build_report(out_root: Path = RESULTS_ROOT, docs_results: Path = DOCS_RESULTS,
                 docs_figures: Path = DOCS_FIGURES) -> tuple[Path, list[Path]]:
    datasets = {name: load(out_root, name) for name in SUITE if (Path(out_root) / name / "summary.csv").exists()}
    if "main" not in datasets:
        raise FileNotFoundError(f"no 'main' experiment in {out_root}; run `python -m scalix.evaluation run` first")
    return write_tables(datasets, docs_results), write_figures(datasets, docs_figures)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scalix.evaluation", description="Run the evaluation suite / report.")
    sub = p.add_subparsers(dest="command", required=True)
    r = sub.add_parser("run", help="run the experiment suite (simulated, local)")
    r.add_argument("--only", nargs="*", choices=list(SUITE), help="run only these experiments")
    sub.add_parser("report", help="generate tables and figures from saved outputs")
    sub.add_parser("list", help="show the experiments in the suite")
    args = p.parse_args(argv)
    if args.command == "list":
        for e in SUITE.values():
            print(f"{e.name:22} {e.question}")
        return 0
    if args.command == "run":
        for folder in run_suite(args.only):
            print(f"saved {folder.relative_to(PROJECT_ROOT)}")
        print(DATA_LABEL)
        return 0
    tables, figures = build_report()
    print(f"wrote {tables.relative_to(PROJECT_ROOT)}")
    for f in figures:
        print(f"wrote {f.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
