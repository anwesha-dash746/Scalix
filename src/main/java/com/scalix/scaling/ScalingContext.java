package com.scalix.scaling;

import java.util.List;

/**
 * Everything a policy may look at when deciding. It is a plain value built by the engine from
 * simulation measurements, so policies can be unit-tested without CloudSim Plus.
 *
 * @param time                 current simulated time (s)
 * @param provisionedVms       VMs counted against the limits: active + booting (draining VMs excluded)
 * @param activeVms            VMs currently accepting traffic
 * @param bootingVms           VMs requested but not yet accepting traffic
 * @param utilization          mean CPU utilization of active VMs over the last decision interval, in [0, 1]
 * @param utilizationHistory   per-interval utilization, oldest first, current interval last
 * @param arrivalRateHistory   per-interval arrival rate (requests/s), oldest first, current interval last
 * @param lastScaleUpTime      time of the last applied scale-up, or {@link Double#NEGATIVE_INFINITY}
 * @param lastScaleDownTime    time of the last applied scale-down, or {@link Double#NEGATIVE_INFINITY}
 */
public record ScalingContext(
    double time,
    int provisionedVms,
    int activeVms,
    int bootingVms,
    double utilization,
    List<Double> utilizationHistory,
    List<Double> arrivalRateHistory,
    double lastScaleUpTime,
    double lastScaleDownTime) {

    public ScalingContext {
        if (Double.isNaN(time) || time < 0) {
            throw new IllegalArgumentException("time must be >= 0");
        }
        if (provisionedVms < 0 || activeVms < 0 || bootingVms < 0) {
            throw new IllegalArgumentException("VM counts must be >= 0");
        }
        if (Double.isNaN(utilization) || utilization < 0 || utilization > 1.0 + 1e-9) {
            throw new IllegalArgumentException("utilization must be within [0, 1], got " + utilization);
        }
        utilizationHistory = List.copyOf(utilizationHistory);
        arrivalRateHistory = List.copyOf(arrivalRateHistory);
    }
}
