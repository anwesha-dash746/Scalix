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
* When we tuned settings, we tuned on different seeds (101–110) from the ones we report (1–10 and the real NASA days).

**12. Is the response time real?**
No. It comes from a **queueing model** (M/M/c, Erlang C): each worker is treated as 2 parallel slots, requests arrive at random, and the formula gives how long a request waits for a free slot at the current load. The model adds that wait to any backlog and the 2 s service time. Unlike our first proxy, it shows that a server at 80% load makes requests wait more than one at 40%. No real application latency was measured.

**13. Is the cost real?**
It's an **estimate from AWS list prices**, not a bill. Each worker is priced as one t3.micro in us-east-1 (USD 0.0104/h), plus a public IPv4 address (USD 0.005/h) and an 8 GB disk, plus T3 CPU credits: USD 0.05 per vCPU-hour of use above the 10% baseline.

**14. Why do CPU credits matter so much?**
A t3.micro is a *burstable* instance. It earns credits worth 10% CPU every hour it runs, and pays extra when it's busier than that. The total CPU work is the same whichever strategy serves the demand, so having more workers means more baseline credits earned and a smaller credit bill. That's why SCALIX's extra workers cost only 2–4% more than Threshold in total, instead of 22–36%.

**15. What is the NASA trace?**
The real request log of NASA Kennedy Space Center's web server for July 1995, a classic dataset in auto-scaling research. We kept only the number of requests per 5 seconds, scaled it to fit our 20-worker fleet, and replayed every full day (27 days) in real time.

**16. What does "tuned on training seeds" mean?**
We searched many settings for each strategy (238 in total) on seeds 101–110. For each strategy we picked the cheapest setting whose p95 response stayed at or below 10 s in every scenario. Then we tested those settings on seeds 1–10 and on the real NASA days, which tuning had never seen. That's like a train/test split in machine learning.

## Results

**17. Did SCALIX win?**
At default settings, yes, on responsiveness, at a small extra cost:
* **Sudden spike:** p95 of 9.08 s against Threshold's 72.73 s.
* **Real NASA traffic:** p95 of 7.02 s against 27.92 s.
* **Cost:** 2–4% more than Threshold on synthetic traffic and 10% more on real traffic.

After tuning, the three were almost equal on synthetic traffic. On real traffic only SCALIX still met the 10 s target at a normal cost.

**18. Why is SCALIX faster on spikes?**
* It acts at 55% when demand jumps, instead of waiting for 70%.
* It adds several workers at once (sized for 60% utilization) instead of one.
* Its scale-up cooldown is only 30 s.

So capacity arrives minutes earlier.

**19. Why does SCALIX use more workers?**
* It scales up generously.
* It scales down slowly: its 90 s scale-down cooldown, which also counts from scale-ups, and its "wait for 2 quiet intervals" rule held most of its low-utilization decisions.

On noisy real traffic this adds up: 1.84× Threshold's worker-time.

**20. Where does SCALIX *not* outperform?**
* **Cost**, at default settings, in every scenario.
* **Calm or slowly rising demand:** its lead is only 0.02–0.07 s, which in practice is a tie.
* **Tuned, synthetic traffic:** tuned Threshold (scale up above 50%, 15 s cooldown) did as well.
* **AWS-like timing** (5-minute decisions): even SCALIX missed the 10 s target.

Static is always cheapest on synthetic traffic, but it falls badly behind when demand grows.

**21. Would a higher SCALIX target reduce its cost?**
Yes. Raising the target from 60% to 70% saved about 18–42 worker-minutes per run, but its spike p95 rose by about 7 s. It's a speed-versus-cost dial.

**22. What if servers take longer to start?**
With a 120 s start-up, both auto-scalers get worse. SCALIX's spike advantage shrinks from about 9× to about 5×, but it remains clear.

**23. What did your two additions to the original design do?**
These are spike detection and the 2-interval scale-down rule. Switching both off gave more scale-downs (more flapping) and a slightly worse spike p95, but slightly less worker-time. The effect is small. Most of SCALIX's advantage comes from the original design: early scale-up and target-sized steps.

**24. Is high utilization good?**
Not always. Static had the highest utilization (up to 87.6%) because its workers were overloaded while requests queued. Utilization has to be read together with waiting time.

## AWS and safety

**25. Did you deploy on AWS?**
No. The AWS code is implemented and tested with mocks only. It has never contacted a real AWS account, no infrastructure was created, and no scaling action was made. All results are simulated.

**26. What would the AWS part do?**
* **Read-only mode:** read the Auto Scaling group's size and its CPU from CloudWatch.
* **Live mode:** with explicit approval, change the group's desired capacity with one API call, `SetDesiredCapacity`.

**27. How do you stop it doing something dangerous?**
* SIMULATION is the default.
* A real change needs LIVE mode in a local file, `live.enabled`, an exact allowlisted group name, `--apply`, `--confirm <group>`, fresh live data and the kill switch off.
* Changes are capped at 2 workers, 1 at a time, once every 5 minutes, and are never retried.
* Every decision is logged before it's applied.
* The dashboard has no way to make a change.

**28. What happens if CloudWatch has no data?**
The controller holds. Missing, stale or malformed data is never treated as 0% CPU, because that would wrongly trigger a scale-down.

**29. Where are the AWS credentials?**
Only in your own `~/.aws/credentials`, under a named profile. They're never in the code, the config files, Git or the chat. Root credentials are never used.

**30. How does SCALIX get a demand signal on AWS without a load balancer?**
It uses **CPU utilization × the number of running instances**, i.e. how many instances' worth of work is being done. That costs nothing. It can't see demand beyond what a fully busy fleet serves, but there the 70% CPU rule scales up anyway. In simulation, SCALIX made exactly the same decisions with this signal as with the true request rate in all 40 synthetic runs.

**31. Then why does the AWS controller still default to Threshold?**
Only for the first live test, which is capped at 2 instances and changes 1 at a time. At that size both policies can make only the same few moves, and Threshold's single rule is easiest to check by hand. In the simulation with AWS-like timing, SCALIX was the better policy (p95 about 8 s lower per day, for 3% more cost), so switching to it later is one line of config.

## Engineering

**32. How do you know the code is correct?**
* There are automated tests for the policies, simulator, metrics, evaluation, AWS layer and dashboard. See [TESTING.md](TESTING.md).
* The Python policies match the original Java ones decision for decision on 15,000 test situations.
* We deliberately planted bugs to check that the tests can actually fail.

**33. Why is the policy a "pure function"?**
It only takes measurements in and returns a decision, with no clock, no network and no AWS. That makes it easy to test, and the exact same code runs in the simulator and in the AWS controller.

**34. Why Python and not the original Java?**
The rest of the repository, the dashboard and the tests were already Python, and boto3 is AWS's Python library. The Java engine is kept as a reference, and the parity tests prove the port matches it.

**35. What would you do next?**
* Run the small AWS experiment, with your approval at each step. That's also the only way to measure real latency.
* Test on a second, modern real-traffic trace.
* Make SCALIX release capacity sooner on noisy traffic.

See [LIMITATIONS.md](LIMITATIONS.md#7-future-improvements).
