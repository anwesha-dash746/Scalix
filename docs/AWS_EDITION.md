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
  algorithms/            scaling policies: STATIC, THRESHOLD, SCALIX      (Phase 1)
  simulation/            workload generator + simulation engine           (Phase 2)
  metrics/               metrics, statistics, result files                (Phase 2)
  aws/                   CloudWatch reader + Auto Scaling adapter, opt-in (Phase 4)
  config.py              run mode; SIMULATION by default                  (Phase 0)
tests/                   pytest suite for scalix/
app.py                   streamlit run app.py (hands over to dashboard/app.py)
config/scalix.yaml       run-mode settings (committed, safe defaults)
dashboard/               existing Streamlit dashboard (views/, scalix_dash/, tests/)
src/, pom.xml            existing Java engine, kept as a reference to cross-check the Python port
results/                 experiment output (the repository's equivalent of a data/ folder)
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
| `READ_ONLY` | Reads only (e.g. CloudWatch) | Never | `mode: READ_ONLY` in `config/scalix.yaml` |
| `LIVE` | Yes | Only one allowlisted Auto Scaling group | `mode: LIVE` **and** `live.enabled: true`, plus explicit approval |

* The mode is read only from `config/scalix.yaml`, never from environment variables.
* If the file is missing, SCALIX runs in SIMULATION mode.
* Unknown keys and invalid values are rejected with a clear message instead of being ignored.

## Safety rules (summary)

* **No AWS needed for the core project.** The simulator, dashboard and tests run with no AWS account or credentials. `import scalix` never loads boto3. A test checks this in a clean interpreter with every AWS variable removed.
* **boto3 is optional.** It is in `requirements-aws.txt`, not `requirements.txt`.
* **Credentials stay outside the repository.** They come only from Boto3's normal lookup: a named AWS CLI profile or temporary credentials. They are never hard-coded, printed or committed. `.gitignore` excludes `.env*`, `.aws/`, `credentials*`, `*_accessKeys.csv`, `*.pem` and `*.key`, and a test checks this.
* **No cloud change without approval.** No AWS resource is created, changed, scaled or deleted without explicit approval of that exact action. That includes clean-up, because a forgotten stack keeps billing.
* **More safeguards come in Phase 4:** the group allowlist, a capacity cap of 2 by default, cooldowns, duplicate-request protection, an audit log, a global live-action disable, refusal to act on stale or malformed metrics, and a dashboard that cannot write to AWS when a page is opened or refreshed.

## Phase status

| Phase | Content | Status |
|---|---|---|
| 0 | Inspection, package skeleton, run-mode config, dependencies, `.gitignore`, setup docs | Done |
| 1 | Python scaling policies, plus parity tests against the Java policies | Not started |
| 2 | Python workload generator, simulator, metrics and results | Not started |
| 3 | Dashboard on the Python engine, Plotly charts, SIMULATED labels | Not started |
| 4 | AWS adapters and safety guard, tested offline with Boto3's Stubber | Not started |
| 5 | CloudFormation template, billable-resource review and IAM policy (not deployed) | Not started |
| 6 | Viva preparation | Not started |

## Known issues and open questions

* **CloudWatch's free EC2 metrics arrive every 5 minutes.** One-minute detailed monitoring costs extra. LIVE mode will therefore decide less often than the simulator's 15 s interval, or treat data older than one period as stale. This is designed in Phase 4.
* **AWS Academy permissions are unknown.** Learner Lab accounts often cannot create IAM roles and use expiring temporary credentials. READ_ONLY mode will be the first thing to check what the account actually allows. The project does not assume any permission.
* **The Java build needs Maven Central.** If that network is blocked, the 3 real-engine Python tests are skipped (they say why). Everything else still runs.
