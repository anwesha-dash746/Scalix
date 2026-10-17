# SCALIX metrics reference

Every column in `summary.csv` falls into one of four kinds. `metric_definitions.csv` lists them for every run, and the writer generates it from the same table it writes from.

| Kind | Meaning |
|------|---------|
| **ID** | An identifier. |
| **CONFIG** | An input parameter, copied from the configuration. |
| **MEASURED** | Comes directly from what happened in the CloudSim Plus run: Cloudlet start and finish times, VM state at each tick, and the decisions the policy made. No model sits between the simulator and the number. |
| **CALCULATED** | An estimate that applies an *assumed* model to measured values. Today only cost is calculated. |

## Response time (MEASURED)

For each request: `response = finish − arrival`.

* `arrival` is the time the generated request reaches the load balancer.
* `finish` is `Cloudlet.getFinishTime()` from CloudSim Plus.
* The response time includes three parts: time waiting at the load balancer, time queued on the VM (the space-shared scheduler is FIFO), and execution time.
* Averages and percentiles cover only **completed** requests. `requests_unfinished` reports the rest. With the default 3600 s drain limit, no default run left a request unfinished.
* Percentiles use linear interpolation between the closest ranks, the same method as `numpy.percentile` and Excel `PERCENTILE.INC`.

## Utilization (MEASURED)

* **`cpu_utilization`** = busy PE-seconds ÷ provisioned PE-seconds.
  * Busy PE-seconds: the sum over completed requests of `(finish − start) × pes`.
  * Provisioned PE-seconds: the sum over all VMs of `lifetime × vm.pes`, where a VM's lifetime runs from its request until it is destroyed. Boot and drain time count as provisioned, so idle paid-for capacity lowers this figure.
* **`mean_active_vm_utilization`**: every tick, `Vm.getCpuPercentUtilization()` is averaged over ACTIVE VMs, and those values are averaged over the run. This is the same signal the policies see.

## Fleet (MEASURED)

* `avg_billable_vms` and `peak_billable_vms` count ACTIVE + BOOTING + DRAINING VMs at each tick.
* `min_provisioned_vms` counts ACTIVE + BOOTING VMs. These are the VMs counted against the policy's limits.
* `vm_seconds` is the sum of VM lifetimes.
* VM count over time is in `timeseries.csv`. Per-VM lifetimes are in `vms.csv`.

## Scaling actions (MEASURED)

* `scale_up_actions` and `scale_down_actions` count decisions that actually changed the fleet. One decision that adds 3 VMs counts as one action. `vms_added` and `vms_removed` count the VMs themselves.
* `cooldown_suppressions` counts decisions where the policy wanted to act but a cooldown blocked it.
* `scaling_events.csv` records **every** policy evaluation. Each row has the input measurements, the trend, the requested and applied change, the VM count before and after, and the reason.

## Cost (CALCULATED, assumed pricing model)

The pricing model is assumed for illustration. It is **not** a real provider's price list.

```
lifetime(vm)      = destruction time (or run end) − request time
billedSeconds(vm) = max(minimumBillingSeconds, ceil(lifetime / granularity) × granularity)
cost(vm)          = billedSeconds(vm) / 3600 × pricePerVmHour
total_cost_usd    = Σ cost(vm)
```

The defaults are `pricePerVmHour = 0.10`, `granularity = 1 s` and `minimum = 60 s`. Change them with the `pricing.*` keys. Billing starts when a VM is requested, so boot time is paid for. It stops when the VM is destroyed, which happens only after the VM has drained its queue.

## Known measurement caveats

* **Execution-time overhead.** CloudSim Plus reports execution times (`finish − start`) slightly longer than the nominal `length / MIPS`. In checks with seed 42, the excess averaged 0.027 s per request at LOW load and 0.038 s at HIGH load, with a maximum of 0.083 s. That's about 1–2% of a 2 s request. It applies to every policy, and the reported values are the measured ones.
* **The run end is aligned to the 1 s tick.** The last VMs are billed up to the tick after the final request finishes.
* **Results come from a single seed.** No confidence intervals are computed. To estimate variability, repeat the experiment with several `--seed` values.
