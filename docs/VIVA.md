# Viva questions and answers

Short, plain answers. The numbers come from [EVALUATION.md](EVALUATION.md) and are **all simulated**.

## The basics

**1. What is auto-scaling?**
Changing the number of servers ("workers") automatically as demand changes. With too few workers, users wait. With too many, you pay for idle machines.

**2. What does SCALIX do, in one sentence?**
It decides how many workers to run by looking at how busy they are *and* where demand is heading. It scales up early and by the right amount, and scales down cautiously.

**3. What are the three strategies?**
* **Static:** always 8 workers.
* **Threshold:** add 1 worker above 70% busy, remove 1 below 30%, with a 60 s cooldown.
* **SCALIX:** scales up at 70%, or early at 55% if demand is rising or has spiked. Each step is sized so workers end up about 60% busy (at most +3 per decision). It removes 1 worker only after 2 quiet intervals, when demand isn't rising, with a 90 s cooldown.

**4. What is utilization?**
The fraction of the workers' capacity actually used. 70% means the workers spent 70% of their time serving requests.

**5. What is a cooldown, and why is it needed?**
A waiting time after a scaling action. A new server takes time to start. Without a cooldown, the system would still see high load while that server boots and would keep adding servers it doesn't need.

**6. What is "flapping"?**
Adding and removing a worker repeatedly because utilization keeps crossing a threshold. SCALIX's 2-interval rule and 90 s scale-down cooldown reduce it.

## Method

**7. Why a simulator, and not real AWS?**
* **Cost and safety:** a simulator costs nothing and can't break anything.
* **Fairness:** every strategy sees *exactly* the same demand, which real traffic can't guarantee.
* **Repeatability:** the same seed always gives the same result.

The price is realism. See [LIMITATIONS.md](LIMITATIONS.md).

**8. How does the simulator work?**
* Time moves in 5 s steps, and requests arrive at random (Poisson) around a demand curve.
* Each worker serves 1 request per second, and a new worker serves only after 30 s of start-up.
* Requests that can't be served wait in a queue.
* Every 15 s the strategy looks at the last interval and decides.

**9. What is a seed, and why use 10?**
A seed fixes the random numbers. Ten seeds give ten different but repeatable versions of each scenario, so we can see how much results vary and compute a confidence interval.

**10. What is a 95% confidence interval?**
A range that, by the usual statistical reasoning, would contain the true average 95% of the time. If two strategies' intervals don't overlap, we call the difference **clear**.

**11. How did you keep the comparison fair?**
* All three strategies got the same demand trace for each scenario and seed, the same starting workers and the same model.
* The suite and seeds were fixed in code before running, and every run is reported.

**12. Is the response time real?**
No. It's a **proxy**: 2 s of service time plus the average waiting time, computed from the queue length with Little's law. No real application latency was measured.

**13. Is the cost real?**
No. It's billed worker-time × an assumed USD 0.10 per worker-hour. It compares strategies; it isn't an AWS bill.

## Results

**14. Did SCALIX win?**
Not overall.
* **Sudden demand (Spike):** its response proxy was 2.52 s against Threshold's 23.56 s, with a clear difference.
* **Calm or gradually rising demand:** no faster than Threshold (both 2.00 s).
* **Cost:** 22–36% more than Threshold in every scenario.

It trades money for responsiveness to sudden changes.

**15. Why is SCALIX faster on spikes?**
* It acts at 55% when demand jumps, instead of waiting for 70%.
* It adds several workers at once (sized for 60% utilization) instead of one.
* Its scale-up cooldown is only 30 s.

So capacity arrives minutes earlier.

**16. Why does SCALIX cost more?**
* It scales up generously.
* It scales down slowly. On the Normal, Spike and Mixed patterns, the decision logs show that about two-thirds to three-quarters of its decisions at low utilization were held by its 90 s cooldown or its "wait for 2 quiet intervals" rule.

**17. Where does SCALIX *not* outperform?**
* Cost and utilization in every scenario.
* Response time on Normal and Rising, where it ties.

Static is always cheapest, but it falls badly behind when demand grows: on Rising, 1,866 requests were still waiting at the end.

**18. Would a higher SCALIX target fix the cost?**
Partly. Raising the target from 60% to 70% saved about 18–42 worker-minutes per run. On spikes it was a little slower (3.48 s instead of 2.52 s), but still far faster than Threshold. It still cost 10–30% more than Threshold.

**19. What if servers take longer to start?**
With a 120 s start-up, both auto-scalers get worse. SCALIX's spike advantage shrinks from about 9× to about 5×, but it remains clear.

**20. What did your two additions to the original design do?**
These are spike detection and the 2-interval scale-down rule. Switching both off gave more scale-downs (more flapping) and a slightly worse spike p95, but slightly less worker-time. The effect is small. Most of SCALIX's advantage comes from the original design: early scale-up and target-sized steps.

**21. Is high utilization good?**
Not always. Static had the highest utilization (up to 87.6%) because its workers were overloaded while requests queued. Utilization has to be read together with waiting time.

## AWS and safety

**22. Did you deploy on AWS?**
No. The AWS code is implemented and tested with mocks only. It has never contacted a real AWS account, no infrastructure was created, and no scaling action was made. All results are simulated.

**23. What would the AWS part do?**
* **Read-only mode:** read the Auto Scaling group's size and its CPU from CloudWatch.
* **Live mode:** with explicit approval, change the group's desired capacity with one API call, `SetDesiredCapacity`.

**24. How do you stop it doing something dangerous?**
* SIMULATION is the default.
* A real change needs LIVE mode in a local file, `live.enabled`, an exact allowlisted group name, `--apply`, `--confirm <group>`, fresh live data and the kill switch off.
* Changes are capped at 2 workers, 1 at a time, once every 5 minutes, and are never retried.
* Every decision is logged before it's applied.
* The dashboard has no way to make a change.

**25. What happens if CloudWatch has no data?**
The controller holds. Missing, stale or malformed data is never treated as 0% CPU, because that would wrongly trigger a scale-down.

**26. Where are the AWS credentials?**
Only in your own `~/.aws/credentials`, under a named profile. They're never in the code, the config files, Git or the chat. Root credentials are never used.

**27. Why doesn't the AWS controller use SCALIX by default?**
SCALIX needs a request-rate signal for its trend. On AWS that normally comes from a load balancer, which costs money. With CPU only, SCALIX's trend is "unknown", so it would never scale down. Threshold works with CPU alone.

## Engineering

**28. How do you know the code is correct?**
* There are automated tests for the policies, simulator, metrics, evaluation, AWS layer and dashboard. See [TESTING.md](TESTING.md).
* The Python policies match the original Java ones decision for decision on 15,000 test situations.
* We deliberately planted bugs to check that the tests can actually fail.

**29. Why is the policy a "pure function"?**
It only takes measurements in and returns a decision, with no clock, no network and no AWS. That makes it easy to test, and the exact same code runs in the simulator and in the AWS controller.

**30. Why Python and not the original Java?**
The rest of the repository, the dashboard and the tests were already Python, and boto3 is AWS's Python library. The Java engine is kept as a reference, and the parity tests prove the port matches it.

**31. What would you do next?**
* Use a more realistic response model or measure real latency.
* Tune each strategy before comparing.
* Make SCALIX release capacity sooner.
* Test on real traffic traces.
* Only then, with approval at each step, run a small AWS experiment.

See [LIMITATIONS.md](LIMITATIONS.md#6-future-improvements).
