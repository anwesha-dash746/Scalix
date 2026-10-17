# SCALIX — Part 1: Simulation Engine

SCALIX is a workload-aware auto-scaling system for virtual machines (VMs). This part is a CloudSim Plus simulation that compares three VM allocation strategies under identical conditions:

| Policy | Id | Behaviour |
|--------|----|-----------|
| A | `STATIC` | Keeps a fixed number of VMs for the whole run. |
| B | `THRESHOLD` | Adds 1 VM when interval utilization is above `upper` and removes 1 when it is below `lower`, within min/max limits. The cooldown is optional and off by default. |
| C | `SCALIX` | Uses utilization **plus** the arrival-rate trend over an observation window. It scales up early when demand is rising, sizes each scale-up step for a target utilization (counting VMs that are still booting), and has separate scale-up and scale-down cooldowns. It never removes the last VM. |

Part 1 has no dashboard. The Streamlit dashboard is planned for a later part.

---

## 1. Verified stack

These were checked against what is actually installed, not assumed:

| Component | Version | How it was verified |
|-----------|---------|---------------------|
| JDK | 23.0.1 (Oracle). The project targets **Java 17**, so any JDK 17 or newer works. | `java -version` |
| Maven | 3.9.14, through the included Maven Wrapper (`mvnw` / `mvnw.cmd`). No system Maven is needed. | `./mvnw -version` |
| CloudSim Plus | **8.5.7**: `org.cloudsimplus:cloudsimplus`, the latest release on Maven Central at the time of writing. Its POM targets `<release>17</release>`. | `maven-metadata.xml` and the POM on repo1.maven.org |
| JUnit | 5.12.2 (`junit-jupiter`), the same version CloudSim Plus 8.5.7 uses. | |

CloudSim Plus also brings in commons-math3, commons-lang3, commons-collections4, gson, logback, slf4j and JetBrains annotations. SCALIX has no other runtime dependencies.

**API notes.** The original CloudSim, CloudSim Plus 7 and CloudSim Plus 8 have different APIs, and so does CloudAnalyst. Every call here was checked with `javap` and against the CloudSim Plus 8.5.7 sources. In particular:

* Packages are `org.cloudsimplus.*`, not `org.cloudbus.cloudsim.*`.
* The simulation class is `CloudSimPlus`, not `CloudSim`.
* The constructor arguments are `HostSimple(ram, bw, storage, peList)`, `CloudletSimple(id, length, pes)` and `VmSimple(id, mipsPerPe, pes)`.
* VMs are destroyed with `Vm.shutdown()`, which sends a `VM_DESTROY` event.
* `DatacenterBroker.setShutdownWhenIdle(false)` keeps the broker alive through quiet periods.
* A `CloudSimEntity` subclass with `schedule(...)` and `processEvent(...)` drives the periodic controller.

## 2. Install, build and test

```bash
# from the project root (Windows: use mvnw.cmd instead of ./mvnw)
./mvnw -B clean package        # compiles, runs all 108 tests, builds target/scalix-1.0.0-SNAPSHOT-all.jar
./mvnw -B test                 # tests only
```

The first build downloads Maven 3.9.14 (through the wrapper) and the dependencies from Maven Central.

## 3. Run the simulation

```bash
# All 7 workloads x 3 policies with the default configuration (about 3 s on a laptop)
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar

# Same, with a fixed output folder name (handy for reproducing)
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --experiment-id exp-default-seed42

# Without building a jar first
./mvnw -B -q compile exec:java -Dexec.args="--workloads BURST,HIGH --policies THRESHOLD,SCALIX"
```

Command-line options:

```
--config FILE          properties file overriding the defaults
--set KEY=VALUE        override one key (repeatable; applied after --config)
--workloads A,B,...    LOW,MEDIUM,HIGH,BURST,INCREASING,DECREASING,FLUCTUATING
--policies X,Y,...     STATIC,THRESHOLD,SCALIX
--seed N               master random seed
--out DIR              output root directory (default: results)
--experiment-id ID     output sub-directory (default: exp-yyyyMMdd-HHmmss)
--help
```

Exit codes: `0` success, `2` invalid configuration or arguments (reported before any simulation runs), `1` runtime failure.

## 4. Configuration

All experimental values live in `src/main/resources/scalix-default.properties`. That file documents every key and serves as the reference list of valid keys. Unknown keys are rejected to catch typos. Invalid values (negative durations, `lower >= upper`, `minVms < 1`, a VM larger than a host, more VMs than the datacenter can hold, non-numeric values and so on) are rejected with a clear message.

Example configs:

* `configs/quick.properties`: 180 s workloads for fast checks.
* `configs/cold-start.properties`: the auto-scalers start with 2 VMs instead of 8.

Main defaults:

| Area | Default |
|------|---------|
| Infrastructure | 10 hosts × 8 PEs × 1000 MIPS. VM type: 2 PEs × 1000 MIPS, 2 GB RAM, the same for every policy. |
| Requests | One Cloudlet per request, 1 PE, length uniform in 1000–3000 MI (about 2 s on one PE). One VM serves about 1 request/s at 100% CPU. |
| Timing | Arrival phase 900 s, controller tick 1 s, scaling decision every 15 s, VM boot 30 s. |
| STATIC | 8 VMs. |
| THRESHOLD | upper 0.70, lower 0.30, cooldown 0 s, min 1, initial 8, max 20. |
| SCALIX | upper 0.70, lower 0.30, preemptive 0.55, target 0.60, window 6 intervals (90 s), trend threshold 3% per interval, projection 2 intervals, cooldown 30 s up / 90 s down, max step 3, min 1, initial 8, max 20. |
| Pricing | $0.10 per VM-hour (assumed), per-second billing, 60 s minimum. |

All thresholds, windows and cooldowns are **untuned starting points**, not claimed optima.

## 5. How the simulation works

```
WorkloadGenerator ──(identical request list per workload)──► ScalixSimulation (one per policy)
                                                               │
            CloudSim Plus: Datacenter, Hosts, Broker, VMs      │ Controller entity, every tick:
            (space-shared CloudletScheduler = FIFO per VM)     │  1. activate booted VMs, destroy drained VMs
                                                               │  2. sample Vm.getCpuPercentUtilization()
                                                               │  3. every decision interval:
                                                               │       build ScalingContext → policy.decide()
                                                               │       → engine re-checks limits → VmPool applies
                                                               │       → DecisionRecord (reason, VM count)
                                                               │  4. LoadBalancer dispatches arrivals as Cloudlets
                                                               ▼
                                             RunResult → RunSummary → CSV export
```

* **Workloads** (`workload/`): requests arrive as a Poisson process whose rate can change over time (generated by Lewis–Shedler thinning). Each workload type has its own seed, derived from `simulation.seed`. Each workload is generated **once**, and the same request list goes to every policy.
  * LOW, MEDIUM, HIGH: constant rates of 2, 5 and 10 requests/s.
  * BURST: 4 requests/s with a 16 requests/s burst from 300 to 420 s.
  * INCREASING: rises linearly from 1 to 12 requests/s.
  * DECREASING: falls linearly from 12 to 1 request/s.
  * FLUCTUATING: 6 ± 4 requests/s, a sine wave with a 240 s period.

  Each workload's parameters, seed, request count and expected count are written to `workloads.csv`.
* **Decision logic** (`scaling/`): policies implement `ScalingPolicy.decide(ScalingContext)`. They are pure functions of measured inputs (time, VM counts, interval utilization, utilization and arrival-rate history, last scale times), so they are unit-tested without CloudSim Plus.
  * `TrendAnalyzer` fits a least-squares slope over the observation window and normalises it by the mean. The result is RISING, FALLING, STABLE, or UNKNOWN when there are fewer than 2 samples.
* **Allocation** (`allocation/`):
  * `LoadBalancer` sends each request to the ACTIVE VM with the fewest outstanding requests. It is the same for every policy.
  * `VmPool` handles VM lifecycles: BOOTING → ACTIVE → DRAINING → TERMINATED.
  * Scale-down removes booting VMs first, then the least-loaded active VM, and never the last serving VM.
  * A VM being removed is **drained**: it gets no new work and is destroyed only after its queue is empty.
  * Initial VMs skip the boot delay. VMs added by a scale-up wait `vm.bootSeconds` before taking traffic and are billed from the moment they are requested.
* **Safety:** the engine clamps every decision to the policy's limits again before applying it, as a backstop to the policy's own checks.

## 6. Output

Each experiment writes its results to `results/<experiment-id>/`:

| File | Content |
|------|---------|
| `summary.csv` | One row per run. It holds the run id, workload type, policy, seeds, configuration and policy parameters, and every aggregated metric. |
| `scaling_events.csv` | One row per policy evaluation: time, action, requested and applied change, VMs before and after, the utilization and arrival rate the policy saw, trend, slope, whether a cooldown blocked it, and the **reason**. |
| `timeseries.csv` | One row per run per tick: VM counts by state, utilization of active VMs, arrivals, and requests in the system. |
| `vms.csv` | Each VM's request, activation, drain and termination times, lifetime, billed seconds and cost. |
| `workloads.csv` | Workload parameters, arrival pattern, seed, and generated vs expected request counts. |
| `metric_definitions.csv` | Every summary column, labelled ID, CONFIG, MEASURED or CALCULATED, with its definition. |
| `config_used.properties` | The complete effective configuration. Pass it to `--config` to re-run the experiment. |
| `requests.csv` | Optional (`--set export.requests=true`). One row per request with its length and all timings. |

**Measured vs calculated.** Response times, utilization, VM counts and scaling actions are measured from the simulation. Cost is calculated by applying the documented, assumed pricing model to the measured VM lifetimes. The exact formulas are in [`docs/METRICS.md`](docs/METRICS.md).

## 7. Reproducing experiments

Runs are deterministic. The same configuration and seed give byte-identical CSV metrics, except `wall_clock_ms`, which depends on the machine. An integration test checks this.

```bash
./mvnw -B clean package
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --experiment-id exp-default-seed42      # defaults, seed 42
java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --config results/exp-default-seed42/config_used.properties --experiment-id rerun
for s in 1 2 3 4 5; do java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --seed $s --experiment-id seed-$s; done
```

## 8. Results of the default run (seed 42)

These numbers were produced by `java -jar target/scalix-1.0.0-SNAPSHOT-all.jar --experiment-id exp-default-seed42`. They come from **one seed** with **untuned parameters** and support no general claim. The full data is in `results/exp-default-seed42/`.

| Workload | Policy | Avg RT (s) | p95 RT (s) | CPU util | Avg VMs | Cost ($) | Scale-ups | Scale-downs |
|---|---|---|---|---|---|---|---|---|
| LOW | STATIC | 2.01 | 2.93 | 0.252 | 8.00 | 0.2007 | 0 | 0 |
| LOW | THRESHOLD | 2.10 | 3.01 | 0.421 | 4.80 | 0.1221 | 3 | 7 |
| LOW | SCALIX | 2.02 | 2.94 | 0.314 | 6.43 | 0.1613 | 2 | 6 |
| MEDIUM | STATIC | 2.14 | 3.04 | 0.638 | 8.00 | 0.2007 | 0 | 0 |
| MEDIUM | THRESHOLD | 2.08 | 2.98 | 0.485 | 10.54 | 0.2643 | 3 | 0 |
| MEDIUM | SCALIX | 2.07 | 2.96 | 0.435 | 11.73 | 0.2943 | 2 | 0 |
| HIGH | STATIC | 127.95 | 246.48 | 0.982 | 8.00 | 0.2598 | 0 | 0 |
| HIGH | THRESHOLD | 2.84 | 9.26 | 0.606 | 16.90 | 0.4240 | 10 | 0 |
| HIGH | SCALIX | 2.47 | 5.84 | 0.554 | 18.51 | 0.4642 | 4 | 0 |
| BURST | STATIC | 38.57 | 115.33 | 0.708 | 8.00 | 0.2004 | 0 | 0 |
| BURST | THRESHOLD | 17.26 | 60.66 | 0.494 | 11.50 | 0.2889 | 12 | 10 |
| BURST | SCALIX | 9.08 | 28.72 | 0.379 | 14.98 | 0.3754 | 5 | 6 |
| INCREASING | STATIC | 26.44 | 92.66 | 0.751 | 8.00 | 0.2242 | 0 | 0 |
| INCREASING | THRESHOLD | 2.14 | 3.08 | 0.617 | 10.96 | 0.2773 | 13 | 3 |
| INCREASING | SCALIX | 2.06 | 2.98 | 0.490 | 13.81 | 0.3467 | 6 | 1 |
| DECREASING | STATIC | 53.18 | 87.06 | 0.811 | 8.00 | 0.2000 | 0 | 0 |
| DECREASING | THRESHOLD | 6.32 | 24.10 | 0.449 | 14.47 | 0.3638 | 12 | 17 |
| DECREASING | SCALIX | 4.64 | 20.42 | 0.363 | 17.93 | 0.4483 | 4 | 5 |
| FLUCTUATING | STATIC | 9.14 | 21.08 | 0.769 | 8.00 | 0.2007 | 0 | 0 |
| FLUCTUATING | THRESHOLD | 3.05 | 7.54 | 0.503 | 12.25 | 0.3078 | 22 | 17 |
| FLUCTUATING | SCALIX | 2.14 | 3.04 | 0.343 | 18.00 | 0.4515 | 6 | 4 |

What the data shows, **for this configuration only**:

* SCALIX had the lowest average and p95 response time on HIGH, BURST, INCREASING, DECREASING and FLUCTUATING. It needed fewer scaling actions than THRESHOLD in every workload.
* SCALIX was **more expensive than THRESHOLD in every workload** and had lower CPU utilization: it buys latency with extra capacity.
* THRESHOLD was the cheapest auto-scaler. It also oscillated the most (FLUCTUATING: 22 scale-ups and 17 scale-downs).
* STATIC was the cheapest overall on most workloads, but HIGH overloaded it (p95 246 s).
* On LOW, THRESHOLD was the cheapest of all three ($0.12 vs $0.20 for STATIC).
* Both auto-scalers **over-provisioned on MEDIUM**: they scaled up from 8 to 11–12 VMs and never scaled back down. See the limitations below.

## 9. Known limitations

1. **Utilization saturates during a backlog.** When a queue builds up, utilization stays near 100% until the queue drains, which hides the real demand. Both auto-scalers keep adding VMs during that time and then sit at the bottom of their band (utilization 0.3–0.4, above `lower`), so they don't scale back down. SCALIX's trend detection uses the arrival rate, but it sizes scale-ups from utilization. Sizing from measured demand (arrival rate × service time) is a candidate improvement. It is not implemented.
2. **Slow scale-down.** SCALIX removes 1 VM per decision and waits 90 s after any action, so shedding capacity after a peak takes minutes (see DECREASING).
3. **Single seed and untuned parameters.** No confidence intervals are computed. Repeat with several `--seed` values before drawing conclusions.
4. **Boot delay is modelled by SCALIX, not CloudSim Plus.** CloudSim Plus creates the VM immediately. SCALIX's load balancer withholds traffic for `vm.bootSeconds`, and billing starts at the request. CloudSim Plus' own `Vm.setStartupDelay` is not used.
5. **Requests are CPU-only.** Cloudlets use a full-CPU utilization model and no RAM or bandwidth model. Network latency, request payloads and failures are not simulated.
6. **The controller runs on a 1 s tick.** The load balancer routes the next second's arrivals using the state at the start of the tick. Each Cloudlet's exact arrival offset is preserved through its CloudSim Plus submission delay. Requests arriving in [0, 1) s wait for the initial VMs to be placed, which affects every policy the same way.
7. **Execution-time overhead.** CloudSim Plus reports execution times about 1–2% longer than nominal (mean +0.03 s, max +0.08 s per request). The measured values are reported unchanged.
8. **One VM type and one datacenter.** Policies only scale horizontally.
9. **The shaded jar overlaps a few resources.** The build warns that dependencies share `META-INF` license and notice files and `module-info` files. This is harmless: the application runs and all tests pass.

## 10. Project layout

```
pom.xml                         Maven build (CloudSim Plus 8.5.7, JUnit 5.12.2, shade + exec plugins)
mvnw, mvnw.cmd, .mvn/           Maven Wrapper (3.9.14)
configs/                        example experiment configurations
docs/METRICS.md                 metric definitions, measured vs calculated, pricing model
src/main/resources/scalix-default.properties   all parameters with documentation
src/main/java/com/scalix/
  SimulationMain.java           CLI entry point
  config/SimulationConfig       loading, overrides, validation
  workload/                     WorkloadType, WorkloadSpec, WorkloadGenerator, Request
  scaling/                      ScalingPolicy, Static/Threshold/ScalixPolicy, TrendAnalyzer,
                                ScalingContext, ScalingDecision, VmLimits, PolicyFactory
  allocation/                   LoadBalancer, VmPool, ManagedVm, VmState
  engine/                       ScalixSimulation (CloudSim Plus run), ExperimentRunner
  metrics/                      RunResult, RunSummary, RequestRecord, TimeSeriesPoint,
                                DecisionRecord, VmRecord, CostModel, Stats
  export/                       CsvWriter, ResultExporter
src/test/java/com/scalix/       unit tests (policies, trend, cooldowns, limits, config,
                                workloads, VM pool, metrics, CSV) and integration tests
                                (all 21 workload×policy runs, determinism, CSV output, CLI)
```
