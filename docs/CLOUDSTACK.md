# Optional CloudStack integration

SCALIX does **not** need a cloud to run. The simulation and the dashboard work fully without one. This document describes the optional adapter layer in `dashboard/scalix_dash/cloud/`.

## Provider-neutral interface

`CloudProvider` (in `cloud/base.py`) has two operations:

| SCALIX decision (from `scaling_events.csv`) | `VmOperation` | Adapter method |
|---|---|---|
| `applied_delta = +n` (SCALE_UP) | `SCALE_OUT, count=n` | `scale_out(op)` |
| `applied_delta = −n` (SCALE_DOWN) | `SCALE_IN, count=n` | `scale_in(op)` |

`replay(events, provider)` sends a run's fleet-changing decisions to an adapter in time order.

## Adapters

| Adapter | Real resources? | Network? | Use |
|---|---|---|---|
| `MockCloudProvider` (default) | Never | Never | Records the intended operations with generated mock VM ids. It refuses to remove the last VM. The dashboard's *Under the hood → Connecting to a real cloud* section uses it. |
| `CloudStackProvider` | Only when explicitly enabled **and** confirmed | Only when enabled **and** confirmed | Maps operations to CloudStack API commands, as shown below. |

### CloudStack mapping

* **SCALE_OUT n** → `n × deployVirtualMachine` with `zoneid`, `templateid`, `serviceofferingid` and `group=<vm_group>`.
* **SCALE_IN n** → `n × destroyVirtualMachine` with `id=<newest VM this adapter created>` and `expunge=true`.

Requests are signed with the standard CloudStack scheme:
1. Sort the parameters by key.
2. URL-encode the values, using `%20` for spaces.
3. Lowercase the whole string.
4. Compute HMAC-SHA1 with the secret key.
5. Base64-encode the result and send it as `signature`.

## Safety rules (enforced in code and covered by tests)

1. **Disabled by default.** With `cloud.cloudstack.enabled: false` in `config/dashboard.yaml`, the adapter only *plans*. It returns the commands it would send and sends nothing.
2. **Secrets only from the environment.** The endpoint, API key and secret key are read from `CLOUDSTACK_ENDPOINT`, `CLOUDSTACK_API_KEY` and `CLOUDSTACK_SECRET_KEY`. You can rename these variables in the settings, but the values never go in files.
3. **Secrets never shown or passed on.** The config `repr` redacts them, and records omit the API key and signature. Response fields named `apikey`, `secretkey`, `signature` or `password` are redacted. The simulation engine is started without any `CLOUDSTACK_*` variables.
4. **Operator confirmation.** Even when the adapter is enabled and configured, every provider instance must be created with `confirm=True`. Otherwise it raises `ProviderSafetyError`.
5. **Conservative scale-in.** The adapter destroys only VMs it created itself, newest first, and never the last one. It never touches pre-existing VMs.
6. **The dashboard never executes.** The dashboard's cloud section (Under the hood) shows mock replays and *planned* CloudStack calls only. Real execution requires writing code that constructs `CloudStackProvider(config, confirm=True)`.
7. **Least privilege.** Use an API key whose account can only deploy and destroy VMs in a dedicated project or zone.

## Enabling it (for a future live test)

```bash
export CLOUDSTACK_ENDPOINT="https://<your-cloudstack>/client/api"
export CLOUDSTACK_API_KEY="..."
export CLOUDSTACK_SECRET_KEY="..."
```

Then set `cloud.cloudstack.enabled: true`, `zone_id`, `template_id` and `service_offering_id` in `config/dashboard.yaml`.

## Limitations

* The adapter is tested with a **fake transport only**. It has not been run against a live CloudStack server.
* `deployVirtualMachine` and `destroyVirtualMachine` are asynchronous in CloudStack, and the adapter does not poll `queryAsyncJobResult`.
* There is no retry or rate limiting.
* Live autoscaling would also need a control loop that feeds real metrics into a SCALIX policy. Part 2 doesn't provide one: the policies currently run inside the simulator.
