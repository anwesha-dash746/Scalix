# SCALIX architecture

## Components

```
┌────────────────────────── Python (dashboard/) ──────────────────────────┐
│ app.py + views/        Streamlit UI (6 pages, plain-language)           │
│ scalix_dash/params     validate form input → Java .properties            │
│ scalix_dash/runner     run engine process, capture status, write manifest│
│ scalix_dash/results    load + schema-validate engine CSVs                │
│ scalix_dash/catalog    discover experiments, combine valid repetitions   │
│ scalix_dash/analysis   aggregates, CIs, comparisons, fairness checks     │
│ scalix_dash/plots      matplotlib figures   scalix_dash/exports  CSV/zip │
│ scalix_dash/story      plain-language names, verdicts, trade-off text    │
│ scalix_dash/cloud      provider interface, mock + CloudStack (optional)  │
└───────────────┬───────────────────────────────────────▲─────────────────┘
                │ java -jar scalix-…-all.jar            │ CSV files
                │   --config rep-NN.properties          │ (summary, timeseries,
                │   --out <batch dir> --experiment-id   │  scaling_events, vms,
                ▼   rep-NN                              │  workloads, config_used)
┌────────────────────────── Java (src/main/java) ─────┴───────────────────┐
│ SimulationMain → ExperimentRunner → ScalixSimulation (CloudSim Plus 8.5.7)│
│ workload/  scaling/  allocation/  metrics/  export/                      │
└──────────────────────────────────────────────────────────────────────────┘
```

The dashboard and the engine share **only** the command line and the CSV files. No Python code imports or reimplements simulation logic, and no Java code knows the dashboard exists.

## Dashboard ↔ engine contract

| Direction | Mechanism | Details |
|-----------|-----------|---------|
| Python → Java | `.properties` file | Keys are exactly those in `src/main/resources/scalix-default.properties`. The engine rejects unknown keys and invalid values with exit code 2. |
| Python → Java | CLI arguments | `--config FILE --out DIR --experiment-id ID`. The command prefix comes from `config/dashboard.yaml` (`engine.command`). |
| Java → Python | exit code | `0` ok, `2` configuration rejected, `1` runtime failure. Any other code, a timeout, or a failure to start is also reported. |
| Java → Python | CSV files in `DIR/ID/` | Validated against the schemas in `scalix_dash/results.py` (required columns, types, 64-bit integers kept exact, booleans, no orphan rows). |

A repetition counts as **SUCCESS** only if all three conditions hold:
1. The process exited with 0.
2. Every required file passes validation.
3. The summary contains exactly the requested workload × policy runs.

Anything else is recorded as FAILED, TIMEOUT or ERROR in `batch.yaml`, together with the engine log, and is excluded from all charts and statistics.

## Data flow of one dashboard experiment

1. **Validate.** `ExperimentParams.validate()` checks the form values: ranges, `lower < upper`, `min ≤ initial ≤ max ≤ 40`, the SCALIX thresholds lying inside the band, and so on. If anything is invalid, nothing is written and no process starts.
2. **Configure.** `to_engine_properties(seed)` produces one config per repetition. Its seeds are `base, base+1, …`. Burst and fluctuation timing scale with the duration, matching the Part 1 defaults at 900 s.
3. **Execute.** `SimulationRunner.run_batch()` writes `results/dashboard/<batch>/rep-NN.properties`, runs the engine with a timeout, and saves stdout and stderr to `rep-NN.log`. Cloud credentials are removed from the engine's environment. `batch.yaml` is updated after every repetition.
4. **Load.** `catalog.load()` reads every SUCCESS repetition, validates it, and adds `rep` and `seed` columns. Skipped repetitions are listed with their reasons.
5. **Analyse.** `analysis.py`:
   * excludes runs that hit the drain limit;
   * computes n, mean, sample std, min, max and the 95% t-interval for each workload × policy × metric;
   * finds the best policy for each metric and calls the lead "clear" only when the confidence intervals don't overlap;
   * computes percentage change versus STATIC;
   * checks configuration consistency across repetitions.
6. **Show and export.** Charts and tables read only these frames. Downloads are generated from the same frames, plus a zip of the raw engine files.

## Fairness controls

* **Same requests.** Inside a repetition, the engine generates each workload's request list once and gives the identical list to every policy (Part 1 `ExperimentRunner`).
* **Same conditions.** VM type, hosts, tick, decision interval, boot delay, drain limit and pricing come from one configuration file per repetition.
* **Same starting fleet.** The dashboard sets `policy.static.vmCount` and `policy.scaling.initialVms` to the same initial VM count.
* **Shared thresholds.** `upper` and `lower` are shared by THRESHOLD and SCALIX. SCALIX's trend window and cooldowns are its only extra inputs.
* **Pooling check.** Repetitions differ only in their seed, and `config_consistency()` verifies this from each repetition's `config_used.properties`. The *Full statistics* section of the Results page (and *Under the hood*) shows the result.
* **No assumed winner.** Comparisons are per metric. A lead is labelled "single run", "not clear (95% CIs overlap)" or "clear". STATIC and THRESHOLD win wherever the data says they do.

## Scaling algorithms (implemented in Java, Part 1)

Every decision interval (15 s by default), the engine works through these steps:
1. It measures the mean CPU utilization of ACTIVE VMs over the interval, from per-second `Vm.getCpuPercentUtilization()` samples.
2. It measures the arrival rate over the interval.
3. It builds a `ScalingContext` and calls `policy.decide()`.
4. It clamps the decision to the min/max limits.
5. It applies the decision. A scale-up adds BOOTING VMs, which take traffic after the boot delay. A scale-down drains VMs before destroying them.
6. It records the decision with its reason.

**STATIC** never acts.

**THRESHOLD**:
* if utilization is above `upper`, add 1 VM;
* if utilization is below `lower`, remove 1 VM;
* both within `[min, max]`, with an optional shared cooldown (0 s by default).

**SCALIX**:
1. **Trend.** Fit a least-squares slope to the arrival rate over the last `observationWindow` intervals and divide it by the mean, giving a relative slope. Above `+trendThreshold` the trend is RISING, below `−trendThreshold` it is FALLING, otherwise STABLE. With fewer than 2 samples it is UNKNOWN.
2. **Scale up** when utilization ≥ `upper`, or when utilization ≥ `preemptiveUpper` and the trend is RISING.
   * Busy capacity = `active × utilization`. If demand is rising, multiply it by `1 + slope × projectionIntervals`, capped at ×2.
   * Target fleet = `ceil(busy / targetUtilization)`. VMs that are still booting count toward it.
   * Step = target − current, at least 1 when utilization ≥ `upper`, and at most `maxScaleUpStep` and the remaining headroom to `max`.
   * Blocked during `scaleUpCooldownSeconds` after the last scale-up.
3. **Scale down 1 VM** when all of these hold:
   * utilization ≤ `lower`;
   * the trend is FALLING or STABLE (not RISING or UNKNOWN);
   * removing a serving VM wouldn't raise utilization above `targetUtilization`;
   * at least `scaleDownCooldownSeconds` have passed since the last action in either direction;
   * the fleet is above `min`, which is always ≥ 1, so the last VM is never removed.

All thresholds are configurable starting values, not tuned optima.
