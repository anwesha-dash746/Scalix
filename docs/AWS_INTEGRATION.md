# AWS integration guide

> **Status: implemented, tested with mocks only, never run against real AWS.**
> No AWS infrastructure was created for this project, no real AWS call was ever made during development or testing, and no scaling action has ever been applied. **No result in this repository comes from AWS.**

This page is the overview. The step-by-step details are in [AWS_SETUP.md](AWS_SETUP.md) (read-only access) and [CONTROLLER.md](CONTROLLER.md) (the controller and the approved-action procedure).

## 1. Status of each part

| Part | Code | Status | How it was tested |
|---|---|---|---|
| Run modes (SIMULATION / READ_ONLY / LIVE) | `scalix/config.py` | Implemented | Unit tests (54) |
| Session from a named profile, with a read-only operation guard | `scalix/aws/session.py` | Implemented, **mocked** | Boto3 Stubber plus a network trap |
| Access and permission check | `scalix/aws/check_access.py` | Implemented, **mocked** | Stubber: full report, denied, rejected credentials, no group configured, no secret in the report |
| CloudWatch / Auto Scaling monitor | `scalix/aws/monitoring.py` | Implemented, **mocked** | Stubber, plus OK / STALE / MISSING / MALFORMED / ERROR cases |
| Controller (dry run by default) | `scalix/aws/controller.py` | Implemented, **mocked** | 63 logic tests plus 12 Stubber tests |
| Capacity writer (`SetDesiredCapacity` only) | `scalix/aws/writer.py` | Implemented, **never used against AWS** | Stubber only |
| Audit log | `scalix/aws/audit.py` | Implemented | Unit tests |
| Dashboard AWS page (read-only, on button press) | `ui/pages/aws.py` | Implemented, **mocked** | Streamlit AppTest with counted calls |
| CloudFormation template (VPC, launch template, Auto Scaling group, max 2) | – | **Not built** | – |
| Real account access check | – | **Not run** | – |
| Live scaling test | – | **Not run, not approved** | – |

"Mocked" means the code was exercised with AWS-shaped fake responses (Boto3's Stubber), never by AWS itself. A network trap fails the test if any request tries to leave the computer.

## 2. Permissions

SCALIX never creates or changes IAM. These are the *minimum* permissions each mode needs, for reference. On AWS Academy you usually can't attach policies; the lab's role decides.

**READ_ONLY:** five read operations, and the client refuses every other operation before sending it.

```json
{
  "Effect": "Allow",
  "Action": [
    "sts:GetCallerIdentity",
    "autoscaling:DescribeAutoScalingGroups",
    "ec2:DescribeInstances",
    "cloudwatch:GetMetricStatistics",
    "cloudwatch:ListMetrics"
  ],
  "Resource": "*"
}
```

**LIVE adds exactly one action**, scoped to one named group:

```json
{
  "Effect": "Allow",
  "Action": "autoscaling:SetDesiredCapacity",
  "Resource": "arn:aws:autoscaling:<region>:<account>:autoScalingGroup:*:autoScalingGroupName/<exact-group-name>"
}
```

**Never used:**
* `RunInstances` and `TerminateInstances`;
* `UpdateAutoScalingGroup`, `DeleteAutoScalingGroup` and `PutScalingPolicy`;
* any CloudFormation or IAM action;
* `GetMetricData`, because AWS always bills it;
* detailed 1-minute monitoring, which is also billed.

Tests check that the guards block these operations.

## 3. Restrictions built into the code

| Restriction | Enforced by |
|---|---|
| SIMULATION is the default. The mode is never taken from environment variables. | `config.py` |
| Only an explicitly named profile is used. Ambient credentials and environment keys are ignored, and root credentials are never asked for. | `session.make_session` |
| Credentials are never stored, printed or committed. Account IDs are masked in every report and log. | `session.py`, `.gitignore`, tests |
| Missing, stale (older than 15 min) or malformed CPU data means HOLD. It is never read as 0%. | `monitoring.py`, controller |
| Only an exact-name allowlisted group can be changed, to a maximum of **2** workers, 1 at a time, at most once per 5 minutes. | controller action checks |
| A write needs LIVE mode, `live.enabled`, the kill switch off, `--apply`, `--confirm <exact group>`, live data, and the controller not running on a worker. | controller live gates |
| One `SetDesiredCapacity` call, `HonorCooldown=True`, never retried. APPLYING is logged *before* the call. | `writer.py`, `audit.py` |
| Global stop: `touch config/LIVE_DISABLED` or `SCALIX_LIVE_DISABLED=1`. | writer and controller |
| The dashboard can't write to AWS and reads only on a button press. | `tests/test_ui.py` |
| Nothing is ever cleaned up or deleted automatically. | by design |

## 4. AWS Academy restrictions to expect

These are untested assumptions about a typical AWS Academy Learner Lab. The access check reports what your account actually allows.

* **Temporary credentials** expire after a few hours. Paste fresh ones into the same profile.
* **IAM is usually locked.** You can't create roles or attach the policies above, so the lab's role decides.
* **Regions and instance types may be limited.**
* **Fixed credit budget.** Resources left running keep using it, sometimes even after the lab session stops.

## 5. Costs

* **SIMULATION and the tests cost nothing.** They never contact AWS.
* **READ_ONLY** makes standard API requests only and creates no billable resources. CloudWatch API requests have a free-tier allowance on many accounts, but SCALIX doesn't assume your account has one.
* **LIVE and the infrastructure** would cost money. EC2 instances bill per second while running, and EBS volumes, public IPv4 addresses and any load balancer bill too. A cost estimate is part of the approval request, before anything is created.

## 6. What would come next, only with your approval at each step

1. **Template and cost review.** Write a CloudFormation template for the smallest possible environment: an Auto Scaling group with max 2 small instances, no load balancer. List every billable resource and its hourly cost. *Writing it costs nothing; deploying it needs approval.*
2. **Read-only access check** on your account: `python -m scalix.aws.check_access`. *It makes read calls only.*
3. **Stack creation.** This needs explicit approval of the exact template, region and expected cost.
4. **Dry runs** against the real group with `--watch`. *Read-only.*
5. **One live action.** This needs explicit approval of the exact group, the change (e.g. 1 → 2), its cost, its duration and the rollback. See [CONTROLLER.md §7](CONTROLLER.md#7-how-an-explicitly-approved-live-action-would-work).
6. **Rollback and stack deletion.** Each is a separately approved action.

None of these steps has been started.
