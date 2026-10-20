# The AWS auto-scaling controller

**Status: implemented and tested offline only. It has never changed a real Auto Scaling group.** A live test needs your separate, explicit approval (see section 7).

Code: `scalix/aws/controller.py` (decisions), `scalix/aws/writer.py` (the only code that can change AWS) and `scalix/aws/audit.py` (the audit log).

```bash
.venv/bin/python -m scalix.aws.controller --mock overload
.venv/bin/python -m scalix.aws.controller --mock idle
.venv/bin/python -m scalix.aws.controller
.venv/bin/python -m scalix.aws.controller --watch 6
```

1. **`--mock overload`** is a demo with scripted MOCKED data. It never touches AWS.
2. **`--mock idle`** is the same kind of demo. The other mock scenarios are `calm`, `at-max`, `missing`, `stale`, `malformed` and `converging`.
3. **No flags:** one dry-run decision on your real group. It needs READ_ONLY mode and does reads only.
4. **`--watch 6`:** six dry-run decisions, 5 minutes apart.

## 1. What one decision does

```
read group + CPU (read-only) --> data checks --> policy decides --> action checks --> DRY RUN (default)
                                     |                |                  |                  |
                                   HOLD             HOLD             REFUSED        all live gates pass?
                                                                                     no -> REFUSED
                                                                                     yes -> APPLYING (audit)
                                                                                            1 x SetDesiredCapacity
                                                                                            APPLIED / FAILED (audit)
```

| Outcome | Meaning |
|---|---|
| `HOLD` | The data isn't fit for a decision, or the policy proposes no change. |
| `REFUSED` | The policy wanted a change, but a safety check or live gate blocked it. |
| `DRY_RUN` | The change passed every action check. It's reported but **not applied**. |
| `APPLIED` | Exactly one `SetDesiredCapacity` call succeeded. |
| `FAILED` | The call was made and AWS returned an error. The error is recorded and **never retried**. |

## 2. Data checks (any failure means HOLD)

| Check | Why |
|---|---|
| Target group configured | Nothing to control otherwise. |
| No monitoring errors | Denied or expired credentials, or a network error. |
| Group state available | The group could not be read. |
| CPU reading usable | MISSING, STALE (older than 15 min), MALFORMED and ERROR are never treated as 0% and never acted on. |
| Group is the target | The data must describe the configured group. |
| Group has at least one worker | SCALIX never starts a group from zero. |
| Group stable (no change in progress) | In service = desired and nothing is pending, so an earlier change has finished. This prevents piling actions on top of each other. |
| Desired within approved maximum | If someone set the group above `live.max_capacity`, SCALIX does nothing at all, not even a scale-down. A person must look first. |

## 3. Policy

The policy is the same code the simulator uses, chosen by `controller.policy`. Its capacity limits are the group's min and max, with the max capped at `live.max_capacity` (default **2**). CPU history and the last scale-up/down times come from the audit log, so the policies' own cooldowns work across runs.

**Demand signal without a load balancer (Phase 7).** On AWS without a load balancer there is no request-rate metric. Before Phase 7, SCALIX's trend was therefore always UNKNOWN, and by design it never scales down while demand is unknown. Now, with `controller.demand_signal: CPU` (the default), the controller gives SCALIX **CPU utilization × in-service instances** as its demand history: the busy-instance equivalents, i.e. the load actually served. The history comes from earlier runs in the audit log, which now records the in-service count. It costs nothing and needs no extra AWS permission. `demand_signal: NONE` restores the old behaviour.

* **What it can't see:** demand above what the group serves. When every instance is saturated, CPU × instances stays at the group's capacity, so SCALIX's trend under-reports a rise. Its 70% CPU rule still scales up.
* **What the simulation shows** ([EVALUATION.md](EVALUATION.md)): with this CPU-only signal, SCALIX's decisions were identical to the full-signal version on all 40 synthetic runs, and almost identical on the 27 NASA days. Under AWS-like timing (5-minute decisions, 120 s start-up, CPU-only signal, `nasa-aws`), SCALIX's p95 response was 7.89 ± 1.70 s lower than Threshold's per day, for 3.0% more estimated cost.

**Default policy: still THRESHOLD, for the first live test.** The approved first test caps the group at 2 instances, changes 1 instance at a time and waits 5 minutes between changes. At that size the two policies can only make the same few moves, and Threshold's single rule (above 70% add one, below 30% remove one) is the easiest to check by hand against the audit log. Once the controller is proven on real AWS, `controller.policy: SCALIX` is the choice the simulation supports. It's a one-line change in your local config file.

## 4. Action checks (any failure means REFUSED, in dry runs too)

| Check | Rule |
|---|---|
| Group on allowlist | `live.allowed_groups` must list the exact name. Wildcards are rejected. |
| Audit log readable | If SCALIX can't tell when it last acted, it doesn't act. |
| Controller cooldown | At least `live.min_seconds_between_actions` (default 300 s) since the last change, whatever the policy says. |
| Not a duplicate, nothing unresolved | An `APPLYING` entry with no recorded result blocks everything until a person checks. A change to a size the group already has is refused. |
| Within approved maximum | The new desired capacity must be from 1 to `live.max_capacity`. |
| *(adjustment)* | A step bigger than `live.max_step` (default 1) is reduced, and the reduction is noted in the reason. |

## 5. Live gates (every one must pass to apply)

| Gate | How it is satisfied |
|---|---|
| Apply requested | The `--apply` flag. Without it, every run is a dry run. |
| Mode is LIVE | `mode: LIVE` in `config/scalix.local.yaml`. |
| `live.enabled` | `live.enabled: true`. LIVE mode refuses to load without it. |
| Kill switch off | No `config/LIVE_DISABLED` file and no `SCALIX_LIVE_DISABLED` environment variable. The writer checks the switch **again** immediately before the call. |
| Group confirmed by operator | `--confirm <exact group name>` must match the configured group exactly. |
| Data is live AWS data | Mocked data can never cause a write. |
| Not running on an EC2 instance | The controller must not run on the workers it manages. This is checked by reading local files only, with no network. |
| Writer available | A writer is only created in LIVE mode with `live.enabled`, at the moment of use. |
| Single-run lock | Only one controller run may apply at a time (`audit.lock`). |

**The writer itself** (`CapacityWriter`) can't be built outside LIVE mode. Its client is guarded so it can call the read operations plus **only** `autoscaling:SetDesiredCapacity`. `UpdateAutoScalingGroup`, `DeleteAutoScalingGroup`, `TerminateInstanceInAutoScalingGroup` and `PutScalingPolicy` are blocked, and tests confirm it. Before the call it re-checks the group, the bounds (1 to `live.max_capacity`) and the kill switch. It sends `HonorCooldown=True`, so AWS also enforces the group's own cooldown, and it makes one attempt with no retries.

**READ_ONLY can never write.** No writer is ever created, the gates "mode is LIVE" and "live.enabled" fail, and every read client blocks `SetDesiredCapacity`.

**The dashboard cannot enable LIVE.** Dashboard code never imports `scalix.aws`, never references the writer, `--apply`, `apply=True`, the local config file, the allowlist or the kill switch. A test scans for these. A second test renders the dashboard with the controller and writer booby-trapped and checks they're never touched. LIVE can only be turned on by editing your local config file **and** running the command line with `--apply --confirm <group>`.

## 6. Audit log

The audit log is at `results/aws/audit.jsonl` (git-ignored). Mock runs go to `results/aws/audit-mock.jsonl`. It has one JSON line per decision, and two (`APPLYING`, then `APPLIED` or `FAILED`) for a write. Each line records the time, group, mode, data source, outcome, desired before/after, CPU status and value, the policy's decision and reason, and every check with pass/fail. Account IDs are masked.

**Global live-action disable:** `touch config/LIVE_DISABLED` stops all writes immediately. Delete the file to lift it. Setting `SCALIX_LIVE_DISABLED=1` also works. Environment variables can only disable, never enable.

### Dry-run output (MOCKED demo, `--mock overload`)

```
SCALIX controller [MOCKED: Mocked (test data, not from AWS)]
  Group:     mock-scalix-workers
  CPU:       OK (85.0%)
  Capacity:  desired 1 -> 2
  Policy:    SCALE_UP +1: utilization 0.850 > upper 0.70
  OUTCOME:   DRY_RUN: would change desired 1 -> 2; dry run, not applied
  Safety checks:
    [pass] ... (data and action checks)
  Live gates (all must pass to apply):
    [FAIL] apply requested: no (dry run)
    [FAIL] mode is LIVE: mode SIMULATION
    [FAIL] live.enabled: false
    [pass] kill switch off: off
    [FAIL] group confirmed by operator: pass --confirm <exact group name>
    [FAIL] data is live AWS data: MOCKED
    [pass] not running on an EC2 instance: controller host is not a worker
    [FAIL] writer available: no writer (READ_ONLY or mock)
  No AWS change was made.
```

Even with `--apply --confirm mock-scalix-workers`, mocked data gives `REFUSED: apply requested but live gate(s) failed: mode is LIVE; live.enabled; data is live AWS data; writer available`.

## 7. How an explicitly approved live action would work

This is the procedure. **None of it has been done.** Each numbered step that touches AWS needs your separate, explicit approval at the time.

1. **Infrastructure exists (separate approval).** A small Auto Scaling group with max 2 workers, created from a reviewed CloudFormation template after a billable-resource and cost review. Not built yet; see [AWS_INTEGRATION.md](AWS_INTEGRATION.md#6-what-would-come-next-only-with-your-approval-at-each-step).
2. **Read-only first.** `mode: READ_ONLY`, then `python -m scalix.aws.check_access` and several dry runs (`--watch`). Check that the group, CPU readings and proposed actions all make sense.
3. **Approval request.** I'd give you one written request: the exact group name and region, current to proposed desired capacity (for example 1 → 2), why the policy wants it, the cost of one extra worker per hour at that instance type's price, how long the test runs, and the rollback (desired back to its previous value, which is itself an approved action).
4. **You enable it locally.** In `config/scalix.local.yaml`, set `mode: LIVE`, `live.enabled: true` and `live.allowed_groups: [<exact name>]`. This file is never committed.
5. **One action.** `python -m scalix.aws.controller --apply --confirm <exact name>` makes at most one change and records APPLYING and then APPLIED or FAILED.
6. **Verify read-only.** Run the controller again without `--apply`. The group should show the new desired capacity, and the next decision is a dry run.
7. **Turn it off.** `touch config/LIVE_DISABLED` and set `mode: READ_ONLY` again. Rollback, and later deleting the infrastructure, are separate approved actions. Nothing is cleaned up automatically.

**Extra permission LIVE would need** beyond READ_ONLY, scoped to the one group:

```json
{
  "Effect": "Allow",
  "Action": "autoscaling:SetDesiredCapacity",
  "Resource": "arn:aws:autoscaling:<region>:<account>:autoScalingGroup:*:autoScalingGroupName/<exact-group-name>"
}
```

AWS Academy roles may not allow `SetDesiredCapacity` at all. If AWS returns AccessDenied, the controller records it as FAILED and stops. It never tries a workaround.

## 8. Limitations

* **Never run against real AWS.** Every test uses mocks, Boto3's Stubber, or a network trap that would catch any request that tried to leave.
* **Slow feedback.** CloudWatch basic data is 5–10 minutes old and the controller cooldown is 5 minutes, so the AWS controller reacts in minutes, not the simulator's 15 seconds.
* **CPU only.** There's no request-rate signal. SCALIX uses CPU × in-service instances instead, which can't see demand beyond what the group serves while it is saturated.
* **One action per run.** There's no unattended apply loop: `--watch` is dry-run only. That's deliberately conservative for a student project.
* **No draining.** Scale-down uses `SetDesiredCapacity`, and AWS picks which instance to terminate under the group's termination policy. In-flight requests on that instance aren't drained.
* **Wall-clock time.** Cooldowns use the controller machine's clock. A badly wrong clock would make them inaccurate. CPU readings from the future are already rejected.
