package com.scalix.metrics;

/**
 * Fleet state sampled once per controller tick.
 *
 * @param time            simulated time (s)
 * @param provisionedVms  ACTIVE + BOOTING
 * @param activeVms       VMs accepting traffic
 * @param bootingVms      VMs requested but still booting
 * @param drainingVms     VMs finishing their queue before being destroyed
 * @param utilization     mean CPU utilization of ACTIVE VMs at this instant, from
 *                        {@code Vm.getCpuPercentUtilization()}; NaN if no VM is active
 * @param arrivals        requests that arrived during this tick
 * @param inSystem        requests arrived but not finished (queued at the balancer or on a VM)
 */
public record TimeSeriesPoint(double time, int provisionedVms, int activeVms, int bootingVms,
                              int drainingVms, double utilization, int arrivals, int inSystem) {

    /** VMs being paid for at this instant. */
    public int billableVms() {
        return provisionedVms + drainingVms;
    }
}
