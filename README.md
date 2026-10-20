# SCALIX: workload-aware VM auto-scaling

SCALIX is a research prototype for cost-efficient cloud resource management. It compares three ways of deciding how many virtual machines (VMs) to run:

| Policy | Id | Behaviour |
|--------|----|-----------|
| A | `STATIC` | Keeps a fixed number of VMs for the whole run. |
| B | `THRESHOLD` | Adds 1 VM when utilization is above `upper` and removes 1 when it is below `lower`, within min/max limits. The cooldown is optional. |
| C | `SCALIX` | Uses utilization **plus** the arrival-rate trend over an observation window. It scales up early when demand is rising, sizes each step for a target utilization, has separate scale-up and scale-down cooldowns, and never removes the last VM. |

The project has two parts:

* **Part 1, simulation engine** (Java 17+, CloudSim Plus 8.5.7). Runs the policies on identical workloads and writes measured results as CSV files.
* **Part 2, dashboard and analysis** (Python, Streamlit). Configures and runs experiments through the engine's command line, validates and loads the CSVs, compares the policies with variability estimates, and exports the findings. An optional, safe-by-default cloud adapter layer is included.

Every number in the dashboard comes from engine output. The dashboard contains **no demonstration or synthetic data**. Fixture CSVs exist only inside `dashboard/tests/`, for testing.

> **AWS edition: in progress on the `aws-edition` branch.** SCALIX is moving to one Python codebase, the `scalix/` package, with an optional AWS integration. That integration covers EC2 Auto Scaling, CloudWatch and CloudFormation. By default everything runs locally in **SIMULATION** mode, needs no AWS account and costs nothing. Until the Python engine is finished, the dashboard still uses the Java engine described below. The structure, run modes, safety rules and phase status are in [`docs/AWS_EDITION.md`](docs/AWS_EDITION.md).

---

## 1. Architecture

```
 Streamlit UI (dashboard/app.py, views/)
        │ form → ExperimentParams.validate() → rep-NN.properties
        ▼
 scalix_dash.runner ──► java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --config … --out … --experiment-id rep-NN
        ▲                         │ CloudSim Plus simulation (Part 1)
        │                         ▼
 scalix_dash.results ◄── summary.csv, timeseries.csv, scaling_events.csv, vms.csv, workloads.csv, config_used.properties
        │ schema validation, valid repetitions only
        ▼
 scalix_dash.analysis / plots / exports ──► charts, tables, CSV downloads
```

The details are in [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md): the dashboard–engine contract, the data flow, the fairness controls, and the scaling algorithms.

## 2. Prerequisites (verified versions)

| Tool | Version used | Notes |
|------|--------------|-------|
| JDK | 23.0.1 | The project targets **Java 17**, so any JDK 17 or newer works. `java` must be on PATH. |
| Maven | 3.9.14, via the included wrapper (`mvnw` / `mvnw.cmd`) | No system Maven is needed. |
| CloudSim Plus | 8.5.7 (`org.cloudsimplus:cloudsimplus`) | Downloaded by Maven. |
| Python | 3.11.9 | 3.11 or newer is recommended. |
| Python packages | streamlit 1.66.0, pandas 3.0.6, numpy 2.4.6, matplotlib 3.11.2, PyYAML 6.0.3, pytest 9.1.1 | Pinned in `requirements.txt`. |

## 3. Installation

**Windows, one command.** From the project folder in PowerShell:

```powershell
.\run.ps1
```

The script:
1. checks for Java;
2. builds the simulator if needed;
3. creates `.venv` and installs `requirements.txt` on first use, and again whenever that file changes;
4. starts the dashboard and opens it in your browser.

Options:

| Command | What it does |
|---|---|
| `.\run.ps1 -Port 8600` | Uses another port. |
| `.\run.ps1 -Rebuild` | Rebuilds the simulator first. |
| `.\run.ps1 -Test` | Runs all Java and Python tests instead of launching. |
| `.\run.ps1 -NoBrowser` | Starts the server without opening a browser tab. |

If Windows blocks scripts, run `powershell -ExecutionPolicy Bypass -File .\run.ps1`.

**Manual setup, macOS / Linux.** Paste the lines exactly as shown. The blocks contain no inline `#` comments, because zsh, the macOS default shell, does not treat `#` as a comment in pasted commands.

```bash
./mvnw -B clean package
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
```

The first line builds the Java engine. It compiles, runs the 108 Java tests and builds the runnable jar. The other two lines create the Python environment. `requirements.txt` contains no AWS library. Install `requirements-aws.txt` instead only when you reach the optional AWS modes.

**Manual setup, Windows (PowerShell):**

```powershell
.\mvnw.cmd -B clean package
python -m venv .venv
.venv\Scripts\python -m pip install -r requirements.txt
```

## 4. Running the Java simulation directly

```bash
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --experiment-id exp-default-seed42
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --config configs/quick.properties --seed 7
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --workloads BURST --policies THRESHOLD,SCALIX --set vm.bootSeconds=60
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --help
```

The first command runs all 7 workloads × 3 policies with the defaults. Results go to `results/<experiment-id>/`, and the dashboard lists them automatically. Exit codes: `0` success, `2` invalid configuration (reported before any simulation runs), `1` runtime failure.

## 5. Launching the dashboard

macOS / Linux:

```bash
.venv/bin/streamlit run app.py
```

Windows:

```powershell
.venv\Scripts\streamlit run app.py
```

`app.py` in the project root hands over to `dashboard/app.py`, so `streamlit run dashboard/app.py` starts the same app.

Then open http://localhost:8501. `.streamlit/config.toml` binds the server to **localhost only**, because the dashboard can start processes, and turns off Streamlit's usage statistics.

### Running a complete SCALIX experiment from the dashboard

The dashboard uses plain names:

| Engine id | Shown as |
|-----------|----------|
| `STATIC` | Fixed fleet |
| `THRESHOLD` | Rule-based |
| `SCALIX` | SCALIX |
| `LOW`, `MEDIUM`, `HIGH` | Quiet day, Normal day, Busy day |
| `BURST` | Sudden rush |
| `INCREASING`, `DECREASING` | Growing demand, Winding down |
| `FLUCTUATING` | Up and down |

To run an experiment:

1. On the **Start** page, pick one or more traffic patterns.
2. Choose how careful the test should be:

   | Option | Runs |
   |---|---|
   | Quick look | 1 |
   | Balanced | 3 |
   | Thorough | 5 |

   Each run uses a different seed, `base + i`.
3. Optionally, open **Fine-tune the simulation** to change any technical setting: strategies, duration, server counts, thresholds, the SCALIX trend window and cooldowns, the seed, and an exact run count. A further **Show expert settings** switch reveals the trend threshold, the preemptive and target utilization, the decision interval, the boot time, the price and the drain limit.
4. Click **Run the comparison**. Progress appears live. If a setting is invalid, the problem is listed and nothing is run. Failed or timed-out runs are reported as such and left out. If Java or the jar is missing, a red message explains the fix and the button stays disabled.
5. When the runs finish, the app opens **Results** for the new experiment, which also appears in the sidebar under *Your experiments*.

| Page | What it shows |
|------|---------------|
| **Start** | What SCALIX solves, the three strategies, and the run form. |
| **Results** | Three verdict cards (fastest for users, cheapest to run, least idle capacity), each with an honest confidence note: "clear lead", "too close to call" or "single run". Then a plain trade-off sentence, one card per strategy (mean ± std), and the time chart. A collapsed *Full statistics* section has the fairness check, any-metric tables and bar charts with 95% CIs, the speed-vs-cost scatter, the best strategy per pattern with evidence, win counts, and % change vs the fixed fleet. |
| **Over time** | Incoming traffic, servers paid for, and how busy they were, for one run or the average of all runs. |
| **Decision log** | Each resize, with time, what was done, servers before → after, % busy, requests/s, demand trend and the recorded reason. Optionally includes checks where nothing changed. |
| **Download** | CSV files (per run, averages and spread, second-by-second, decisions, traffic patterns) and a zip with the raw engine files. |
| **Under the hood** | The problem explained, the method, metric definitions (measured vs calculated), the conditions and fairness of the selected experiment, simulator status, and the optional cloud adapter (mock replay and planned CloudStack calls). |

Dashboard settings (engine command, jar path, timeout, results folder, form defaults, cloud options) live in `config/dashboard.yaml`. To use a different file, set `SCALIX_DASHBOARD_CONFIG=<path>`.

## 6. Configuring workloads and policies

* **Dashboard:** the Start page. Pick the traffic pattern and the number of runs; every technical setting is under *Fine-tune the simulation*, and the rarely needed ones are behind *Show expert settings*.
* **Engine CLI:** `--config <file>` and `--set key=value`. Every key is documented in `src/main/resources/scalix-default.properties`, and unknown keys are rejected.

**Workloads.** Arrivals form a Poisson process whose rate can change over time. Each workload type gets its own seed, derived from the master seed.

| Workload | Default rate (requests/s) |
|---|---|
| LOW | 2 |
| MEDIUM | 5 |
| HIGH | 10 |
| BURST | 4, rising to 16 between ⅓ and ⅓ + 2⁄15 of the duration |
| INCREASING | 1 → 12, linear |
| DECREASING | 12 → 1, linear |
| FLUCTUATING | 6 ± 4, a sine wave with period 4⁄15 of the duration |

Requests are 1000–3000 MI on 1 PE, so one 2-PE VM handles about 1 request/s at 100% CPU.

**Defaults:**
* 10 hosts × 8 PEs; VM type 2 PEs × 1000 MIPS.
* Decision interval 15 s, VM boot 30 s.
* Thresholds 0.70 and 0.30; SCALIX window 6 intervals; cooldowns 30 s up and 90 s down.
* Price $0.10 per VM-hour (assumed), per-second billing, 60 s minimum.

All of these are **untuned starting values**.

## 7. Metric definitions

| Metric | Kind | Definition |
|--------|------|------------|
| Avg / p50 / p95 / p99 / max response time | MEASURED | `finish − arrival` per completed request (Cloudlet finish time from CloudSim Plus). Percentiles use linear interpolation. |
| Avg wait / service time | MEASURED | `start − arrival` / `finish − start`. |
| CPU utilization | MEASURED | Busy PE-seconds ÷ provisioned PE-seconds. Boot and drain time count as provisioned. |
| Mean active-VM utilization | MEASURED | Per-tick mean of `Vm.getCpuPercentUtilization()` over ACTIVE VMs, averaged over the run. |
| Avg / peak billable VMs | MEASURED | ACTIVE + BOOTING + DRAINING VMs per tick. |
| VM-seconds | MEASURED | Sum of VM lifetimes, from request to destruction. |
| Scale-up / scale-down / total actions; VMs added / removed; cooldown suppressions | MEASURED | Counted from the decision log. |
| **Simulated cost** | **CALCULATED** | `Σ max(60 s, ceil(lifetime)) / 3600 × $0.10`. This is an **assumed** pricing model, not a real price list. |
| Requests unfinished; drain timed out | MEASURED | Requests still unfinished when the run hit `simulation.maxDrainSeconds`. |

The dashboard adds statistics across repetitions:
* **n**: the number of valid runs;
* **mean**;
* **std**: sample standard deviation, using n−1;
* **min / max**;
* **95% CI**: Student-t, ± t × std/√n.

With one repetition, std and CI are shown as *n/a*. The full column-by-column reference is in [`docs/METRICS.md`](docs/METRICS.md), and every engine output folder includes `metric_definitions.csv`.

## 8. Methodology and fairness controls

* **Same requests.** Within a repetition, every policy receives the **identical request sequence**. Each workload is generated once per seed.
* **Same conditions.** VM type, hosts, timing, boot delay, drain limit and pricing come from **one configuration file** per repetition. The initial VM count is the same for every policy.
* **Repetitions** differ only in their seed. The *Full statistics* section of the Results page checks this from each repetition's `config_used.properties` and flags any difference.
* **Only valid runs are counted.** Repetitions that failed, timed out or produced invalid files are excluded and listed. Runs that hit the drain limit are excluded from statistics and counted.
* **No assumed winner.** The best policy is chosen per metric. A lead is called *clear* only when the 95% CIs don't overlap; otherwise it is labelled *not clear* or *single run*. The dashboard shows where STATIC or THRESHOLD do better.
* **Context is always visible.** The workload parameters, policy parameters, pricing model and seeds are shown next to the results.

## 9. Reproducing results

Runs are deterministic: the same configuration and seed give identical metrics.

* **Dashboard experiments.** `results/dashboard/<batch>/` contains `batch.yaml` (every parameter plus per-repetition status) and the exact `rep-NN.properties` passed to the engine. To re-run a repetition:

  ```bash
  java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --config results/dashboard/<batch>/rep-01.properties --experiment-id rerun-01
  ```

  Dashboard batches are git-ignored. Keep the folder, or its `batch.yaml`, to reproduce them.
* **CLI experiments.** `results/<id>/config_used.properties` can be passed back with `--config`.
* **Reference run.** `results/exp-default-seed42/` is the Part 1 reference run, produced with the default configuration and `--experiment-id exp-default-seed42`. It appears in the dashboard as a one-repetition experiment.

### Observations from the Part 1 reference run (seed 42, one repetition)

* SCALIX had the lowest average and p95 response time on HIGH, BURST, INCREASING, DECREASING and FLUCTUATING. It also made fewer scaling changes than THRESHOLD.
* SCALIX cost more than THRESHOLD on every workload and had lower CPU utilization.
* STATIC had the lowest cost on 6 of 7 workloads, but HIGH overwhelmed it (p95 246 s).
* On LOW, THRESHOLD was the cheapest.

These observations come from one seed with untuned parameters. Use repetitions in the dashboard before drawing conclusions.

## 10. Tests

macOS / Linux (on Windows use `mvnw.cmd` and `.venv\Scripts\python`):

```bash
./mvnw -B test
.venv/bin/python -m pytest
.venv/bin/python -m pytest -m engine
```

1. The Java suite: 108 tests, unit plus CloudSim Plus integration.
2. All Python tests, configured in `pytest.ini`. They live in `tests/` for the `scalix` package and in `dashboard/tests/` for the dashboard.
3. Only the tests that run the real Java engine.

The `tests/` suite (AWS edition) covers package imports, run-mode validation, and a check that `import scalix` works with no AWS credentials and never loads boto3. It also renders the root `app.py`, and checks that `.gitignore` excludes credential and environment files.

The `dashboard/tests/` suite covers:
* CSV loading and schema validation;
* missing, empty and malformed files;
* parameter validation;
* metric aggregation and confidence intervals;
* process failures (exit 1 or 2, timeout, unstartable executable, invalid or incomplete output, partial batches);
* download generation;
* catalog discovery;
* the mock and CloudStack adapters, including the safety rules and secret redaction;
* the Streamlit app, rendered headlessly with `streamlit.testing.v1.AppTest`, including form validation and running experiments;
* real-engine integration across all 7 workloads × 3 policies × 2 repetitions.

The real-engine tests skip, and say why, if Java or the jar is missing.

## 11. Known limitations

1. **Utilization saturates during a backlog.** When a queue builds up, utilization stays near 100% and hides the true demand, so both auto-scalers can over-provision and then sit inside their band without scaling down. SCALIX sizes scale-ups from utilization, not from demand (arrival rate × service time).
2. **Slow scale-down.** SCALIX removes one VM per decision, with a 90 s cooldown.
3. **Simplified model.** Boot delay is modelled by SCALIX's load balancer: the VM exists in CloudSim Plus and is billed from the request. Requests are CPU-only, with no RAM, network or failure modelling. There is one VM type and one datacenter.
4. **Execution-time overhead.** CloudSim Plus reports execution times about 1–2% above `length / MIPS`, at most 0.08 s per request. It applies to every policy equally.
5. **Wide intervals with few repetitions.** With n = 2, t = 12.71, so intervals are very wide. This is shown as computed, without clipping.
6. **Dashboard runs execute synchronously.** The page waits while the engine runs. A full 7 × 3 experiment at 900 s took about 4.5 s per repetition on the test machine, including JVM start-up. There is a per-repetition timeout (`engine.timeout_seconds`).
7. **Fixed infrastructure in the dashboard.** The form doesn't change the host or VM type, so the maximum fleet is 40 VMs. Use the CLI properties to change them.
8. **The CloudStack adapter is tested only with a fake transport.** It doesn't poll asynchronous jobs, and there is no live control loop. See [`docs/CLOUDSTACK.md`](docs/CLOUDSTACK.md).
9. **The shaded jar overlaps a few resources.** The build warns that some `META-INF` license and notice files and `module-info` files are duplicated across dependencies. This is harmless.

## 12. Optional CloudStack setup

Cloud integration is **off by default** and not needed for anything above.
* The default provider, `mock`, records intended operations and never provisions resources or makes network calls.
* A real CloudStack connection needs four things: `enabled: true` in `config/dashboard.yaml`, credentials **only** in environment variables (`CLOUDSTACK_ENDPOINT`, `CLOUDSTACK_API_KEY`, `CLOUDSTACK_SECRET_KEY`), a least-privilege account, and code that explicitly confirms (`confirm=True`).
* The dashboard itself only plans CloudStack calls and never executes them.

The decision mapping and the safety rules are in [`docs/CLOUDSTACK.md`](docs/CLOUDSTACK.md).

## 13. Project layout

```
pom.xml, mvnw, mvnw.cmd, .mvn/      Java build (CloudSim Plus 8.5.7, JUnit 5.12.2)
src/main/java/com/scalix/           Part 1 engine: config, workload, scaling, allocation, engine, metrics, export
src/main/resources/                 scalix-default.properties (all engine parameters, documented)
src/test/java/                      Java unit + integration tests
configs/                            example engine configurations
results/exp-default-seed42/         reference engine output
requirements.txt, pytest.ini        Python dependencies (no AWS library) and test configuration
requirements-aws.txt                optional extras for READ_ONLY / LIVE modes (boto3)
.streamlit/config.toml              localhost-only server, no usage statistics
config/dashboard.yaml               dashboard settings (engine command, paths, defaults, cloud)
config/scalix.yaml                  AWS-edition run mode: SIMULATION (default), READ_ONLY, LIVE
app.py                              entry point: streamlit run app.py (hands over to dashboard/app.py)
scalix/                             AWS-edition Python package: algorithms, simulation, metrics, aws, config
tests/                              pytest suite for the scalix package
dashboard/app.py                    Streamlit dashboard
dashboard/views/                    one module per page (start, results, over time, decision log, download, under the hood)
dashboard/scalix_dash/              core (no Streamlit): params, runner, results, catalog, analysis, story, plots, exports, settings
dashboard/scalix_dash/cloud/        provider interface, mock adapter, CloudStack adapter (disabled by default)
dashboard/tests/                    pytest suite, fixture writer and fake engine (test-only)
docs/                               ARCHITECTURE.md, METRICS.md, CLOUDSTACK.md, AWS_EDITION.md
```
