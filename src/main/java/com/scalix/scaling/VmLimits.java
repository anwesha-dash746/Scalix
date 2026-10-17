package com.scalix.scaling;

/**
 * Safe VM-count bounds for a policy. {@code min} is at least 1, so no policy can remove the last VM.
 */
public record VmLimits(int min, int initial, int max) {
    public VmLimits {
        if (min < 1) {
            throw new IllegalArgumentException("Minimum VM count must be >= 1 (the last VM can never be removed), got " + min);
        }
        if (max < min) {
            throw new IllegalArgumentException("Maximum VM count (" + max + ") must be >= minimum (" + min + ")");
        }
        if (initial < min || initial > max) {
            throw new IllegalArgumentException("Initial VM count " + initial + " must be within [" + min + ", " + max + "]");
        }
    }

    public int clamp(int count) {
        return Math.max(min, Math.min(max, count));
    }
}
