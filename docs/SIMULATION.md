# The local workload simulator

The code is in `scalix/simulation/` (workloads, engine, experiment runner) and `scalix/metrics/` (metrics, statistics). It runs entirely on your computer, with no AWS. **Every number it produces is simulated.**

```bash
.venv/bin/python -m scalix.simulation
.venv/bin/python -m scalix.simulation --scenarios SPIKE,MIXED --repetitions 3 --seed 7
.venv/bin/python -m scalix.simulation --help
```

The first command runs all 4 scenarios × 3 strategies × 5 seeds in about 1.5 seconds. It saves to `results/simulations/sim-<timestamp>/` and prints a mean table.

## 1. Workload scenarios

The base rate (`--rate`, default 5 requests/s) is the demand intensity. Every shape below is a multiple of it.

| Scenario | Shape (default 30 min run) |
|---|---|
| `NORMAL` | Moderate fluctuation: base ± 25% as a sine wave with an 8-minute period (3.75–6.25 req/s). |
| `RISING` | Gradual straight-line rise from 0.5× to 2.5× base (2.5 → 12.5 req/s). |
| `SPIKE` | Base rate, then at minute 10 a **sudden jump** to 3× (15 req/s) in a single step. It holds for 3 minutes, declines in a straight line back to base over 7.5 minutes, then stays at base. |
| `MIXED` | All of the above in sequence. 0–25%: normal. 25–50%: rise to 1.5×. 50–55%: spike to 3×. 55–75%: decline to 0.6×. 75–100%: quiet, fluctuating around 0.6×. |

**Random arrivals.** Each step draws a Poisson-distributed number of requests with mean `rate × step`. This is the standard model for many independent users arriving at random. Use `poisson_noise=False` (in Python) for the smooth curve.

**Reproducible.** The same settings and seed always give the identical trace. Each scenario mixes its own name into the seed, so NORMAL and SPIKE with seed 42 are independent draws.

**Configurable:** duration, time step, base rate, seed, and every shape parameter (`WorkloadSpec`).

## 2. The model and its assumptions

| # | Assumption | Default |
|---|---|---|
| 1 | Time moves in fixed steps. | 5 s |
| 2 | The policy decides every *decision interval*, using averages over the interval that just ended. | 15 s (must be a whole number of steps) |
| 3 | Each serving worker processes a fixed number of requests per second. | 1.0 req/s per worker |
| 4 | A new worker is **billed immediately** but only **serves after a start-up delay**. | 30 s |
| 5 | A removed worker stops serving and billing immediately (no connection-draining phase). Workers still booting are removed first. | |
| 6 | All requests share one first-come-first-served queue. Nothing is dropped; demand that can't be served waits. | |
| 7 | Per step: `available = queue + arrivals`, `served = min(available, workers × capacity × step)`, `queue = available − served`. | |
| 8 | **Estimated utilization** = `served / (workers × capacity × step)`, between 0 and 1. Whenever a queue is left over it is exactly 1.0, because the workers were fully busy. | |
| 9 | The policy sees: utilization (interval average and history), demand in req/s (interval average and history), worker counts (serving and booting) and its own last-action times. | |
| 10 | The engine re-applies the capacity limits after every decision. Any clamp is written into the decision reason. Cooldowns are each policy's own rule. | |
| 11 | Every policy in an experiment gets the **identical** workload trace, the same starting worker count and the same model settings. | |
| 12 | Cost = billed worker-seconds × an **illustrative** price. Each worker is billed for at least 60 s, then per second. | $0.10 per worker-hour (illustrative) |

The defaults (1 req/s per worker, 30 s start-up, 15 s decisions, 1–20 workers starting at 8, $0.10/hour) match the original Java engine, so the two can be compared.

## 3. Metrics (per run)

| Metric | Meaning |
|---|---|
| `avg_utilization`, `peak_utilization` | Mean and maximum per-step **estimated** utilization of serving workers. |
| `time_above_threshold_s` | Seconds with utilization above `utilization_threshold` (default 0.70). |
| `avg_workers`, `peak_workers`, `min_workers` | Provisioned workers, serving plus booting. |
| `worker_seconds`, `worker_minutes` | Total worker lifetime, from request to removal or the end of the run. |
| `billed_worker_seconds` | The same, with the 60 s minimum per worker and rounded up to whole seconds. |
| `scale_up_events`, `scale_down_events`, `workers_added`, `workers_removed` | Counted from the decision log, applied actions only. |
| `cooldown_blocks` | Decisions where the policy wanted to act but its cooldown stopped it. |
| `engine_clamps` | Decisions the engine had to cut back to stay within limits (should be 0 for the built-in policies). |
| `time_with_queue_s`, `peak_queue_requests`, `queued_request_seconds` | Overload: how long, and how much, demand waited. |
| `unserved_at_end`, `served_fraction` | Demand still waiting when the run ended, and the share that was served. |
| `response_time_proxy_avg_s` | **Simulated proxy.** `service_time (2 s) + Σ(queue × step) / total arrivals`. The second term is the average waiting time from Little's law. |
| `response_time_proxy_p95_s` | **Simulated proxy.** The arrival-weighted 95th percentile of the per-step estimate `service_time + queue / serving capacity`, i.e. how long a request arriving then would wait for the queue ahead of it. |
| `illustrative_cost` | `billed_worker_seconds / 3600 × price`. **Not an AWS charge.** |

**Across repetitions** (`aggregate.csv`): n, mean, sample std (n − 1), min, max, and a 95% confidence interval from Student's t. With one repetition, std and the CI are shown as missing, not zero.

## 4. Output files

These are saved to `results/simulations/<id>/`:

| File | Contents |
|---|---|
| `summary.csv` | one row per run (scenario × policy × seed), every metric |
| `aggregate.csv` | statistics per scenario × policy × metric |
| `timeseries.csv` | one row per step per run: demand, serving and booting workers, queue, utilization, response proxy |
| `events.csv` | the decision log: time, action, requested vs applied change, workers before and after, utilization, demand, trend, spike flag, cooldown flag and **reason** |
| `workloads.csv` | the exact arrivals each repetition used |
| `experiment.json` | every setting, the seeds, software versions and the SIMULATED data label |

Re-run any experiment with the same settings and seeds and you get identical CSVs. The tests check this.

## 5. Reference experiment: `results/simulations/reference-seed42/`

> This is the first check run, made while building the simulator (Phase 2), with seeds 42–46. **The final evaluation uses a separate, pre-defined suite with seeds 1–10: see [EVALUATION.md](EVALUATION.md).** The two agree on every conclusion below.

Settings: all defaults, 4 scenarios × 3 strategies, 5 seeds (42–46). Mean ± 95% CI half-width. **All values are simulated.**

| Scenario | Strategy | Response proxy avg (s) | Response proxy p95 (s) | Illustrative cost ($) | Avg utilization | Worker-min |
|---|---|---|---|---|---|---|
| NORMAL | Static | 2.01 ± 0.01 | 2.0 | 0.400 | 0.63 | 240 |
| | Threshold | 2.00 | 2.0 | 0.507 ± 0.014 | 0.50 | 304 |
| | SCALIX | 2.00 | 2.0 | 0.648 ± 0.023 | 0.40 | 389 |
| RISING | Static | 39.6 ± 2.3 | 201 ± 12 | 0.400 | 0.81 | 240 |
| | Threshold | 2.00 | 2.0 | 0.605 ± 0.008 | 0.62 | 362 |
| | SCALIX | 2.00 | 2.0 | 0.732 ± 0.024 | 0.51 | 439 |
| SPIKE | Static | 145 ± 4 | 304 ± 10 | 0.400 | 0.87 | 240 |
| | Threshold | 27.7 ± 1.6 | 82 ± 5 | 0.628 ± 0.008 | 0.60 | 377 |
| | SCALIX | 3.1 ± 0.9 | 12.6 ± 7.4 | 0.801 ± 0.032 | 0.46 | 481 |
| MIXED | Static | 53.3 ± 2.2 | 176 ± 6 | 0.400 | 0.77 | 240 |
| | Threshold | 6.5 ± 1.5 | 23.7 ± 6.3 | 0.587 ± 0.009 | 0.53 | 352 |
| | SCALIX | 2.1 ± 0.2 | 3.2 ± 1.5 | 0.788 ± 0.014 | 0.40 | 473 |

**What these simulated results do and do not show:**

* **Sudden changes (SPIKE, MIXED):** SCALIX had a clearly lower response-time proxy than Threshold, with non-overlapping 95% CIs: about 3 s against 28 s on SPIKE. Scaling up early, and by more than one worker, is what helps here.
* **Calm or gradual changes (NORMAL, RISING):** Threshold and SCALIX were indistinguishable on the response proxy, both at the 2 s floor. **SCALIX cost 21–28% more for no measured benefit** here.
* **Resource efficiency:** SCALIX had the **lowest** utilization (0.40–0.51) and the highest cost of the two auto-scalers in *every* scenario. That's partly by design, because its target utilization is 0.60 while Threshold only acts at 0.70. The dashboard now lets you change SCALIX's target, and the evaluation's `scalix-target-70` experiment tests a 0.70 target: it lowers the cost, but SCALIX still costs more than Threshold ([EVALUATION.md §6.2](EVALUATION.md#62-scalix-target-70-instead-of-60)).
* **Static** was cheapest everywhere. On RISING and SPIKE it fell badly behind: about 1,800 and 500 requests were still waiting when those runs ended.
* So **no strategy wins outright.** SCALIX trades money for responsiveness to sudden demand. Whether that's worth it depends on how much waiting costs compared with servers.

## 6. Limitations

1. **The response time is a proxy.** Below saturation the queue is empty, so it shows only the 2 s service time. Real systems queue noticeably from about 70–80% utilization; the proxy ignores that, so it understates the benefit of extra headroom at moderate load.
2. **Fluid model.** Requests are treated as a continuous amount of work served in 5 s steps. There are no individual request sizes, no per-worker queues and no load-balancer behaviour.
3. **No draining or failures.** Removed workers vanish instantly, and workers never crash or boot slowly at random.
4. **Fixed start-up delay.** 30 s by default. Real EC2 instances often take 1–3 minutes to become ready, so try `--startup-delay 120`.
5. **The queue never drops requests.** A real service would time out or reject requests, which would turn the queue into errors.
6. **Runs stop at the duration.** Work still queued at the end is reported (`unserved_at_end`), but it isn't drained or billed further.
7. **The illustrative price is not an AWS price.** It's an assumed rate for comparing strategies. AWS charges depend on instance type, region, storage, data transfer and more.
8. **The defaults are untuned.** The thresholds, target utilization and cooldowns are starting values, so the ranking can change with tuning.
9. **Poisson arrivals are an assumption.** Real traffic is often burstier.
