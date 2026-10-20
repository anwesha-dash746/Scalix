# Limitations and future improvements

This is the full list of what the project does **not** show. The topic-specific lists in [SIMULATION.md](SIMULATION.md#6-limitations), [ALGORITHMS.md](ALGORITHMS.md#8-limitations-honest-list), [AWS_SETUP.md](AWS_SETUP.md#10-limitations-and-open-points) and [CONTROLLER.md](CONTROLLER.md#8-limitations) give more detail.

## 1. What has and hasn't been done

| Claim | Status |
|---|---|
| The three strategies are implemented and make the decisions described | **Shown.** Unit tests, plus decision-for-decision parity with the original Java policies |
| SCALIX responds faster than Threshold to sudden demand **in the simulator** | **Shown, in simulation only.** 10 seeds, non-overlapping 95% CIs |
| SCALIX costs more than Threshold **in the simulator** | **Shown, in simulation only.** Every scenario |
| SCALIX improves real response times on AWS | **Not shown.** No real application latency was measured |
| SCALIX saves real money on AWS | **Not shown.** In simulation it cost *more*. Costs are illustrative |
| The AWS monitor works on a real account | **Not shown.** Tested with mocks and Boto3's Stubber only |
| The AWS controller can scale a real group safely | **Not shown.** Tested offline only; no real scaling action was ever made |
| A deployable AWS environment exists | **No.** The CloudFormation template has not been written |

## 2. The simulation model

1. **The response time is a proxy, not a measurement.** It's 2 s plus the queueing delay from Little's law. When no queue forms, it sits at exactly 2.00 s, so it can't show any slowdown at 60–80% load. A real server does slow down there. Differences under light load are therefore invisible: on Normal and Rising the proxy may understate any benefit of SCALIX's extra headroom, and nothing here shows whether it does.
2. **Fluid model.** Requests are a continuous amount of work served in 5 s steps. There are no individual request sizes, no per-worker queues and no load balancer.
3. **Fixed worker capacity:** 1 request/s per worker, always.
4. **Fixed start-up delay.** 30 s, or 120 s in the sensitivity experiment. Real start-up times vary.
5. **No draining, failures or timeouts.** Removed workers vanish instantly, no worker crashes, and requests wait forever instead of timing out.
6. **Synthetic workloads.** Four hand-designed shapes with Poisson arrivals. Real traffic is often burstier and has daily cycles.
7. **Illustrative cost.** USD 0.10 per worker-hour, per-second billing with a 60 s minimum. It's not an AWS price, and it ignores storage, data transfer and load balancers.

## 3. The algorithms

1. **Parameters are not tuned.** 70/30, 55%, 60%, the 30 s / 90 s cooldowns, 1.5× and 2 intervals are starting values. Raising SCALIX's target from 60% to 70% alone cut its worker-time by about 18–42 worker-minutes per run ([EVALUATION.md §6.2](EVALUATION.md#62-scalix-target-70-instead-of-60)), so the ranking could change with tuning.
2. **SCALIX keeps more capacity than it needs** after a scale-up. On the Normal, Spike and Mixed patterns, most held scale-downs in the decision logs were held by its 90 s cooldown, which also counts from scale-ups, or by its 2-interval rule ([EVALUATION.md §5](EVALUATION.md#5-why-scalix-keeps-more-workers-evidence-from-the-decision-logs)).
3. **The anti-flapping overload check never fired** in the experiments. It is untested by realistic workloads; only unit tests exercise it.
4. **Utilization saturates at 100% during a backlog,** which hides how much demand is really waiting, so SCALIX's step sizing then underestimates.
5. **Spike detection is noise-sensitive at very low traffic.**
6. **The ablation is joint.** Spike detection and the 2-interval rule were switched off together, so their separate effects are unknown.

## 4. The AWS layer

1. **Never run against a real account.** Everything AWS-related was tested with mocks, Boto3's Stubber and a network trap.
2. **No request-rate signal on AWS** without a load balancer, which is billable. Without it, SCALIX's trend is always UNKNOWN, so it never scales down. The controller therefore defaults to Threshold.
3. **Slow feedback.** Free CloudWatch data arrives every 5 minutes, a few minutes late, and the controller cooldown is 5 minutes. A real controller reacts in minutes, not the simulator's 15 seconds. The simulated results don't carry over to that timescale.
4. **One action per run.** There's no unattended apply loop. This is deliberately conservative.
5. **No connection draining** on scale-down.
6. **AWS Academy restrictions are unknown** until the access check is run on the real account.

## 5. Process

* **The evaluation ran once, as defined in advance.** That is honest, but it means no independent replication.
* **Whole-run results aren't cross-checked against the Java engine.** Only the policies are compared decision for decision; the two simulators differ.
* **Tests can show bugs, not their absence.** Planted-bug checks show the tests can fail, but they cover chosen mistakes only.

## 6. Future improvements

In rough order of value:

1. **A better response model.** Replace the proxy with a queueing model that slows down below saturation, for example M/M/c, or measure real latency on a small deployment.
2. **Tune before comparing.** Search each strategy's parameters on a training set of traces, then compare on held-out traces. This makes the comparison fairer.
3. **Fix SCALIX's slow release.** Count the scale-down cooldown only from the last *scale-down*, or shorten it when utilization stays very low, then measure the cost change.
4. **Realistic traces,** such as public web-server logs with daily cycles, instead of synthetic shapes.
5. **Separate ablations** for spike detection and the 2-interval rule.
6. **A CPU-only SCALIX for AWS.** Use the CPU trend as the demand signal when no load balancer exists, and test it in simulation first.
7. **The optional AWS step.** Write the CloudFormation template (max 2 small instances), do a billable-resource and cost review, run the read-only access check, then hold one approved live session. Each step needs its own approval, and none has been started.
8. **Connection draining** using Auto Scaling lifecycle hooks before scale-down.
