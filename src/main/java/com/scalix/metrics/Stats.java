package com.scalix.metrics;

import java.util.Arrays;

/** Small descriptive-statistics helpers. Empty inputs yield NaN rather than a fabricated value. */
public final class Stats {

    private Stats() { }

    public static double mean(double[] xs) {
        if (xs.length == 0) {
            return Double.NaN;
        }
        double s = 0;
        for (double x : xs) {
            s += x;
        }
        return s / xs.length;
    }

    public static double max(double[] xs) {
        return xs.length == 0 ? Double.NaN : Arrays.stream(xs).max().getAsDouble();
    }

    /**
     * Percentile with linear interpolation between closest ranks (same as numpy's default and
     * Excel PERCENTILE.INC).
     *
     * @param p percentile in [0, 100]
     */
    public static double percentile(double[] xs, double p) {
        if (p < 0 || p > 100) {
            throw new IllegalArgumentException("percentile must be within [0, 100]");
        }
        if (xs.length == 0) {
            return Double.NaN;
        }
        final double[] s = xs.clone();
        Arrays.sort(s);
        final double rank = p / 100.0 * (s.length - 1);
        final int lo = (int) Math.floor(rank);
        final int hi = (int) Math.ceil(rank);
        return s[lo] + (s[hi] - s[lo]) * (rank - lo);
    }
}
