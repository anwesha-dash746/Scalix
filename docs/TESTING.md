# Testing summary

## 1. Latest results (actual runs, 21 October 2026)

| Environment | Command | Result |
|---|---|---|
| Full environment: Python 3.13, `requirements.txt` + `requirements-aws.txt`, Java 17 + engine jar | `.venv/bin/python -m pytest -q` | **570 passed**, 0 failed, 0 skipped (57 s) |
| Clean environment: fresh venv with `requirements.txt` only (no boto3), all environment variables cleared | `python -m pytest -q` | **530 passed, 2 skipped**, 0 failed. The 2 skips are the two boto3 Stubber modules (40 tests), which skip with the reason "boto3 not installed". |
| Original Java engine (Part 1), on your Mac, 20 October 2026 | `./mvnw -B test` | 108 / 108 passed |

**No test contacts AWS.** The AWS tests use Boto3's Stubber and a network trap, a hook that fails the test if any request tries to leave the computer.

## 2. What is tested

| File | Tests | Covers |
|---|---|---|
| `tests/test_package_foundation.py` | 25 | Package imports without boto3 or Streamlit; `.gitignore` covers secrets; requirements are split correctly |
| `tests/test_scalix_config.py` | 54 | Run modes, safe defaults, unknown keys rejected, environment variables can't enable LIVE |
| `tests/test_policies.py` | 119 | Static, Threshold and SCALIX decisions: boundaries, cooldowns, capacity limits, trend, spike, step sizing, reasons |
| `tests/test_java_parity.py` | 5 | The Python policies equal the original Java policies on 15,000 random and boundary situations (action, step, trend, slope, cooldown flag, exact reason text) |
| `tests/test_workloads.py` | 29 | Scenario shapes, Poisson arrivals, seeding, reproducibility |
| `tests/test_simulation.py` | 37 | Queue model, start-up delay, billing, metrics and the response proxy, checked against hand calculations |
| `tests/test_experiment.py` | 13 | Identical traces for every policy, output files, statistics (mean, std, 95% CI), reproducibility |
| `tests/test_evaluation.py` | 10 | Suite fixed in advance; identical traces across experiments; every report cell computed from the outputs; verdict rules; paired differences; scale-down hold reasons; the committed report equals what the committed CSVs regenerate |
| `tests/test_aws_logic.py` | 41 | Metric judging (OK / STALE / MISSING / MALFORMED / ERROR, never 0%), masking, access-check logic, no boto3 needed |
| `tests/test_aws_stubbed.py` | 28 | Read-only guard blocks writes and billed calls *before sending*; exact read requests; denied and expired credentials; explicit profile ignores environment keys |
| `tests/test_controller.py` | 63 | Data checks, action checks, every live gate, cooldowns, duplicates, unresolved actions, the kill switch, the audit log, and the dashboard having no write path |
| `tests/test_controller_stubbed.py` | 12 | The writer sends exactly one `SetDesiredCapacity` with `HonorCooldown=True`, never retries, re-checks the kill switch, and is blocked from every other write |
| `tests/test_ui.py` | 18 | Every dashboard page renders and is labelled; AWS reads only on a button press; errors are shown, not crashed on; no credentials shown; no write path |
| `dashboard/tests/` (Part 1 dashboard) | 116 | The original Java-engine dashboard, including 3 integration tests with the real engine jar |
| **Total** | **570** | |

## 3. Do the tests actually catch bugs? (planted-bug checks)

A test suite that never fails proves little. After each phase, small bugs were planted in the code on purpose, one at a time, and the tests were run. **Every planted bug was caught.**

| Phase | Area | Bugs planted | Caught |
|---|---|---|---|
| 1 | Policies, including `>` vs `>=` at thresholds, cooldown off-by-one, growth cap, rounding, scale-down boundary | 13 | 13 |
| 2 | Simulator and metrics | 10 | 10 |
| 3 | Read-only AWS layer | 10 | 10 |
| 4 | Controller, writer and audit log | 18 | 18 |
| 5 | Dashboard | 10 | 10 |
| 6 | Evaluation: CI formula, population std, verdict direction, tie naming, overlap rule, paired sign, pairing by seed, bold rule, hold threshold, hold reasons, seed count, base seed | 12 | 12 |
| **Total** | | **73** | **73** |

In Phase 6, the first run caught 11 of 12. The missed bug, `<=` changed to `<` in the low-utilization filter, led to a new boundary test, and the re-run caught all 12.

## 4. How to run

```bash
.venv/bin/python -m pytest -q
.venv/bin/python -m pytest -q tests/test_policies.py
.venv/bin/python -m pytest -q -m "not java"
```

1. **Full suite:** about a minute.
2. **One file:** useful when you're working on one area.
3. **Skip the Java comparison:** use this when Java isn't installed. Those tests also skip by themselves, with a reason, if `java` or the jar is missing.

## 5. What is not tested

* **Real AWS.** No test, and no development step, ever contacted AWS. The read-only monitor, access check, controller and writer are tested only against Boto3's Stubber and mocks. Real AWS responses, permissions, delays and AWS Academy restrictions are untested.
* **Real application latency.** The response time is a model output, not a measurement.
* **Long-running behaviour of a real controller,** such as clock drift, restarts and credential expiry during a run.
* **Whole-run agreement with the Java engine.** Only the policies are compared decision for decision; the two simulators are different models.
