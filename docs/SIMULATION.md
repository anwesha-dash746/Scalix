# The local workload simulator

The code is in `scalix/simulation/` (workloads, engine, experiment runner) and `scalix/metrics/` (metrics, statistics). It runs entirely on your computer, with no AWS. **Every number it produces is simulated.**

```bash
.venv/bin/python -m scalix.simulation
.venv/bin/python -m scalix.simulation --scenarios SPIKE,MIXED --repetitions 3 --seed 7
.venv/bin/python -m scalix.simulation --help
```

The first command runs all 4 scenarios × 3 strategies × 5 seeds in about 1.5 seconds. It saves to `results/simulations/sim-<timestamp>/` and prints a mean table.

## 1. Workload scenarios

The base rate (`--rate`, default 5 requests/s) is the demand intensity. Every synthetic shape below is a multiple of it. A fifth scenario, `NASA`, replays **real traffic** (section 1.1).

| Scenario | Shape (default 30 min run) |
|---|---|
| `NORMAL` | Moderate fluctuation: base ± 25% as a sine wave with an 8-minute period (3.75–6.25 req/s). |
| `RISING` | Gradual straight-line rise from 0.5× to 2.5× base (2.5 → 12.5 req/s). |
| `SPIKE` | Base rate, then at minute 10 a **sudden jump** to 3× (15 req/s) in a single step. It holds for 3 minutes, declines in a straight line back to base over 7.5 minutes, then stays at base. |
| `MIXED` | All of the above in sequence. 0–25%: normal. 25–50%: rise to 1.5×. 50–55%: spike to 3×. 55–75%: decline to 0.6×. 75–100%: quiet, fluctuating around 0.6×. |

**Random arrivals.** Each step draws a Poisson-distributed number of requests with mean `rate × step`. This is the standard model for many independent users arriving at random. Use `poisson_noise=False` (in Python) for the smooth curve.

**Reproducible.** The same settings and seed always give the identical trace. Each scenario mixes its own name into the seed, so NORMAL and SPIKE with seed 42 are independent draws.

**Configurable:** duration, time step, base rate, seed, and every shape parameter (`WorkloadSpec`).

### 1.1 Real traffic: the NASA web server log, July 1995 (Phase 7)

`NASA` replays the request log of NASA Kennedy Space Center's web server, July 1995, a classic trace in auto-scaling research. Code: `scalix/simulation/nasa.py`.

* **Source.** The Internet Traffic Archive ([NASA-HTTP](https://ita.ee.lbl.gov/html/contrib/NASA-HTTP.html)), file `NASA_access_log_Jul95.gz`. The copy used has exactly the 1,891,715 lines the archive documents (one is unparseable, so 1,891,714 requests are counted). The archive says the traces may be freely redistributed.
* **What is kept.** Only the **number of requests per 5 seconds** is committed, in `data/traces/nasa_jul95_5s.csv.gz` (260 KB). Hosts, URLs, status codes and sizes are discarded, and the raw log isn't in the repository. `python -m scalix.simulation.nasa <log>` rebuilds the file byte for byte.
* **One run = one real day,** 00:00 to 24:00 local time, replayed in real time with no time compression. The run's seed picks the day: 1 = 1 July … 27 = 27 July. 28 July is incomplete and isn't used.
* **Scaling.** The real counts are multiplied by 2.37. That makes the busiest minute of the month (6.75 req/s) about 16 req/s, i.e. 80% of the 20-worker maximum, so the synthetic scenarios' capacity limits fit. The shape stays real: daily cycles, quiet weekends, the 13 July Shuttle launch peak, and real second-to-second bursts.
* **No Poisson noise** is added; the real counts are used as they are.

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
| 12 | Cost is **estimated from AWS list prices**: one worker = one t3.micro instance in us-east-1. Billed worker-time × an hourly price, plus T3 Unlimited CPU credits (section 3.2). Each worker is billed for at least 60 s, then per second. | USD 0.016277 per worker-hour + credits |
| 13 | The **response time** comes from a queueing model (M/M/c, section 3.1) on top of the backlog the simulator queues explicitly. | 2 s mean service time, exponential |
| 14 | The policies' **demand signal** is the true arrival rate (as a load balancer would report), or, with `demand_signal="cpu"`, the rate actually served (utilization × capacity), which is all CPU monitoring can show (section 3.3). | arrival rate |

The model defaults (1 req/s per worker, 30 s start-up, 15 s decisions, 1–20 workers starting at 8) match the original Java engine. Until Phase 6 the price also matched it ($0.10 per worker-hour, illustrative); Phase 7 replaced it with AWS list prices.

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
| `response_time_model_avg_s`, `response_time_model_p95_s` | **Simulated, queueing model (headline since Phase 7).** Average and 95th percentile response time: service + waiting for a free worker (Erlang C) + waiting behind any backlog. See section 3.1. |
| `queueing_wait_avg_s` | The Erlang C part alone: average wait for a free worker below saturation. |
| `response_time_proxy_avg_s` | **Original proxy (Phase 6 metric).** `service_time (2 s) + Σ(queue × step) / total arrivals`. The second term is the average waiting time from Little's law. |
| `response_time_proxy_p95_s` | **Original proxy.** The arrival-weighted 95th percentile of the per-step estimate `service_time + queue / serving capacity`. |
| `estimated_cost` | `cost_hourly_part + cost_cpu_credits`, in USD. **An estimate from list prices, not an AWS bill.** |
| `cost_hourly_part` | `billed_worker_seconds / 3600 × price_per_worker_hour` |
| `cost_cpu_credits`, `surplus_vcpu_hours` | T3 Unlimited surplus CPU credits and their price (section 3.2) |

**Across repetitions** (`aggregate.csv`): n, mean, sample std (n − 1), min, max, and a 95% confidence interval from Student's t. With one repetition, std and the CI are shown as missing, not zero.

### 3.1 Response time: the queueing model (Phase 7)

The original proxy is exactly 2.00 s whenever no backlog forms, so it can't show the waiting real servers have at 60–90% load. Code: `scalix/metrics/queueing.py`.

* **Each worker = 2 parallel slots** (1 req/s × 2 s service time). The fleet in a 5 s step is an **M/M/c queue**: c = serving workers × 2, Poisson arrivals, exponential service times with a 2 s mean.
* **Load** = the step's estimated utilization ρ, capped at 0.95. The M/M/c formula is a long-run average and grows without bound as ρ → 1, which one 5 s step can't reach; overload beyond the cap is already in the explicit backlog. The `queue-cap-0.99` experiment checks that the cap doesn't change the conclusions.
* **A request arriving in that step** waits behind the backlog (`queue / serving capacity`, the proxy's term), then waits for a free slot with probability C (Erlang C) for an exponential time with rate c·μ·(1 − ρ), then is served.
* **Average** = 2 s + backlog wait + Erlang C wait, so the model is never below the old proxy. **p95** = the 95th percentile of the arrival-weighted mixture of these per-step distributions, solved numerically.
* **An idle system's p95 is about 6.0 s** (2 s × ln 20), because service times are exponential, not 2 s.
* **Still a model:** Poisson arrivals, exponential service, each step treated as steady state, and no network or request-size differences. It is not a measured latency.

Tests check Erlang C against textbook values and the direct formula, and the p95 against a Monte Carlo simulation.

### 3.2 Cost: AWS list prices (Phase 7)

Code: `scalix/pricing.py`. One worker = one **t3.micro** Linux instance in **us-east-1**, the smallest general-purpose size AWS Academy Learner Labs allow, with one public IPv4 address and an 8 GB gp3 boot volume. Prices were read from AWS's pages on 21 October 2026:

| Part | Price | Source |
|---|---|---|
| t3.micro on-demand, Linux, us-east-1 | USD 0.0104 per hour | [EC2 T3](https://aws.amazon.com/ec2/instance-types/t3/) |
| Public IPv4 address, in use | USD 0.005 per hour | [VPC pricing](https://aws.amazon.com/vpc/pricing/) |
| gp3 storage, 8 GB | USD 0.08 per GB-month (AWS's example rate) = USD 0.00088 per hour | [EBS pricing](https://aws.amazon.com/ebs/pricing/) |
| **Hourly price per worker** | **USD 0.016277** | sum of the above |
| T3 Unlimited surplus CPU credits, Linux | USD 0.05 per vCPU-hour | [EC2 on-demand pricing](https://aws.amazon.com/ec2/pricing/on-demand/) |

**CPU credits matter.** A t3.micro's baseline is 10% per vCPU (2 vCPUs). Above that, T3 instances run in Unlimited mode by default and get no launch credits, so a worker that is busy more than 10% of the time pays for *surplus* credits. In the model, each worker earns credits at its baseline for its whole life (booting included), spends them at its utilization × 2 vCPUs while serving, and pays `max(0, spent − earned)` × USD 0.05 when it's removed. Credits can't move between workers.

This changes the economics: total CPU work is roughly the same whichever strategy serves the demand, and every extra worker earns baseline credits worth USD 0.01 per hour. So an extra worker costs much less than its hourly price while the fleet is busy. The tables show cost with and without the credit part.

**Not included:** data transfer, load balancers, CloudWatch, taxes, free-tier allowances and AWS Academy credits. The worker's capacity of 1 req/s is a model assumption, not a t3.micro benchmark.

### 3.3 Demand signal without a load balancer (Phase 7)

SCALIX's trend and spike checks need demand in requests/s. On AWS that normally comes from a load balancer, which is billable. With `SimulationConfig(demand_signal="cpu")`, SCALIX instead sees **CPU utilization × serving capacity**: the rate the workers actually served. That's all CPU monitoring alone can show.

* Below saturation it equals demand.
* While requests queue it **under-reports** demand, because the workers can't serve more than their capacity. While the backlog drains afterwards it over-reports.

The `cpu-signal` and `nasa-cpu-signal` experiments measure what this costs SCALIX. The AWS controller uses the same idea, CPU × in-service instances (`controller.demand_signal: CPU`, see [CONTROLLER.md](CONTROLLER.md)).

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

> This is the first check run, made while building the simulator (Phase 2), with seeds 42–46. **The final evaluation uses a separate, pre-defined suite with seeds 1–10: see [EVALUATION.md](EVALUATION.md).** This table uses the Phase 2 metrics: the original response proxy and the illustrative $0.10 per worker-hour, so its costs aren't comparable with the Phase 7 estimates.

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
* **Resource efficiency:** SCALIX had the **lowest** utilization (0.40–0.51) and the highest cost of the two auto-scalers in *every* scenario. That's partly by design, because its target utilization is 0.60 while Threshold only acts at 0.70. The dashboard now lets you change SCALIX's target, and the evaluation's `scalix-target-70` experiment tests a 0.70 target: it lowers the cost, but SCALIX still costs more than Threshold ([EVALUATION.md §10](EVALUATION.md#10-sensitivity-checks)).
* **Static** was cheapest everywhere. On RISING and SPIKE it fell badly behind: about 1,800 and 500 requests were still waiting when those runs ended.
* So **no strategy wins outright.** SCALIX trades money for responsiveness to sudden demand. Whether that's worth it depends on how much waiting costs compared with servers.

## 6. Limitations

1. **The response time is a model.** Since Phase 7 an M/M/c queueing model replaces the 2 s floor of the original proxy, but it is still a formula with Poisson arrivals and exponential service, not a measured latency.
2. **Fluid model.** Requests are treated as a continuous amount of work served in 5 s steps. There are no individual request sizes, no per-worker queues and no load-balancer behaviour.
3. **No draining or failures.** Removed workers vanish instantly, and workers never crash or boot slowly at random.
4. **Fixed start-up delay.** 30 s by default. Real EC2 instances often take 1–3 minutes to become ready, so try `--startup-delay 120`.
5. **The queue never drops requests.** A real service would time out or reject requests, which would turn the queue into errors.
6. **Runs stop at the duration.** Work still queued at the end is reported (`unserved_at_end`), but it isn't drained or billed further.
7. **The cost is an estimate from list prices,** not an AWS bill. It assumes one t3.micro per worker in us-east-1 and ignores data transfer, free tier and AWS Academy credits. The CPU-credit model nets credits per worker over its life and ignores the 24-hour accrual cap.
8. **Default settings are starting values.** The `tuned` experiments compare tuned settings too (see [EVALUATION.md](EVALUATION.md)).
9. **Synthetic arrivals are Poisson.** The NASA scenario adds one real trace, but it is a 1995 web server, scaled; one trace isn't every kind of traffic.
