# SCALIX: Workload-Aware Auto-Scaling for Cost-Efficient Cloud Resource Management

SCALIX is a student research project. It compares three ways of deciding how many cloud worker servers to run as demand changes:

| Strategy | Rule, in one line |
|---|---|
| **Static** | Always the same number of workers (the baseline). |
| **Threshold** | Add 1 worker above 70% CPU, remove 1 below 30%, with a cooldown. |
| **SCALIX** | Also watches the *demand trend* and *sudden spikes*, scales up early and by the right amount, and scales down cautiously. |

**The question:** can a workload-aware strategy balance responsiveness and resource use better than the simpler ones? The project doesn't assume it can; the experiments decide.

## What's real and what's simulated

| Part | Status |
|---|---|
| Algorithms, simulator, metrics, experiments, tuning, dashboard | **Implemented and tested locally.** All results in this repository are **SIMULATED**, from a documented model. |
| Real traffic | One real trace, replayed in the simulator: the NASA web server log, July 1995 (request counts only). |
| Response time | From a queueing model (M/M/c). **Not measured latency.** |
| Cost | **Estimated** from AWS list prices: a t3.micro in us-east-1, plus IPv4, storage and T3 CPU credits. Not an AWS bill. |
| Read-only AWS monitoring (CloudWatch, Auto Scaling) | **Implemented and tested with mocks.** The read-only access check **ran successfully on a real AWS Academy account** on 21 October 2026, making 4 reads and changing nothing. No group exists yet, so no real group or CPU has been read. |
| AWS scaling controller | **Implemented, tested with mocks only.** It's dry-run by default, and **no real scaling action has ever been made.** |
| CloudFormation deployment template | **Not built yet.** No AWS infrastructure exists for this project. |

**No result here is an AWS measurement. Strict AWS budget: USD 20 only.**

## Headline results (simulated)

From [`docs/EVALUATION.md`](docs/EVALUATION.md). Synthetic results use 10 test seeds; real traffic uses all 27 NASA days.

* **Sudden spike:** SCALIX's p95 response time was **9.08 s** against Threshold's **72.73 s** and Static's 295.79 s, with non-overlapping 95% intervals.
* **Real traffic (NASA 1995):** SCALIX's p95 was **7.02 s** against Threshold's 27.92 s, but it used 1.84× Threshold's worker-time.
* **Cost:** at t3.micro list prices SCALIX cost only **2–4% more** than Threshold on synthetic traffic and **10% more** on real traffic, because T3 CPU credits make extra headroom cheap. Without credits, i.e. on a non-burstable instance, the premium would be 22–36% and 84%.
* **Tuned on separate training seeds,** all three strategies became almost equal on synthetic traffic. On real traffic, which tuning never saw, only SCALIX still met the 10 s p95 target at a normal cost.
* **Without a load balancer,** SCALIX using a CPU-only demand signal made the same decisions in every synthetic run and lost nothing measurable on real traffic.

SCALIX gave the best balance at default settings, at a small extra cost. The evaluation explains where it doesn't win and why, using the decision logs.

## Quick start (local, costs nothing, no AWS account needed)

You need Python 3.11+. Java 17+ is only needed for the original Part 1 engine and the Java comparison tests.

**macOS / Linux.** Paste the lines exactly as shown; zsh treats `#` in pasted lines as text, not as a comment, so no comments are included.

```bash
git clone https://github.com/anwesha-dash746/Scalix.git
cd Scalix
git checkout aws-edition
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

Open **http://localhost:8501**. Stop with **Ctrl+C**.

**Windows (PowerShell).** Same steps with `python -m venv .venv`, `.venv\Scripts\pip install -r requirements.txt`, then `.venv\Scripts\streamlit run app.py`.

Other useful commands:

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m scalix.tuning run
.venv/bin/python -m scalix.evaluation run
.venv/bin/python -m scalix.evaluation report
.venv/bin/python -m scalix.simulation --scenarios SPIKE --repetitions 3
.venv/bin/python -m scalix.algorithms.examples
.venv/bin/python -m scalix.aws.controller --mock overload
```

1. **Tests:** the whole suite, which takes about a minute.
2. **Tuning:** re-tunes every strategy on the training seeds (about 3 minutes).
3. **Evaluation run:** re-runs every experiment (about 6 minutes). Results are identical every time.
4. **Evaluation report:** rebuilds the results tables and figures from the saved outputs.
5. **One experiment:** a single custom experiment from the command line.
6. **Examples:** shows each strategy deciding on hand-made situations.
7. **Controller dry run:** uses mocked data and never contacts AWS.

AWS features are optional. Install them with `.venv/bin/pip install -r requirements-aws.txt` and read [`docs/AWS_INTEGRATION.md`](docs/AWS_INTEGRATION.md) first.

## Documentation

| # | Document | What it covers |
|---|---|---|
| 1 | This README | Overview, status, setup |
| 2 | [System architecture](docs/SYSTEM_ARCHITECTURE.md) | Diagram, components, data flow, safety layers |
| 3 | [Scaling algorithms](docs/ALGORITHMS.md) | Each strategy explained, with worked examples |
| 4 | [Simulation model](docs/SIMULATION.md) | Workloads, model, assumptions, metric definitions |
| 5 | [Evaluation](docs/EVALUATION.md) · [generated tables](docs/results/RESULTS_TABLES.md) | Methodology, results, where SCALIX doesn't win, and why |
| 6 | [Testing summary](docs/TESTING.md) | What is tested, actual results, planted-bug checks |
| 7 | [Limitations and future work](docs/LIMITATIONS.md) | Everything this project does not show |
| 8 | [AWS integration guide](docs/AWS_INTEGRATION.md) · [setup](docs/AWS_SETUP.md) · [controller](docs/CONTROLLER.md) | Status, permissions, restrictions, approved-action procedure |
| 9 | [Local demo script](docs/DEMO.md) · [dashboard guide](docs/DASHBOARD.md) | A 10-minute walkthrough for the viva |
| 10 | [Viva questions and answers](docs/VIVA.md) | Beginner-friendly Q&A |

Background: [AWS edition plan and phases](docs/AWS_EDITION.md) · [Part 1 Java/CloudSim Plus engine](docs/PART1_JAVA_ENGINE.md).

## Repository layout

```
scalix/                    Python package (no Streamlit, no AWS needed)
  algorithms/              Static, Threshold, SCALIX policies + trend/spike detection
  simulation/              workload generator, discrete-time simulator, experiment runner
  metrics/                 per-run metrics, statistics across seeds
  aws/                     optional: read-only monitor, access check, controller, writer, audit log
  evaluation.py            the fixed experiment suite and report generator
  tuning.py                tuning on training seeds (separate from the test seeds)
  pricing.py               AWS list prices used for the cost estimate
  config.py, sources.py    run modes (SIMULATION / READ_ONLY / LIVE), data-source labels
ui/                        Streamlit dashboard (streamlit run app.py)
tests/                     pytest suite for scalix/ and ui/
results/experiments/       evaluation outputs (summaries committed; large files regenerated)
results/tuning/            tuning results (grid and chosen settings)
data/traces/               the NASA July 1995 trace as request counts per 5 s
docs/                      documentation (see the table above), figures, generated tables
config/                    scalix.yaml (safe defaults); scalix.local.yaml is yours, git-ignored
src/, pom.xml, dashboard/  Part 1: the original Java engine and its dashboard (kept as reference)
```

## Safety and cost rules

* **SIMULATION is the default.** The simulator, dashboard and tests need no AWS account and no AWS library.
* **Nothing in this repository creates, changes or deletes AWS resources** without an explicitly approved, individual action. None has been approved or performed.
* **Credentials are never stored in the repository.** SCALIX only uses a named AWS CLI profile from your own computer.
