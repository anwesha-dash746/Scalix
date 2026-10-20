# Optional AWS access: read-only setup and monitoring

**Status:** read-only only. SCALIX can *read* an AWS account: who you are, Auto Scaling groups, EC2 instances and CloudWatch CPU. It cannot change anything. No infrastructure exists for this project yet, and nothing here creates any. Everything else in SCALIX works with no AWS at all.

## 1. Prerequisites

| Need | Why | How |
|---|---|---|
| AWS access you're allowed to use, e.g. an AWS Academy Learner Lab | to have an account to read | AWS Academy: *Start Lab*, then *AWS Details*, then *AWS CLI* shows temporary credentials (they expire, typically after a few hours) |
| boto3 | SCALIX's AWS library | `.venv/bin/pip install -r requirements-aws.txt` |
| A **named profile** in `~/.aws/credentials` | SCALIX only uses an explicitly named profile | see section 2 |
| `config/scalix.local.yaml` | tells SCALIX the profile **name**, region and (later) group name | `cp config/scalix.local.example.yaml config/scalix.local.yaml` |
| AWS CLI *(optional)* | only a convenience for writing the profile | `brew install awscli` on macOS |

## 2. Create the profile, on your own computer only

Credentials go in **one** place: your own `~/.aws/credentials` file. Never put them in the repository, in a SCALIX config file, or in a chat message.

**Option A, with the AWS CLI:**

```bash
aws configure --profile scalix-academy
```

It asks for the access key ID, secret key, region (e.g. `us-east-1`) and output format. AWS Academy credentials also include a session token. Add it with:

```bash
aws configure set aws_session_token PASTE_TOKEN_HERE --profile scalix-academy
```

**Option B, edit the file yourself.** Open `~/.aws/credentials` in a text editor and add this block, using the values from *AWS Details*:

```ini
[scalix-academy]
aws_access_key_id = <from AWS Details>
aws_secret_access_key = <from AWS Details>
aws_session_token = <from AWS Details>
```

Then run `chmod 600 ~/.aws/credentials` so only your user can read it. When the lab session expires, paste fresh values into the same block.

Then edit `config/scalix.local.yaml`, which is git-ignored:

```yaml
mode: READ_ONLY
aws:
  profile: scalix-academy
  region: us-east-1
  auto_scaling_group: null   # stays null until a group exists
```

## 3. Run the access check

```bash
.venv/bin/python -m scalix.aws.check_access
```

It prints a report and saves a copy to `results/aws/access-report-<time>.json` (git-ignored). If the check is missing anything (boto3, a profile, a region, credentials), it says what's missing and **makes no AWS call at all**.

What it checks:

| Check | AWS operation | Kind |
|---|---|---|
| AWS CLI installed, boto3 version, profile **names**, credentials present / temporary | none (local only) | local |
| Caller identity (masked account and role) | `sts:GetCallerIdentity` | read |
| Configured region | none | local |
| Describe Auto Scaling groups | `autoscaling:DescribeAutoScalingGroups` | read |
| Describe EC2 instances (first page, states only) | `ec2:DescribeInstances` | read |
| List CloudWatch EC2 CPU metrics (first page) | `cloudwatch:ListMetrics` | read |
| Read the group's CPU, only if a group is configured | `cloudwatch:GetMetricStatistics` | read |

* **Denials:** if AWS returns `AccessDenied` / `UnauthorizedOperation`, the report shows the error code and message exactly as returned. SCALIX does not try any workaround.
* **Rejected credentials** (expired, invalid): the check stops after the identity call.
* **Masking:** account IDs are shown as `********1234`, the role session name is shortened, and access key IDs are removed. Secret keys and tokens are never read by SCALIX's reporting code.

## 4. The monitoring adapter (`scalix/aws/monitoring.py`)

`CloudWatchMonitor` reads, for the one configured group:

* **Group state:** min, desired and max sizes, plus each instance's lifecycle state and health. This comes from `DescribeAutoScalingGroups`.
* **CPU:** the average `CPUUtilization` from `AWS/EC2`, using the `AutoScalingGroupName` dimension and a 5-minute period. This comes from `GetMetricStatistics`.

**How readings are judged.** Missing data is never treated as 0%:

| Status | Meaning | Value used for decisions |
|---|---|---|
| OK | newest data point is valid and at most 15 min old | yes, as 0–1 |
| STALE | newest point is older than `max_metric_age_seconds` (default 900 s) | **no** |
| MISSING | no data points (no running instances, or data delayed) | **no**, and not 0 |
| MALFORMED | not a number, outside 0–100%, from the future, wrong unit, or a malformed response | **no** |
| ERROR | the call failed (denied, expired, network) | **no** |

`snapshot()` combines both readings. AWS problems are listed in `errors`, never raised or replaced with zeros. `decision_ready` is true only when both readings are present and the CPU reading is OK. The adapter doesn't import the scaling algorithms; it only reads.

`MockMonitor` returns AWS-shaped scripted data, labelled **MOCKED**, for demos and tests. Its readings are judged by the same rules.

## 5. Data-source labels

Every value carries one of these labels (`scalix/sources.py`):

| Label | Comes from |
|---|---|
| `SIMULATED` | the local simulator. Every simulator CSV has a `data_source` column. |
| `MOCKED` | scripted test data shaped like AWS data, never from AWS |
| `LIVE_AWS` | read from a real account through the read-only adapter |

The dashboard (Phase 5) shows these labels on every page.

## 6. Safety design

* **Read-only guard.** Every AWS client SCALIX creates refuses any operation that isn't one of the five above, and raises `ReadOnlyViolation` before the request is sent. Tests check this for `SetDesiredCapacity`, `UpdateAutoScalingGroup`, `DeleteAutoScalingGroup`, `RunInstances`, `TerminateInstances`, `PutMetricData`, `GetMetricData`, `CreateStack` and `CreateRole`. They use a network trap that would catch any request that got through.
* **Explicit profile only.** READ_ONLY and LIVE need `aws.profile` and `aws.region`. With an explicit profile, Boto3 ignores `AWS_ACCESS_KEY_ID`-style environment variables, and a test confirms it.
* **SIMULATION never contacts AWS.** The monitor refuses to start in SIMULATION mode, and `import scalix` never loads boto3.
* **Fail fast.** 5 s connect timeout, 15 s read timeout, at most 2 attempts.

## 7. Operations not used, and why

| Operation | Why not |
|---|---|
| `cloudwatch:GetMetricData` | AWS always bills it, even within the free tier. `GetMetricStatistics` gets the same CPU average. |
| `autoscaling:SetDesiredCapacity` / `UpdateAutoScalingGroup` | changes capacity. That's LIVE mode only, after explicit approval (see [CONTROLLER.md](CONTROLLER.md)). |
| `ec2:RunInstances` / `TerminateInstances` | creates or deletes servers. SCALIX never calls these. |
| `cloudformation:CreateStack` / `DeleteStack` | creates or deletes infrastructure. That's a manual, separately approved step. |
| `iam:*` | SCALIX never creates or changes permissions, and never tries to escalate. |
| Detailed (1-minute) monitoring | AWS charges for it. SCALIX uses the free 5-minute data. |

## 8. Least-privilege permissions, for reference

This is the minimum an IAM identity needs for READ_ONLY mode. On AWS Academy you usually **can't** attach policies. The lab's pre-made role decides, and the access check shows what it allows.

```json
{
  "Version": "2012-10-17",
  "Statement": [{
    "Sid": "ScalixReadOnly",
    "Effect": "Allow",
    "Action": [
      "sts:GetCallerIdentity",
      "autoscaling:DescribeAutoScalingGroups",
      "ec2:DescribeInstances",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics"
    ],
    "Resource": "*"
  }]
}
```

These Describe, List and Get actions don't support resource-level restrictions, which is why `"Resource": "*"` is used.

## 9. Costs

The read calls don't create billable resources. CloudWatch `GetMetricStatistics` and `ListMetrics` count as standard API requests. AWS's pricing page lists 1 million API requests per month in the free tier, then about $0.01 per 1,000 ([CloudWatch pricing](https://aws.amazon.com/cloudwatch/pricing/)). SCALIX doesn't assume your account has a free tier, and doesn't promise that any AWS usage is free. AWS Academy accounts work from a fixed credit budget instead.

## 10. Limitations and open points

* **Only the access check has run against a real account.** On 21 October 2026 the read-only access check ran successfully against a real AWS Academy Learner Lab account (us-east-1, `voclabs` lab role): describing Auto Scaling groups, EC2 instances and CloudWatch metrics was permitted, and the account had no groups or instances. The monitor has not yet read a real group or real CPU, because no group exists.
* **No group exists yet,** so the group and CPU checks are skipped until one does. Creating one is a later, separately approved step.
* **CloudWatch delay.** Basic monitoring publishes every 5 minutes and arrives a few minutes late, so a fresh reading is normally 5–10 minutes old. A live controller can't react on the simulator's 15 s timescale.
* **No request rate without a load balancer.** EC2 and Auto Scaling metrics give CPU only, so SCALIX's demand-trend input is unavailable on AWS unless a (billable) load balancer is added. This has to be decided before LIVE mode.
* **Account restrictions vary.** AWS Academy labs often restrict regions, instance types and IAM. Nothing is assumed; the access check reports what is actually allowed.
* **First page only.** The access check reads only the first page of instances and metrics. It checks permission, not completeness.
