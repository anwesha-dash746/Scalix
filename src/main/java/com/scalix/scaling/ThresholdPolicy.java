package com.scalix.scaling;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Policy B: classic reactive threshold rule. Adds one VM when utilization exceeds {@code upper},
 * removes one when it falls below {@code lower}, within the VM limits. An optional cooldown
 * (default 0 = none) applies to both directions.
 */
public final class ThresholdPolicy implements ScalingPolicy {

    public static final String NAME = "THRESHOLD";

    private final VmLimits limits;
    private final double upper;
    private final double lower;
    private final double cooldownSeconds;

    public ThresholdPolicy(VmLimits limits, double upper, double lower, double cooldownSeconds) {
        PolicyValidation.thresholds(lower, upper);
        PolicyValidation.nonNegative("cooldownSeconds", cooldownSeconds);
        this.limits = limits;
        this.upper = upper;
        this.lower = lower;
        this.cooldownSeconds = cooldownSeconds;
    }

    @Override
    public String name() {
        return NAME;
    }

    @Override
    public VmLimits limits() {
        return limits;
    }

    @Override
    public ScalingDecision decide(ScalingContext ctx) {
        final double u = ctx.utilization();
        final int n = ctx.provisionedVms();
        final double lastAction = Math.max(ctx.lastScaleUpTime(), ctx.lastScaleDownTime());
        final boolean inCooldown = ctx.time() - lastAction < cooldownSeconds;

        if (u > upper) {
            if (n >= limits.max()) {
                return ScalingDecision.none(fmt("utilization %.3f > upper %.2f but already at max %d VMs", u, upper, limits.max()));
            }
            if (inCooldown) {
                return ScalingDecision.none(fmt("utilization %.3f > upper %.2f but in cooldown", u, upper), Trend.UNKNOWN, 0, true);
            }
            return ScalingDecision.up(1, fmt("utilization %.3f > upper %.2f", u, upper), Trend.UNKNOWN, 0);
        }
        if (u < lower) {
            if (n <= limits.min()) {
                return ScalingDecision.none(fmt("utilization %.3f < lower %.2f but already at min %d VMs", u, lower, limits.min()));
            }
            if (inCooldown) {
                return ScalingDecision.none(fmt("utilization %.3f < lower %.2f but in cooldown", u, lower), Trend.UNKNOWN, 0, true);
            }
            return ScalingDecision.down(1, fmt("utilization %.3f < lower %.2f", u, lower), Trend.UNKNOWN, 0);
        }
        return ScalingDecision.none(fmt("utilization %.3f within [%.2f, %.2f]", u, lower, upper));
    }

    @Override
    public Map<String, String> parameters() {
        final Map<String, String> m = new LinkedHashMap<>();
        m.put("minVms", Integer.toString(limits.min()));
        m.put("initialVms", Integer.toString(limits.initial()));
        m.put("maxVms", Integer.toString(limits.max()));
        m.put("upper", Double.toString(upper));
        m.put("lower", Double.toString(lower));
        m.put("cooldownSeconds", Double.toString(cooldownSeconds));
        return m;
    }

    private static String fmt(String format, Object... args) {
        return String.format(Locale.ROOT, format, args);
    }
}
