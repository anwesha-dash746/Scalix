# Limitations and future improvements

This is the full list of what the project does **not** show. The topic-specific lists in [SIMULATION.md](SIMULATION.md#6-limitations), [ALGORITHMS.md](ALGORITHMS.md#8-limitations-honest-list), [AWS_SETUP.md](AWS_SETUP.md#10-limitations-and-open-points) and [CONTROLLER.md](CONTROLLER.md#8-limitations) give more detail.

## 1. What has and hasn't been done

| Claim | Status |
|---|---|
| The three strategies are implemented and make the decisions described | **Shown.** Unit tests, plus decision-for-decision parity with the original Java policies |
| SCALIX responds faster than Threshold to sudden demand and on real traffic **in the simulator** | **Shown, in simulation only.** 10 seeds and 27 real days, with paired differences that exclude 0 |
| SCALIX costs a little more than Threshold **in the simulator** | **Shown, in simulation only.** +2–4% synthetic and +10% real traffic at t3.micro list prices with CPU credits |
| SCALIX works without a load balancer | **Shown, in simulation only.** The CPU-only signal changed no synthetic decision. The controller supports it, tested with mocks |
| SCALIX improves real response times on AWS | **Not shown.** No real application latency was measured |
| The cost estimates match a real AWS bill | **Not shown.** They're list-price estimates |
| SCALIX can connect to a real AWS account | **Shown, read-only.** The access check succeeded on an AWS Academy account on 21 October 2026 |
| The AWS monitor and controller work on a real group | **Not shown.** No group exists yet; tested with mocks and Boto3's Stubber only, and no real scaling action was ever made |
| A deployable AWS environment exists | **No.** The CloudFormation template is the next phase |

## 2. What Phase 7 fixed, and what's left of each limitation

| Phase 6 limitation | Phase 7 fix | What's still a limitation |
|---|---|---|
| Response proxy stuck at 2.00 s | M/M/c queueing model | It's still a model: Poisson arrivals, exponential service, each 5 s step treated as steady state, and a utilization cap of 0.95 (0.99 tested). No measured latency. |
| Illustrative price | AWS list prices for a t3.micro, IPv4 and storage, plus T3 CPU credits | Only one instance type and region. Credits are netted per worker over its life, and the 24-hour accrual cap is ignored. No free tier, AWS Academy credits or data transfer. Worker capacity (1 req/s) is assumed, not benchmarked. The cost conclusion depends heavily on the burstable credit model (see [EVALUATION.md §4](EVALUATION.md#4-cost-why-the-cpu-credits-change-the-picture)). |
| Untuned settings | Tuned on training seeds 101–110, tested on seeds 1–10 and the NASA days | One target (10 s p95), one objective (lowest cost) and finite grids. Threshold's grid was widened once before the final run, after the first grid had no setting meeting the target. |
| Synthetic workloads only | NASA July 1995, all 27 complete days | One real trace, from 1995, scaled ×2.37, with its outages replayed as zero demand. Modern traffic may be burstier or smoother. |
| SCALIX needed a load balancer on AWS | CPU-derived demand signal, in the simulator and the AWS controller | It can't see demand beyond what a saturated fleet serves. Tested in simulation and with mocks only. |

## 3. The simulation model

1. **Fluid model.** Requests are a continuous amount of work served in 5 s steps. There are no individual request sizes, no per-worker queues and no load balancer.
2. **Fixed worker capacity:** 1 request/s per worker, always.
3. **Fixed start-up delay.** 30 s, or 120 s in two experiments. Real start-up times vary.
4. **No draining, failures or timeouts.** Removed workers vanish instantly, no worker crashes, and requests wait forever instead of timing out.
5. **AWS-like timing is approximate.** `nasa-aws` models 5-minute decisions and 120 s start-up, but not CloudWatch's few-minute reporting delay, the controller's 5-minute cooldown or the 2-instance cap of the first live test.

## 4. The algorithms

1. **SCALIX keeps more capacity than it needs** after a scale-up. On real traffic it used 1.84× Threshold's worker-time. Its 90 s scale-down cooldown, which also counts from scale-ups, and its 2-interval rule caused most of the holds. The tuned setting (30 s cooldown) cut this.
2. **The anti-flapping overload check never fired** in the experiments. Only unit tests exercise it.
3. **Utilization saturates at 100% during a backlog,** which hides how much demand is really waiting, so SCALIX's step sizing then underestimates.
4. **Spike detection is noise-sensitive at very low traffic.**
5. **The ablation is joint.** Spike detection and the 2-interval rule were switched off together.

## 5. The AWS layer

1. **Only the read-only access check has run against a real account** (21 October 2026, AWS Academy). Reading a real group, the controller and the writer were tested with mocks, Boto3's Stubber and a network trap only.
2. **Slow feedback.** Free CloudWatch data arrives every 5 minutes, a few minutes late. In simulation, even SCALIX missed the 10 s p95 target at that pace.
3. **One action per run.** There's no unattended apply loop. This is deliberate.
4. **No connection draining** on scale-down.
5. **Write permissions are unknown.** The read-only check showed the lab allows reading groups, instances and metrics. Whether it allows creating a stack or changing a group's size only shows at the first approved step.

## 6. Process

* **The evaluation ran as defined in advance,** with one documented change: the Threshold tuning grid was widened before the final run. There has been no independent replication.
* **Whole-run results aren't cross-checked against the Java engine.** Only the policies are compared decision for decision.
* **Tests can show bugs, not their absence.** Planted-bug checks show the tests can fail, but they cover chosen mistakes only.

## 7. Future improvements

1. **The AWS phase (next).** Write the CloudFormation template (max 2 t3.micro instances, no load balancer) with a cost review. Then, each step with your approval: read-only access check, create the stack, dry runs, one live action, delete the stack. A small real deployment is also the only way to measure real latency.
2. **A second real trace,** such as modern web or serverless traffic, to see whether the NASA results generalise.
3. **Fix SCALIX's slow release on noisy traffic.** Count the scale-down cooldown only from the last *scale-down*, then measure the worker-time saved.
4. **Tune for real traffic and AWS timing too,** on separate training days, and compare with the synthetic tuning.
5. **Model CloudWatch's reporting delay** in the AWS-timing experiment.
6. **Separate ablations** for spike detection and the 2-interval rule.
7. **Connection draining** with Auto Scaling lifecycle hooks before scale-down.
