package com.scalix.scaling;

import java.util.List;

/**
 * Estimates the demand trend over an observation window with an ordinary least-squares line.
 *
 * <p>The slope is normalised by the window mean, giving a <em>relative</em> slope (fractional change
 * per sample), so one threshold works for both small and large workloads. When the mean is zero the
 * relative slope is defined as 0 for a flat series and ±infinity otherwise.
 */
public final class TrendAnalyzer {

    /** Result of a trend estimate. */
    public record Estimate(Trend trend, double relativeSlope, int samples) { }

    private TrendAnalyzer() { }

    /**
     * @param series    observations, oldest first (only the last {@code window} values are used)
     * @param window    number of most recent samples to consider; must be >= 2
     * @param threshold relative slope magnitude needed to call a trend RISING/FALLING; must be >= 0
     */
    public static Estimate estimate(List<Double> series, int window, double threshold) {
        if (window < 2) {
            throw new IllegalArgumentException("Trend window must be >= 2, got " + window);
        }
        if (!(threshold >= 0) || Double.isInfinite(threshold)) {
            throw new IllegalArgumentException("Trend threshold must be finite and >= 0, got " + threshold);
        }
        if (series == null || series.size() < 2) {
            return new Estimate(Trend.UNKNOWN, 0, series == null ? 0 : series.size());
        }
        final int n = Math.min(window, series.size());
        final List<Double> w = series.subList(series.size() - n, series.size());
        for (Double d : w) {
            if (d == null || Double.isNaN(d) || Double.isInfinite(d)) {
                throw new IllegalArgumentException("Trend series contains a non-finite value: " + d);
            }
        }

        final double rel = relativeSlope(w);
        final Trend trend;
        if (rel > threshold) {
            trend = Trend.RISING;
        } else if (rel < -threshold) {
            trend = Trend.FALLING;
        } else {
            trend = Trend.STABLE;
        }
        return new Estimate(trend, rel, n);
    }

    /** Least-squares slope per sample divided by the mean of the samples. */
    static double relativeSlope(List<Double> w) {
        final int n = w.size();
        final double xMean = (n - 1) / 2.0;
        double yMean = 0;
        for (double y : w) {
            yMean += y;
        }
        yMean /= n;

        double sxy = 0;
        double sxx = 0;
        for (int i = 0; i < n; i++) {
            final double dx = i - xMean;
            sxy += dx * (w.get(i) - yMean);
            sxx += dx * dx;
        }
        final double slope = sxy / sxx;
        final double eps = 1e-12;
        if (Math.abs(yMean) < eps) {
            if (Math.abs(slope) < eps) {
                return 0;
            }
            return slope > 0 ? Double.POSITIVE_INFINITY : Double.NEGATIVE_INFINITY;
        }
        return slope / Math.abs(yMean);
    }
}
