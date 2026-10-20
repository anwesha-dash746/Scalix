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
* **The table:** response proxy and overload favour SCALIX; worker-minutes and cost favour Static and Threshold. Read out the verdict column.

## 4. The real evaluation (2 min)

Open `docs/EVALUATION.md`, or show `docs/figures/response.png` and `docs/figures/tradeoff.png`.

**Say:** "This is the fixed suite: 4 scenarios × 3 strategies × 10 seeds, defined before running, all runs reported. On a spike, SCALIX's response proxy was about 2.5 s against Threshold's 23.6 s. But SCALIX cost 22 to 36% more than Threshold in every scenario, and on calm or slowly rising demand it was no faster. So it trades money for responsiveness to sudden changes."

To show it's reproducible, run it live:

```bash
.venv/bin/python -m scalix.evaluation run
git status --short results/
```

The run takes about 10 seconds. `git status` lists only the four `experiment.json` files, and only their `created_utc` time has changed. Every results CSV is byte-for-byte identical, because the runs are deterministic. Put the timestamps back afterwards with `git checkout results/`.

## 5. Why SCALIX costs more (1 min)

Show section 5 of `EVALUATION.md`, the decision-log table.

**Say:** "When utilization was low, SCALIX usually held its workers. The reasons were its 90-second cooldown and its rule of waiting for two low intervals. That caution is where the extra cost comes from."

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
