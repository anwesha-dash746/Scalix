# Testing summary

## 1. Latest results (actual runs, 21 October 2026)

| Environment | Command | Result |
|---|---|---|
| Full environment: Python 3.13, `requirements.txt` + `requirements-aws.txt`, Java 17 + engine jar | `.venv/bin/python -m pytest -q` | **637 passed**, 0 failed, 0 skipped (78 s) |
| Clean environment: fresh venv with `requirements.txt` only (no boto3), all environment variables cleared | `python -m pytest -q` | **597 passed, 2 skipped**, 0 failed. The 2 skips are the two boto3 Stubber modules (40 tests), which skip with the reason "boto3 not installed". |
| Original Java engine (Part 1), on your Mac, 20 October 2026 | `./mvnw -B test` | 108 / 108 passed |

**No test contacts AWS.** The only real AWS contact so far was the read-only access check you ran by hand on 21 October 2026, outside the tests. The AWS tests use Boto3's Stubber and a network trap, a hook that fails the test if any request tries to leave the computer.

## 2. What is tested

| File | Tests | Covers |
|---|---|---|
| `tests/test_package_foundation.py` | 25 | Package imports without boto3 or Streamlit; `.gitignore` covers secrets; requirements are split correctly |
| `tests/test_scalix_config.py` | 55 | Run modes, safe defaults, unknown keys rejected, environment variables can't enable LIVE, the USD 20 budget setting |
| `tests/test_policies.py` | 126 | Static, Threshold and SCALIX decisions: boundaries, cooldowns, capacity limits, trend, spike, step sizing, reasons; *(Phase 8)* the AWS target tracking model: 3 data points to scale out, 15 below 90% of target to scale in, rounding up, warming instances counted, warm-up blocking scale-in, 5-minute data points, and the simulator's interval check |
| `tests/test_java_parity.py` | 5 | The Python policies equal the original Java policies on 15,000 random and boundary situations (action, step, trend, slope, cooldown flag, exact reason text) |
| `tests/test_workloads.py` | 29 | Scenario shapes, Poisson arrivals, seeding, reproducibility |
| `tests/test_simulation.py` | 38 | Queue model, start-up delay, billing, metrics and the original response proxy, checked against hand calculations |
| `tests/test_queueing_pricing.py` | 17 | *(Phase 7)* Erlang C against textbook values and the direct formula; the p95 against a Monte Carlo simulation; the utilization cap; the price adds up from its cited parts; CPU credits checked by hand, including workers removed while booting |
| `tests/test_traces_tuning_signal.py` | 23 | *(Phase 7)* The NASA trace keeps every request of the log (1,891,714) and the per-day totals; one scale constant; tuning picks the cheapest setting meeting the target in every scenario and never uses test seeds; the CPU demand signal equals demand below saturation and under-reports it when saturated |
| `tests/test_experiment.py` | 14 | Identical traces for every policy, output files, statistics (mean, std, 95% CI), reproducibility, the same-start rule |
| `tests/test_evaluation.py` | 14 | Suite fixed in advance; test seeds never overlap training seeds; identical traces across experiments; tuned experiments use the committed tuned settings; every report cell computed from the outputs; verdicts; paired differences; hold reasons; the committed report **and the tables in EVALUATION.md** equal what the committed CSVs regenerate |
| `tests/test_aws_logic.py` | 41 | Metric judging (OK / STALE / MISSING / MALFORMED / ERROR, never 0%), masking, access-check logic, no boto3 needed |
| `tests/test_aws_stubbed.py` | 28 | Read-only guard blocks writes and billed calls *before sending*; exact read requests; denied and expired credentials; explicit profile ignores environment keys |
| `tests/test_controller.py` | 65 | Data checks, action checks, every live gate, cooldowns, duplicates, unresolved actions, the kill switch, the audit log, the dashboard having no write path, and *(Phase 7)* the CPU-derived demand signal |
| `tests/test_cloudformation.py` | 4 | *(Phase 8)* The live-test stack, checked offline: max 2 t3 workers, the group name the controller expects, no scaling policies, no IAM, no load balancer, NAT or Elastic IP, no inbound access, free monitoring, a small disk deleted on termination, and the plan's safety steps |
| `tests/test_controller_stubbed.py` | 12 | The writer sends exactly one `SetDesiredCapacity` with `HonorCooldown=True`, never retries, re-checks the kill switch, and is blocked from every other write |
| `tests/test_ui.py` | 25 | Every dashboard page renders and is labelled; AWS reads only on a button press; errors are shown, not crashed on; no credentials shown; no write path; the strict budget notice in every mode; the last saved access check shown without contacting AWS; the mocked AWS tab has the live tab's controls and views; the animated charts carry exactly the simulated data, finish within 30 s and start only when visible |
| `dashboard/tests/` (Part 1 dashboard) | 116 | The original Java-engine dashboard, including 3 integration tests with the real engine jar |
| **Total** | **637** | |

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
| 7 | Erlang B instead of C, one slot per worker, waiting rate ignoring load, utilization cap ignored, p90 instead of p95, mean missing the backlog wait, price missing IPv4, credits ignoring vCPUs, credits netted across workers, CPU signal ignored, controller signal switch inverted, busy history missing the instance count, NASA day off by one, NASA scale changed, target judged on the mean scenario, tuning picking the fastest instead of the cheapest, training seeds overlapping test seeds, same-start check disabled | 18 | 18 |
| 8 | Target tracking: any instead of all data points, rounding down, scale-in point ignoring the 90% ratio, warm-up ignored, warming VMs not counted, data points not averaged; simulator interval check removed; paired difference reversed; stack max raised to 3; detailed monitoring switched on; replay longer than 30 s allowed | 11 | 11 |
| **Total** | | **102** | **102** |

Three times, the first run missed a bug or two, and each miss led to a new test before the re-run:

* **Phase 6:** 11 of 12 caught at first. The missed bug, `<=` changed to `<` in the low-utilization filter, led to a new boundary test.
* **Phase 7:** 17 of 18 caught at first. The missed bug, a changed NASA scale constant, revealed that the scale was defined in two places. It's now one constant with a test that pins it.
* **Phase 8:** 9 of 11 caught at first. The scale-in-ratio bug slipped through because the test case had too few workers for a scale-in to be possible, and the reversed paired difference had no direct test. Both now have one.

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
* **Real application latency.** The response time is a queueing-model output, not a measurement.
* **Real AWS bills.** Costs are computed from list prices; nothing checks them against an invoice.
* **Long-running behaviour of a real controller,** such as clock drift, restarts and credential expiry during a run.
* **Whole-run agreement with the Java engine.** Only the policies are compared decision for decision; the two simulators are different models.
