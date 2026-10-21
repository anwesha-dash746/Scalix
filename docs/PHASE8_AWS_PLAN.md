# Phase 8: the live AWS test (plan, cost and approval checkpoints)

> **Status: step 8.1 done (files written, nothing created). Steps 8.2–8.5 have NOT been run.** Each of them needs your explicit approval at the time, and you run them yourself in your AWS Academy lab, the **Cloud Architecting sandbox**. **Strict budget: USD 20 only.** This plan is designed to cost **well under USD 1**.

**The sandbox's rules (from its Readme, 21 October 2026) and how the stack fits them:**

| Sandbox rule | This stack |
|---|---|
| CloudFormation, EC2 Auto Scaling, CloudWatch allowed | uses only these, plus EC2 networking |
| Instance types t2/t3 nano–medium only | t3.micro (the template allows only t3.nano or t3.micro) |
| At most 9 instances, or the lab account may be suspended | at most 2 (group max 2); check nothing else is running |
| On-Demand only, Amazon-provided AMIs only | On-Demand, Amazon Linux 2023 |
| EBS gp2 only, at most 35 GB | one 8 GB gp2 disk per worker |
| IAM read-only | no IAM resources |
| Don't change the lab region | us-east-1; confirm it in the console's top-right corner |

## What the test shows

SCALIX's controller reads a **real** Auto Scaling group's CPU from CloudWatch, decides with the SCALIX policy (CPU-derived demand signal, no load balancer), and, once approved, changes the group's size **once**. That one change is visible in AWS's own activity history, which is independent proof.

## 8.1 What was built (no AWS contact)

| File | What it is |
|---|---|
| `aws/cloudformation/scalix-workers.yaml` | The stack (details below) |
| This document | Cost estimate, steps, approvals, deletion |

**The stack contains:**

* **Network:** a small VPC (`10.42.0.0/16`) with one public subnet, an internet gateway and a route table. These are free.
* **Firewall:** a security group with **no inbound rules**. Nobody can connect to the workers, and there's no SSH key.
* **Launch template:** Amazon Linux 2023 on a t3.micro with an 8 GB gp2 disk. It uses free 5-minute monitoring, and T3 Unlimited is set explicitly. The disk is gp2, not gp3, and unencrypted, because the AWS Academy Cloud Architecting sandbox allows only gp2 volumes up to 35 GB and only lists KMS keys; the disk holds nothing but the OS and the CPU burner.
* **Auto Scaling group `scalix-workers`:** min 1, **max 2**, desired 1. It has no scaling policies, because SCALIX's controller is the only thing that changes it.
* **Synthetic load:** each worker burns about 85% CPU for **25 minutes after boot**, then goes idle, so CloudWatch has something to show.
* **Not included:** no IAM roles (the sandbox can't create them, and the workers don't need any), no load balancer, no NAT gateway, no Elastic IP.

## Cost estimate (us-east-1 list prices, read 21 October 2026)

| Item | Price | Planned use | Estimate |
|---|---|---|---|
| t3.micro instance | USD 0.0104 per hour | about 1 h for worker 1 and 0.5 h for worker 2 | USD 0.016 |
| Public IPv4 address | USD 0.005 per hour per instance | 1.5 instance-hours | USD 0.008 |
| 8 GB gp2 disk | USD 0.10 per GB-month | 1.5 instance-hours | USD 0.002 |
| T3 surplus CPU credits | USD 0.05 per vCPU-hour above the 10% baseline | 2 workers × 2 vCPUs × 0.75 surplus × 25 min | USD 0.06 |
| CloudFormation, VPC, subnet, internet gateway, route table, security group, launch template, Auto Scaling | free | – | USD 0 |
| CloudWatch reads (`GetMetricStatistics`, about 30 calls) | standard API requests | – | about USD 0 |
| **Total for the planned test** | | | **about USD 0.09** |
| *If you forget to delete it for 24 hours* (2 idle workers) | | | about USD 0.80 |

**Worst realistic case:** the stack is left running for a week at 2 workers, which comes to about USD 5.50. That's inside USD 20, but the deletion step exists to prevent it. The sandbox Readme doesn't say what happens to resources when a session ends, but don't rely on that: **delete the stack before you end the lab.**

## Steps

You run every step yourself. Steps marked **APPROVAL** create, change or delete something, so do them only after you've decided to, in this conversation.

### 8.2 Create the stack (APPROVAL: starts billing, about USD 0.02 per hour)

1. Start the lab and wait for the green dot, then click **AWS** to open the console.
2. Check that the region (top right) is **N. Virginia, us-east-1**.
3. Go to **CloudFormation → Create stack → With new resources → Upload a template file**. Choose `aws/cloudformation/scalix-workers.yaml` from your `~/Scalix` folder.
4. **Stack name:** `scalix-phase8`. Keep the default parameters.
5. Click **Next** twice, then **Submit**. Wait for `CREATE_COMPLETE`, which takes about 3–5 minutes.
6. Check **EC2 → Auto Scaling groups**: `scalix-workers` should show 1 instance.

### 8.3 Point SCALIX at the group and watch (read-only, no approval needed)

Edit `config/scalix.local.yaml`. Nothing in it is secret:

```yaml
mode: READ_ONLY
aws:
  profile: scalix-academy
  region: us-east-1
  auto_scaling_group: scalix-workers
controller:
  policy: SCALIX
  demand_signal: CPU
live:
  enabled: false
  allowed_groups: [scalix-workers]
  max_capacity: 2
```

Then run:

```bash
cd ~/Scalix
.venv/bin/python -m scalix.aws.check_access
.venv/bin/python -m scalix.aws.controller --watch 3
```

* **The access check** should now read the group's CPU. The first CloudWatch data point arrives about 5–10 minutes after the instance starts.
* **`--watch 3`** makes three dry-run decisions, 5 minutes apart. While the synthetic load runs, SCALIX should propose `SCALE_UP 1 → 2`. As a dry run, it changes nothing.
* **The dashboard's AWS monitoring page** shows the same group and CPU when you press **Read group and CPU now**.

### 8.4 One real scaling action (APPROVAL: adds a second worker, about USD 0.03 more)

Only after 8.3 shows a sensible `SCALE_UP 1 → 2` proposal:

1. In `config/scalix.local.yaml`, set `mode: LIVE` and `live.enabled: true`.
2. Make sure there's no `config/LIVE_DISABLED` file.
3. Run:

```bash
.venv/bin/python -m scalix.aws.controller --apply --confirm scalix-workers
```

It makes **at most one** `SetDesiredCapacity` call, logs `APPLYING` before the call and `APPLIED` or `FAILED` after, and never retries. If AWS Academy refuses with `AccessDenied`, it records `FAILED` and stops; that's a valid result to report.

**Then, straight away:**

```bash
touch config/LIVE_DISABLED
.venv/bin/python -m scalix.aws.controller
```

* **The kill switch** (`touch config/LIVE_DISABLED`) blocks any further writes.
* **The plain controller run** is a dry run: it should show desired = 2.
* **Proof:** take screenshots of **EC2 → Auto Scaling groups → scalix-workers → Activity**, which shows AWS's own record of the change, the **Instances** list, and the dashboard's **Scaling history** page, which shows "actual AWS change? YES".

### 8.5 Delete everything (APPROVAL: stops all billing)

1. In **CloudFormation**, select `scalix-phase8`, click **Delete**, and wait for `DELETE_COMPLETE` (about 3–5 minutes). This terminates the workers and deletes their disks, the network and the group.
2. Set `mode: READ_ONLY` again and run `.venv/bin/python -m scalix.aws.check_access`. Expect **0 groups and 0 instances**.
3. Check the lab page's remaining credit.
4. End the lab.

## Stop conditions

Stop and tell me if any of these happen; don't work around it:

* stack creation fails, or asks for anything that isn't in this template;
* any `AccessDenied` you didn't expect;
* the credit drops by more than USD 1;
* anything other than `scalix-workers` would be changed.
