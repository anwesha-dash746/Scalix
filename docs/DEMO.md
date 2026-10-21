# Local demo script (about 10 minutes, no AWS, no internet needed)

Everything in this demo runs on your laptop and costs nothing. **Say at the start: "Every number you'll see is simulated or mocked. Nothing was deployed on AWS."**

## Before the viva (once)

```bash
cd ~/Scalix
git checkout aws-edition
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q
```

The test run takes about a minute and should end with all tests passed. If it does, open two terminal windows in `~/Scalix`.

## 1. Start the dashboard (1 min)

```bash
.venv/bin/streamlit run app.py
```

Open **http://localhost:8501**.

**Point out:**
* The sidebar says **SIMULATION**, which is the default.
* Every page carries a data label (SIMULATED / MOCKED / LIVE_AWS).

## 1b. The real-traffic demo (2 min), the strongest opener

Open **Demo: real traffic** (13 July 1995 is selected, the Space Shuttle launch day). The first time, it simulates the day in about 10 seconds.

**Say:** "These are real requests from NASA's web server in 1995, replayed for all four strategies on the same clock. Twenty-four hours play in 20 seconds." Then scroll slowly:

* **Workers chart:** SCALIX (green) and AWS target tracking (yellow) rise before the morning burst; Threshold (orange) follows late.
* **Requests waiting:** Static's queue explodes at the launch peak. In the zoomed chart, SCALIX stays near zero.
* **Scorecard:** on that day SCALIX had the lowest p95 response.

Each chart starts only when it's on screen, so scroll at your own pace.

## 1c. The evaluation in the app (1 min)

Open **Evaluation results** and pick **Default settings, real NASA traffic**.

**Say:** "Across all 27 real days, SCALIX's p95 was 7 seconds, against Threshold's 28 and AWS's own target-tracking policy at 12.6, for about 1 to 10 percent more cost. On synthetic bursts AWS's policy was a little faster, so SCALIX isn't better everywhere." Show the **SCALIX compared, run by run** table.

## 2. The three strategies on one situation (1 min)

In the second terminal:

```bash
.venv/bin/python -m scalix.algorithms.examples
```

**Say:** "Same input, three decisions. At 85% busy, Threshold adds 1 worker, but SCALIX adds 2 because it sizes the step to bring utilization to 60%. At 58% with demand rising, only SCALIX acts. And every decision prints its reason."

## 3. Run a spike live (2 min)

On the **Workload simulation** page:
1. Under **Demand patterns**, select only **Spike**, and set **Runs (seeds)** to 3.
2. Leave everything else at the defaults.
3. Press **Run simulation**. It takes about a second.

**Show:** the demand jump at minute 10, and the utilization lines.

Then open **Policy comparison**.

**Show:**
* **Workers over time:** SCALIX (green) climbs first and in bigger steps. Threshold (orange) climbs one worker at a time. Static (blue) stays flat.
* **Requests waiting:** Static's queue keeps growing. Threshold's queue is smaller, and SCALIX's is smallest.
* **The table:** response time (queueing model) and overload favour SCALIX; worker-minutes and cost favour Static and Threshold. Read out the verdict column.

## 4. The real evaluation (2 min)

Open `docs/EVALUATION.md`, or show `docs/figures/response.png`, `docs/figures/nasa_day.png` and `docs/figures/tradeoff.png`.

**Say:** "This is the fixed suite: 11 experiments, 1,552 runs, defined before running, all reported. With default settings, on a sudden spike SCALIX's p95 response was about 9 s against Threshold's 73 s. On 27 real days of NASA's 1995 web traffic it was 7 s against 28 s. With t3.micro list prices it cost only 2–4% more than Threshold, 10% on real traffic, because T3 CPU credits make spare workers cheap. When we tuned every strategy on separate training seeds they became almost equal on synthetic traffic, but on the real traffic only SCALIX still met our 10-second target at a normal cost."

To show it's reproducible, run one experiment live:

```bash
.venv/bin/python -m scalix.evaluation run --only main
git status --short results/
```

The run takes a few seconds. `git status` lists only `results/experiments/main/experiment.json`, and only its `created_utc` time has changed. Every results CSV is byte-for-byte identical, because the runs are deterministic. Put the timestamp back afterwards with `git checkout results/`. The full suite takes about 6 minutes, so don't run it during the viva.

## 5. Why SCALIX uses more workers (1 min)

Show section 9 of `EVALUATION.md`, the decision-log table, and `docs/figures/nasa_day.png`.

**Say:** "When utilization was low, SCALIX usually held its workers. The reasons were its 90-second cooldown and its rule of waiting for two low intervals. On real, noisy traffic that keeps it several workers above Threshold all day: 1.84 times the worker-time."

## 6. AWS safety, without AWS (2 min)

```bash
.venv/bin/python -m scalix.aws.controller --mock overload
```

**Show:**
* The data label says **MOCKED**.
* The policy wants +1, and the outcome is **DRY_RUN**.
* The list of live gates is mostly **FAIL**, and the output ends "No AWS change was made".

Then, in the dashboard:
* On the **AWS monitoring** page, open the **Mocked** tab and choose "stale" or "no data". The reading is held, never treated as 0%.
* On the **Scaling history** page, show the "actual AWS change?" column. It says NO for everything.

**Say:** "A real change would need LIVE mode in a local file, `live.enabled`, an allowlisted group, `--apply`, `--confirm` with the exact group name, live data, and the kill switch off. Even then it's capped at 2 workers and changes 1 at a time. None of that has ever been switched on."

## 7. Tests (1 min)

```bash
.venv/bin/python -m pytest -q tests/test_policies.py tests/test_java_parity.py
```

**Say:** "The Python policies are checked against the original Java policies on 15,000 situations. We also planted deliberate bugs to make sure the tests can actually fail."

## If something goes wrong

| Problem | Fix |
|---|---|
| Port 8501 is busy | `.venv/bin/streamlit run app.py --server.port 8600` |
| `ModuleNotFoundError` | `.venv/bin/pip install -r requirements.txt` |
| The Java parity tests are skipped | Java 17 or the engine jar is missing. Say so; the other tests still run. |
| The AWS page says boto3 is missing | Expected without `requirements-aws.txt`. Use the Mocked tab. |
