# The three scaling strategies, explained for the viva

The code is in `scalix/algorithms/`. To see every strategy decide on the same situations, run:

```bash
.venv/bin/python -m scalix.algorithms.examples
```

## 1. The shared idea: a policy is a pure function

Every strategy has the same shape:

```
decision = policy.decide(context)
```

* **`context` (`ScalingContext`)** is a snapshot of measurements. It holds the time, the number of workers (active, booting and total), the CPU utilization over the last interval, the recent utilization history and the recent demand history in requests per second, and when the last scale-up and scale-down happened.
* **`decision` (`ScalingDecision`)** has three parts: the action (`SCALE_UP`, `SCALE_DOWN` or `NONE`), how many workers to add or remove, and a plain-English **reason**. It also records the trend that was seen, whether a cooldown blocked an action, and whether a spike was seen.

A policy **only decides**. It has no clock, makes no network calls, never touches AWS and changes nothing. Whatever calls it applies the decision: the simulator (`scalix/simulation`) or the AWS controller (`scalix/aws/controller.py`). That gives three benefits:

1. **Testable:** the same input always gives the same output, so the tests need no timing or mocking.
2. **Fair:** all strategies receive identical contexts. Only the decision logic differs.
3. **Reusable:** the exact code tested in simulation is the code that would run against AWS.

The worker limits (`CapacityLimits`: minimum / initial / maximum) work like an EC2 Auto Scaling group's min / desired / max sizes. The minimum is always at least 1, so no strategy can remove the last worker.

## 2. Strategy A: Static allocation (the baseline)

**Rule:** keep `worker_count` workers (default 8) and never scale.

**Why it exists:** it answers the question "what if we don't auto-scale at all?" It's cheap and stable when the fixed size happens to suit the demand. It's slow when demand outgrows it, and it wastes money when demand is low. Every other strategy is compared against it.

## 3. Strategy B: Threshold-based scaling

**Rule**, once per decision interval:

| Utilization | Action |
|---|---|
| above `upper` (70%) | add 1 worker |
| below `lower` (30%) | remove 1 worker |
| otherwise | nothing |

Two guards apply on top:

* **Capacity bounds:** never above the maximum or below the minimum.
* **Cooldown (60 s, shared by both directions):** after any change, wait before the next one. A new worker takes time to boot. Without a cooldown, utilization would still read high while it boots, and the rule would keep adding workers it doesn't need. Cloud auto-scalers have cooldowns for exactly this reason.

**Boundaries:** at exactly 70% it holds, because the rule says "exceeds". At exactly 30% it also holds.

**Weaknesses, by design:** it only reacts *after* the server is already busy, it always moves one worker at a time, and it ignores where demand is heading.

## 4. Strategy C: SCALIX workload-aware scaling

SCALIX uses two inputs: **how busy the workers are** (utilization) and **where demand is heading** (the request-rate history).

### 4.1 Reading the demand signal

* **Trend (sustained change).** Fit a straight line through the last 6 demand samples (least squares) and divide its slope by the average demand. This gives the *relative* slope, the fractional change per interval. For example, demand going 4 → 5 → 6 → 7 → 8 → 9 has a relative slope of about +0.154 (≈ +15% per interval). Above +0.03 the trend is RISING, below −0.03 it's FALLING, otherwise STABLE. With fewer than 2 samples it's UNKNOWN. Using a *relative* slope means one threshold works for a 2 req/s app and a 2,000 req/s app.
* **Spike (sudden change).** If demand jumped by **≥ 1.5×** since the previous interval, that's a spike. A steady ramp (8 → 9 is 1.125×) is not a spike. Spike detection catches cases the line misses. For example, with demand 8, 7, 6, 5, 4, **7**, the fitted line still points *down*, but the last interval jumped 1.75×.

### 4.2 Scale up: earlier, and by the right amount

* SCALIX scales up when utilization reaches **70%**, the same as Threshold. It also scales up **early, at 55%,** if demand is rising or has just spiked.
* **Step size.** Treat the current load as "busy workers" = active × utilization. If demand is rising, project it 2 intervals ahead (capped at 2× growth). Then work out how many workers would run that load at **60%** (the target). Workers still booting count as already added, so SCALIX doesn't over-order. The step is capped at 3 per decision and by the maximum.
* **Worked example:** 4 workers at 85%. Busy = 3.4 workers. 3.4 / 0.60 = 5.67, rounded up to 6 workers, so add **+2**. Threshold would add +1 here.
* **Saturated-CPU rule.** When utilization is already ≥ 70%, add at least 1 worker. A CPU at 100% hides how much demand is really waiting.
* **Cooldown:** 30 s between scale-ups.

### 4.3 Scale down: cautiously

SCALIX removes **one** worker only if **all** of these are true:

1. Utilization ≤ 30% **for 2 intervals in a row**, so a single quiet moment isn't enough.
2. Demand is not rising, not unknown, and has not just spiked.
3. It's at least 90 s since the last change *in either direction*.
4. Removing a worker wouldn't push the remaining ones above the 60% target.

Rule 4 is the anti-flapping check. Without it, SCALIX could remove a worker, see the others jump above 70%, add it back, and repeat.

### 4.4 Reasons

Every decision carries a reason, including decisions to do nothing. For example:

```
utilization 0.650 >= preemptive 0.55 and demand spiked; trend FALLING (rel. slope -0.069/interval
over 6 samples); spike: demand jumped 1.75x since the previous interval
```

The decision log stores these reasons, so every action can be explained afterwards.

### 4.5 For comparison: AWS target tracking (a model of AWS's own policy)

Code: `scalix/algorithms/target_tracking.py`. This is **not** part of SCALIX. It's the yardstick that answers "why not just use AWS's built-in auto-scaling?" It is modelled from AWS's documentation, not taken from AWS's code.

* **Rule:** keep average CPU near a target (default **50%**).
* **Scale out** after **3 one-minute data points** in a row above the target. It adds enough instances to bring CPU back to the target, rounded up, and instances still starting count as already added.
* **Scale in** only after **15 one-minute data points** below 90% of the target. It never scales in while new instances warm up (300 s by default), and removes as few instances as possible.

**Worked example:** 4 workers at 90% CPU for 3 minutes. Busy capacity is 4 × 0.9 = 3.6 workers, and 3.6 / 0.5 = 7.2, rounded up to 8 workers, so it adds **+4**.

**How it differs from SCALIX:**
* It waits for 3 minutes of evidence before acting; SCALIX acts on the latest interval, plus the trend and spike checks.
* It aims lower (50% against SCALIX's 60%).
* It's even slower to scale in: it needs 15 quiet minutes.

The evaluation compares the two in [EVALUATION.md §6](EVALUATION.md#6-against-awss-own-policy-target-tracking).

## 5. The same situations, three strategies

This is the output of `python -m scalix.algorithms.examples`. These are illustrative inputs, not experiment results.

| Situation (4 workers) | Static | Threshold | SCALIX |
|---|---|---|---|
| 45% busy, steady demand | hold | hold | hold |
| 85% busy, steady demand | hold | +1 | **+2** (sized for 60%) |
| 58% busy, demand rising | hold | hold | **+2** (early) |
| 65% busy, sudden 1.75× jump after falling demand | hold | hold | **+1** (spike) |
| 85% busy, scaled up 10 s ago | hold | hold (cooldown) | hold (cooldown) |
| 15% busy for 1 interval, demand falling | hold | −1 | **hold** (wait for a 2nd low interval) |
| 15% busy for 2 intervals, demand falling | hold | −1 | −1 |
| 20% busy but demand just jumped | hold | −1 | **hold** (spike) |

**This table does not show that SCALIX is better.** Acting early and in bigger steps should cut waiting time, but it also means *more workers, so more cost*. The evaluation measured this trade-off on full workloads, including 27 days of real traffic. At default settings SCALIX responded much faster than Threshold under sudden changes and on real traffic, used much more worker-time, and cost 2–10% more at t3.micro list prices. Against AWS's own target tracking (modelled) it was slower on synthetic bursts but clearly faster on real traffic. See [EVALUATION.md](EVALUATION.md).

## 6. Design choices to be ready to defend

| Choice | Why |
|---|---|
| All thresholds and timings (70/30, 55, 60, 30 s/90 s, 1.5×, 2 intervals) are parameters | They are starting values for experiments, not proven optimums. Every one can be changed. |
| Different cooldowns for up (30 s) and down (90 s) | Adding capacity late hurts users. Removing it late only costs a little money. So SCALIX is quick to add and slow to remove. |
| Relative slope instead of raw slope | One threshold then works at any traffic level. |
| Growth projection capped at 2× | One noisy, steep reading can't order a huge fleet. |
| Booting workers count towards capacity | Avoids ordering workers again while earlier ones are still starting up. |
| Threshold baseline gets a 60 s cooldown | A baseline without a cooldown would be an unfairly weak opponent. |
| Spike = jump since the previous interval, not versus the window average | A window-average comparison wrongly flagged steady ramps as spikes. This was found while building the examples and changed. |

## 7. How we know the Python port is correct

The original Java policies are still in the repository. `tests/test_java_parity.py` builds 3,000 random and boundary situations for each of 5 policy configurations. It runs every situation through **both** the Java classes, called from the engine jar, and the Python port, then checks that the action, step size, trend, slope, cooldown flag and **reason text** match exactly. The two Python-only additions are switched off for this comparison: spike detection and the 2-interval scale-down wait.

To check that this comparison can actually fail, small bugs were deliberately planted and every one was caught: `>` changed to `>=` at the thresholds, a missing rounding guard, an off-by-one in a cooldown, a changed growth cap, and a wrong scale-down boundary. The two Python-only features have their own unit tests, which were checked the same way.

## 8. Limitations (honest list)

* **Spike detection is noise-sensitive at very low traffic.** At 1–2 requests/s, a random 1.5× jump between intervals is fairly common. The effect is limited, because a spike only matters when utilization is already ≥ 55% (scale up) or ≤ 30% (it then delays scale-down by one interval).
* **Utilization saturates at 100% during a backlog,** which hides the true demand. SCALIX's step sizing then underestimates. The "at least +1" rule only partly compensates.
* **The trend needs a demand signal** (requests/s). On AWS that normally comes from a load balancer, which is billable. Phase 7 added a free alternative: the rate actually served, CPU utilization × serving capacity (`demand_signal="cpu"` in the simulator, `controller.demand_signal: CPU` on AWS). It equals demand until the workers saturate, then under-reports it. [EVALUATION.md](EVALUATION.md) measures what that costs SCALIX.
* **The overload check on scale-down never fired in the experiments.** In the decision logs of the main experiment, the "would the remaining workers exceed 60%?" rule blocked no scale-down at all. SCALIX's cautious scale-down came entirely from the 90 s cooldown and the 2-interval rule. See [EVALUATION.md](EVALUATION.md), section 5.

## 9. Likely viva questions

1. **Why not just lower Threshold's upper limit to 55%?** It would then *always* scale up at 55%, even when demand is flat or falling, which costs more. SCALIX only scales up early when the demand signal justifies it.
2. **What stops SCALIX from adding and removing a worker in a loop?** In the experiments, two things did: the 90 s scale-down cooldown, which counts from any action, and the 2-interval low requirement. A third safeguard, the "would the rest exceed 60%?" check, exists but never triggered in the measured runs. Switching off both Python additions together (the 2-interval rule and spike detection) raised scale-downs by 2.7 ± 0.9 per run on the Normal pattern, a sign of more flapping. The experiment doesn't separate the two additions' effects.
3. **Why does a policy not apply its own decision?** Keeping it a pure function makes it testable and identical in simulation and on AWS. It also means the safety checks for AWS can live in one place, outside the algorithm.
4. **Is 70/30 optimal?** No. It's a common starting point. The project treats it as a parameter to experiment with.
