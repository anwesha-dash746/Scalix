"""AWS list prices used for the simulated cost estimate (Phase 7).

The simulator's cost is still an *estimate*: real bills depend on the account, region, free tier,
credits and usage. But instead of an arbitrary rate, each worker is now priced as one small EC2
instance at AWS's published on-demand list prices, so the numbers mean something.

One worker = one ``t3.micro`` Linux instance in ``us-east-1`` (N. Virginia), the smallest
general-purpose size AWS Academy Learner Labs allow, with one public IPv4 address and an 8 GB gp3
boot volume. Prices were read from AWS's pages on 21 October 2026:

* t3.micro on-demand, Linux, us-east-1: USD 0.0104 per hour
  (https://aws.amazon.com/ec2/instance-types/t3/).
* T3 Unlimited surplus CPU credits, Linux: USD 0.05 per vCPU-hour; t3.micro has 2 vCPUs and a
  baseline of 10% per vCPU; T3 starts in Unlimited mode and gets no launch credits
  (same page, and https://docs.aws.amazon.com/AWSEC2/latest/UserGuide/burstable-performance-instances-unlimited-mode-concepts.html).
* Public IPv4 address in use: USD 0.005 per hour (https://aws.amazon.com/vpc/pricing/).
* gp3 storage: USD 0.08 per GB-month, AWS's example rate (https://aws.amazon.com/ebs/pricing/);
  8 GB / 730 hours per month = about USD 0.00088 per hour.

How the simulator uses them:

* **Hourly part** (instance + IPv4 + storage = USD 0.016277 per worker-hour) x billed worker-time.
  EC2 Linux and EBS bill per second with a 60 s minimum; IPv4 is treated the same way.
* **CPU-credit part (T3 Unlimited).** A worker earns credits at its baseline (10% of 2 vCPUs) for
  its whole life and spends them at its utilization x 2 vCPUs while serving. AWS charges any surplus
  when the instance is terminated, so each worker pays ``max(0, used - earned)`` vCPU-hours x
  USD 0.05. The simulator assumes CPU utilization equals the modelled utilization.

Not included: data transfer, load balancers, CloudWatch, taxes, free-tier allowances and AWS
Academy credits. The worker's request capacity (1 req/s) is a model assumption, not a t3.micro
benchmark.
"""

from __future__ import annotations

PRICES_READ_ON = "2026-10-21"
REGION = "us-east-1"
INSTANCE_TYPE = "t3.micro"

T3_MICRO_HOURLY = 0.0104
PUBLIC_IPV4_HOURLY = 0.005
GP3_PER_GB_MONTH = 0.08
BOOT_VOLUME_GB = 8
HOURS_PER_MONTH = 730
EBS_HOURLY = BOOT_VOLUME_GB * GP3_PER_GB_MONTH / HOURS_PER_MONTH

#: Hourly price of one worker: instance + public IPv4 + boot volume.
WORKER_HOURLY = T3_MICRO_HOURLY + PUBLIC_IPV4_HOURLY + EBS_HOURLY

T3_SURPLUS_CREDIT_PER_VCPU_HOUR = 0.05
T3_MICRO_VCPUS = 2
T3_MICRO_BASELINE_PER_VCPU = 0.10

PRICE_LABEL = (f"Estimated from AWS list prices read {PRICES_READ_ON}: {INSTANCE_TYPE} Linux on-demand in {REGION} "
               f"+ public IPv4 + {BOOT_VOLUME_GB} GB gp3, plus T3 Unlimited surplus CPU credits. Not an AWS bill.")
