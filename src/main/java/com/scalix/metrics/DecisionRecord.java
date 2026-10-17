package com.scalix.metrics;

import com.scalix.scaling.ScalingAction;
import com.scalix.scaling.Trend;

/**
 * One evaluation of the scaling policy, whether or not it changed the fleet.
 *
 * @param time            simulated time (s)
 * @param action          action the policy returned
 * @param requestedDelta  VMs the policy asked to add (+) or remove (-)
 * @param appliedDelta    VMs actually added/removed after the engine's safety checks
 * @param vmsBefore       provisioned VMs before the decision
 * @param vmsAfter        provisioned VMs after the decision
 * @param utilization     interval utilization the policy saw
 * @param arrivalRate     interval arrival rate (requests/s) the policy saw
 * @param trend           trend the policy reported
 * @param relativeSlope   relative slope behind the trend
 * @param cooldownBlocked true if a cooldown suppressed an action
 * @param reason          policy's explanation (plus engine notes, if any)
 */
public record DecisionRecord(double time, ScalingAction action, int requestedDelta, int appliedDelta,
                             int vmsBefore, int vmsAfter, double utilization, double arrivalRate,
                             Trend trend, double relativeSlope, boolean cooldownBlocked, String reason) {

    public boolean changedFleet() {
        return appliedDelta != 0;
    }
}
