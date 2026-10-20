# SCALIX AWS edition

This file covers the AWS edition: its structure, run modes and safety rules, and how far each phase has got. The original Java and CloudSim Plus project is described in the [README](../README.md).

## Goal

SCALIX compares three ways of choosing how many worker VMs to run:

1. **Static:** a fixed number of workers.
2. **Threshold:** add a worker above 70% utilization and remove one below 30%.
3. **SCALIX:** combines utilization with the recent demand trend, separate cooldowns, capacity limits and a cautious scale-down.

The AWS edition moves the algorithms and the simulator to Python. It then adds an **optional** way to watch, and with explicit approval control, a real EC2 Auto Scaling group. The thresholds are experiment parameters, not proven optimums, and SCALIX is not assumed to be the best strategy. The experiments decide that.

## Structure

```
scalix/                  Python package (AWS edition)
  algorithms/            scaling policies: STATIC, THRESHOLD, SCALIX      (Phase 1, done)
  simulation/            workload generator + simulation engine           (Phase 2, done)
  metrics/               metrics, statistics, result files                (Phase 2, done)
  aws/                   read-only monitor + access check (Phase 3); controller, writer, audit log (Phase 4)
  config.py              run mode + AWS names; SIMULATION by default      (Phase 0, extended in 3)
  sources.py             SIMULATED / MOCKED / LIVE_AWS labels               (Phase 3)
tests/                   pytest suite for scalix/ (tests/java/ = Java parity harness, test-only)
app.py                   streamlit run app.py (hands over to dashboard/app.py)
config/scalix.yaml       run-mode settings (committed, safe defaults)
config/scalix.local.yaml your profile/region/group names (git-ignored; copy scalix.local.example.yaml)
dashboard/               existing Streamlit dashboard (views/, scalix_dash/, tests/)
src/, pom.xml            existing Java engine, kept as a reference to cross-check the Python port
results/                 experiment output (the repository's equivalent of a data/ folder);
                         results/simulations/ = Python simulator output
```

**How the structure fits the existing repository**

* **No separate `data/` folder.** `results/` already stores experiment output, and the dashboard already reads from it. A second folder would split the data in two.
* **The dashboard stays in `dashboard/`.** It already works and has 113 tests. The root `app.py` is a thin entry point, not a second copy.
* **The Java engine is kept.** The rest of the repository is Python, so the engine is ported to Python. The Java version stays as a reference, so tests can show that the Python policies make the same decisions on the same inputs.
* **`dashboard/scalix_dash/cloud/` (CloudStack) is kept for now.** Phase 4 replaces it with `scalix/aws/`. It is removed only after the AWS adapter covers the same ground.

## Run modes

| Mode | Contacts AWS? | Changes AWS? | How to turn it on |
|---|---|---|---|
| `SIMULATION` | Never | Never | Default. Nothing to do. |
| `READ_ONLY` | Reads only (CloudWatch CPU, Auto Scaling group state) | Never, enforced by a read-only guard on every client | `mode: READ_ONLY` plus `aws.profile` and `aws.region` in `config/scalix.local.yaml` |
| `LIVE` | Yes | Only one allowlisted Auto Scaling group | `mode: LIVE` **and** `live.enabled: true`, plus explicit approval |

* The mode is read only from `config/scalix.yaml` plus the optional, git-ignored `config/scalix.local.yaml`, never from environment variables.
* If neither file exists, SCALIX runs in SIMULATION mode.
* READ_ONLY and LIVE refuse to start without an explicitly named AWS profile, so ambient credentials are never used.
* Unknown keys and invalid values are rejected with a clear message instead of being ignored.

## Safety rules (summary)

* **No AWS needed for the core project.** The simulator, dashboard and tests run with no AWS account or credentials. `import scalix` never loads boto3. A test checks this in a clean interpreter with every AWS variable removed.
* **boto3 is optional.** It is in `requirements-aws.txt`, not `requirements.txt`.
* **Credentials stay outside the repository.** They come only from Boto3's normal lookup: a named AWS CLI profile or temporary credentials. They are never hard-coded, printed or committed. `.gitignore` excludes `.env*`, `.aws/`, `credentials*`, `*_accessKeys.csv`, `*.pem` and `*.key`, and a test checks this.
* **No cloud change without approval.** No AWS resource is created, changed, scaled or deleted without explicit approval of that exact action. That includes clean-up, because a forgotten stack keeps billing.
* **Read-only guard (done).** Every AWS client refuses any operation outside a 5-operation read-only allowlist before the request is sent. Missing, stale or malformed metrics are never treated as 0%. See [AWS_SETUP.md](AWS_SETUP.md).
* **LIVE-mode safeguards (done, offline):** exact-name group allowlist, approved maximum of 2, max step 1, a 5-minute controller cooldown, duplicate and unresolved-action protection, a single-run lock, an append-only audit log, a global kill switch (`config/LIVE_DISABLED`), refusal to act on stale, missing or malformed data, operator confirmation of the group name, and a dashboard with no path to AWS writes. See [CONTROLLER.md](CONTROLLER.md).

## Phase status

| Phase | Content | Status |
|---|---|---|
| 0 | Inspection, package skeleton, run-mode config, dependencies, `.gitignore`, setup docs | Done |
| 1 | Python scaling policies, plus parity tests against the Java policies ([ALGORITHMS.md](ALGORITHMS.md)) | Done |
| 2 | Python workload generator, simulator, metrics and results ([SIMULATION.md](SIMULATION.md)) | Done |
| 3 | Optional read-only AWS access: environment and permission check, CloudWatch / Auto Scaling monitor, data-source labels ([AWS_SETUP.md](AWS_SETUP.md)) | Done (mocked; not yet run against a real account) |
| 4 | AWS controller: policy -> safety checks -> dry run, or one gated SetDesiredCapacity; allowlist, approved max 2, cooldowns, duplicate prevention, audit log, kill switch ([CONTROLLER.md](CONTROLLER.md)) | Done (offline only; never applied to a real group) |
| Later | As briefed phase by phase: dashboard, CloudFormation template and cost review, viva preparation | Not started |

## Known issues and open questions

* **CloudWatch's free EC2 metrics arrive every 5 minutes.** One-minute detailed monitoring costs extra. LIVE mode will therefore decide less often than the simulator's 15 s interval, or treat data older than one period as stale. This is designed in Phase 4.
* **AWS Academy permissions are unknown.** Learner Lab accounts often cannot create IAM roles and use expiring temporary credentials. `python -m scalix.aws.check_access` reports what the account actually allows, using read-only calls. The project does not assume any permission.
* **No request-rate signal on AWS without a load balancer.** SCALIX's trend and spike checks need requests per second. EC2 and Auto Scaling metrics alone only give CPU, and a load balancer is billable. This has to be decided before LIVE mode.
* **The Java build needs Maven Central.** If that network is blocked, the 3 real-engine Python tests are skipped (they say why). Everything else still runs.
