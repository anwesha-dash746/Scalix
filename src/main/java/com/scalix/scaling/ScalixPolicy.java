package com.scalix.scaling;

import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;

/**
 * Policy C: SCALIX workload-aware scaling.
 *
 * <p>Each decision interval:
 * <ol>
 *   <li>Estimate the arrival-rate trend over the last {@code observationWindow} intervals
 *       ({@link TrendAnalyzer}): RISING, FALLING, STABLE or UNKNOWN (too little history).</li>
 *   <li><b>Scale up</b> when utilization reaches {@code upper}, or earlier — at
 *       {@code preemptiveUpper} — when demand is RISING. The step is sized so the projected load
 *       (current busy capacity, extrapolated {@code projectionIntervals} ahead along the trend when
 *       rising) runs at {@code targetUtilization}, counting VMs that are still booting, bounded by
 *       {@code maxScaleUpStep} and the VM maximum.</li>
 *   <li><b>Scale down</b> one VM when utilization is at or below {@code lower}, demand is known not to
 *       be rising, and removing a serving VM would not push the remaining ones above
 *       {@code targetUtilization}.</li>
 *   <li>Scale-ups respect {@code scaleUpCooldownSeconds} since the last scale-up. Scale-downs respect
 *       {@code scaleDownCooldownSeconds} since the last action in <em>either</em> direction, which
 *       prevents add/remove oscillation.</li>
 *   <li>The VM count never leaves {@link VmLimits}; the minimum is at least 1.</li>
 * </ol>
 */
public final class ScalixPolicy implements ScalingPolicy {

    public static final String NAME = "SCALIX";

    /** Upper bound on the projected growth factor, so a noisy steep slope cannot explode the step. */
    private static final double MAX_PROJECTED_GROWTH = 2.0;

    private final VmLimits limits;
    private final double upper;
    private final double lower;
    private final double preemptiveUpper;
    private final double targetUtilization;
    private final int observationWindow;
    private final double trendThreshold;
    private final int projectionIntervals;
    private final double scaleUpCooldownSeconds;
    private final double scaleDownCooldownSeconds;
    private final int maxScaleUpStep;

    public ScalixPolicy(VmLimits limits, double upper, double lower, double preemptiveUpper,
                        double targetUtilization, int observationWindow, double trendThreshold,
                        int projectionIntervals, double scaleUpCooldownSeconds,
                        double scaleDownCooldownSeconds, int maxScaleUpStep) {
        PolicyValidation.thresholds(lower, upper);
        PolicyValidation.fraction("preemptiveUpper", preemptiveUpper);
        if (!(preemptiveUpper > lower && preemptiveUpper <= upper)) {
            throw new IllegalArgumentException("preemptiveUpper must be within (lower, upper], got " + preemptiveUpper);
        }
        if (!(targetUtilization > lower && targetUtilization <= upper)) {
            throw new IllegalArgumentException("targetUtilization must be within (lower, upper], got " + targetUtilization);
        }
        if (observationWindow < 2) {
            throw new IllegalArgumentException("observationWindow must be >= 2 intervals, got " + observationWindow);
        }
        PolicyValidation.nonNegative("trendThreshold", trendThreshold);
        if (projectionIntervals < 0) {
            throw new IllegalArgumentException("projectionIntervals must be >= 0, got " + projectionIntervals);
        }
        PolicyValidation.nonNegative("scaleUpCooldownSeconds", scaleUpCooldownSeconds);
        PolicyValidation.nonNegative("scaleDownCooldownSeconds", scaleDownCooldownSeconds);
        if (maxScaleUpStep < 1) {
            throw new IllegalArgumentException("maxScaleUpStep must be >= 1, got " + maxScaleUpStep);
        }
        this.limits = limits;
        this.upper = upper;
        this.lower = lower;
        this.preemptiveUpper = preemptiveUpper;
        this.targetUtilization = targetUtilization;
        this.observationWindow = observationWindow;
        this.trendThreshold = trendThreshold;
        this.projectionIntervals = projectionIntervals;
        this.scaleUpCooldownSeconds = scaleUpCooldownSeconds;
        this.scaleDownCooldownSeconds = scaleDownCooldownSeconds;
        this.maxScaleUpStep = maxScaleUpStep;
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
        final TrendAnalyzer.Estimate est = TrendAnalyzer.estimate(ctx.arrivalRateHistory(), observationWindow, trendThreshold);
        final Trend trend = est.trend();
        final double slope = est.relativeSlope();
        final double u = ctx.utilization();
        final int n = ctx.provisionedVms();
        final String trendTxt = fmt("trend %s (rel. slope %+.3f/interval over %d samples)", trend, printableSlope(slope), est.samples());

        final boolean overloaded = u >= upper;
        final boolean risingEarly = trend == Trend.RISING && u >= preemptiveUpper;
        if (overloaded || risingEarly) {
            final String why = overloaded
                ? fmt("utilization %.3f >= upper %.2f", u, upper)
                : fmt("utilization %.3f >= preemptive %.2f and demand rising", u, preemptiveUpper);
            if (n >= limits.max()) {
                return ScalingDecision.none(why + "; already at max " + limits.max() + " VMs; " + trendTxt, trend, slope, false);
            }
            if (ctx.time() - ctx.lastScaleUpTime() < scaleUpCooldownSeconds) {
                return ScalingDecision.none(why + "; scale-up suppressed by cooldown; " + trendTxt, trend, slope, true);
            }
            final int step = scaleUpStep(ctx, trend, slope);
            if (step <= 0) {
                return ScalingDecision.none(why + fmt("; %d booting VM(s) already cover projected demand; ", ctx.bootingVms()) + trendTxt,
                    trend, slope, false);
            }
            return ScalingDecision.up(step, why + "; " + trendTxt, trend, slope);
        }

        final boolean underloaded = u <= lower;
        if (underloaded) {
            final String why = fmt("utilization %.3f <= lower %.2f", u, lower);
            if (trend == Trend.RISING || trend == Trend.UNKNOWN) {
                return ScalingDecision.none(why + " but demand is " + trend + "; holding; " + trendTxt, trend, slope, false);
            }
            if (n <= limits.min()) {
                return ScalingDecision.none(why + "; already at min " + limits.min() + " VMs; " + trendTxt, trend, slope, false);
            }
            final double sinceLastAction = ctx.time() - Math.max(ctx.lastScaleUpTime(), ctx.lastScaleDownTime());
            if (sinceLastAction < scaleDownCooldownSeconds) {
                return ScalingDecision.none(why + "; scale-down suppressed by cooldown; " + trendTxt, trend, slope, true);
            }
            if (ctx.bootingVms() == 0) {
                if (ctx.activeVms() <= 1) {
                    return ScalingDecision.none(why + "; only one serving VM; " + trendTxt, trend, slope, false);
                }
                final double projected = u * ctx.activeVms() / (ctx.activeVms() - 1);
                if (projected > targetUtilization) {
                    return ScalingDecision.none(why + fmt("; removing a VM would raise utilization to %.3f > target %.2f; ",
                        projected, targetUtilization) + trendTxt, trend, slope, false);
                }
            }
            return ScalingDecision.down(1, why + "; " + trendTxt, trend, slope);
        }

        return ScalingDecision.none(fmt("utilization %.3f within band; ", u) + trendTxt, trend, slope, false);
    }

    /**
     * Number of VMs to add. Busy capacity is {@code activeVms * utilization}; when demand is rising
     * it is extrapolated {@code projectionIntervals} ahead. The fleet (including booting VMs) is
     * sized for {@code targetUtilization}. Always at least 1 when the current load is already at
     * or above {@code upper}, because saturated utilization understates the real demand.
     */
    int scaleUpStep(ScalingContext ctx, Trend trend, double slope) {
        double busy = ctx.activeVms() * ctx.utilization();
        if (trend == Trend.RISING) {
            final double growth = Double.isInfinite(slope) ? MAX_PROJECTED_GROWTH
                : Math.min(MAX_PROJECTED_GROWTH, 1 + slope * projectionIntervals);
            busy *= growth;
        }
        final int desired = (int) Math.ceil(busy / targetUtilization - 1e-9);
        int step = desired - ctx.provisionedVms();
        if (ctx.utilization() >= upper) {
            step = Math.max(step, 1);
        }
        step = Math.min(step, maxScaleUpStep);
        step = Math.min(step, limits.max() - ctx.provisionedVms());
        return step;
    }

    @Override
    public Map<String, String> parameters() {
        final Map<String, String> m = new LinkedHashMap<>();
        m.put("minVms", Integer.toString(limits.min()));
        m.put("initialVms", Integer.toString(limits.initial()));
        m.put("maxVms", Integer.toString(limits.max()));
        m.put("upper", Double.toString(upper));
        m.put("lower", Double.toString(lower));
        m.put("preemptiveUpper", Double.toString(preemptiveUpper));
        m.put("targetUtilization", Double.toString(targetUtilization));
        m.put("observationWindow", Integer.toString(observationWindow));
        m.put("trendThreshold", Double.toString(trendThreshold));
        m.put("projectionIntervals", Integer.toString(projectionIntervals));
        m.put("scaleUpCooldownSeconds", Double.toString(scaleUpCooldownSeconds));
        m.put("scaleDownCooldownSeconds", Double.toString(scaleDownCooldownSeconds));
        m.put("maxScaleUpStep", Integer.toString(maxScaleUpStep));
        return m;
    }

    /** Infinite slopes (zero-mean window) are printed as +/-999 to keep the reason readable. */
    private static double printableSlope(double d) {
        return Double.isFinite(d) ? d : Math.signum(d) * 999;
    }

    private static String fmt(String format, Object... args) {
        return String.format(Locale.ROOT, format, args);
    }
}
