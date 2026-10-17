package com.scalix.scaling;

/**
 * Output of a policy evaluation.
 *
 * @param action          what to do
 * @param delta           number of VMs to add (positive) or remove (negative); 0 for NONE
 * @param reason          human-readable justification, recorded in the scaling-event log
 * @param trend           demand trend the policy observed (UNKNOWN if the policy ignores trends)
 * @param relativeSlope   relative slope behind {@code trend} (0 if unused)
 * @param cooldownBlocked true if the policy wanted to act but a cooldown prevented it
 */
public record ScalingDecision(ScalingAction action, int delta, String reason, Trend trend,
                              double relativeSlope, boolean cooldownBlocked) {

    public ScalingDecision {
        if (action == ScalingAction.SCALE_UP && delta <= 0) {
            throw new IllegalArgumentException("SCALE_UP needs a positive delta");
        }
        if (action == ScalingAction.SCALE_DOWN && delta >= 0) {
            throw new IllegalArgumentException("SCALE_DOWN needs a negative delta");
        }
        if (action == ScalingAction.NONE && delta != 0) {
            throw new IllegalArgumentException("NONE must have delta 0");
        }
    }

    public static ScalingDecision none(String reason) {
        return new ScalingDecision(ScalingAction.NONE, 0, reason, Trend.UNKNOWN, 0, false);
    }

    public static ScalingDecision none(String reason, Trend trend, double slope, boolean cooldownBlocked) {
        return new ScalingDecision(ScalingAction.NONE, 0, reason, trend, slope, cooldownBlocked);
    }

    public static ScalingDecision up(int vms, String reason, Trend trend, double slope) {
        return new ScalingDecision(ScalingAction.SCALE_UP, vms, reason, trend, slope, false);
    }

    public static ScalingDecision down(int vms, String reason, Trend trend, double slope) {
        return new ScalingDecision(ScalingAction.SCALE_DOWN, -vms, reason, trend, slope, false);
    }
}
