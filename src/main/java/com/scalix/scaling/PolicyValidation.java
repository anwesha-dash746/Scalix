package com.scalix.scaling;

/** Shared parameter checks for policy constructors. */
final class PolicyValidation {

    private PolicyValidation() { }

    static void fraction(String name, double v) {
        if (!(v >= 0 && v <= 1)) {
            throw new IllegalArgumentException(name + " must be within [0, 1], got " + v);
        }
    }

    static void thresholds(double lower, double upper) {
        fraction("lower threshold", lower);
        fraction("upper threshold", upper);
        if (!(lower < upper)) {
            throw new IllegalArgumentException("lower threshold (" + lower + ") must be < upper threshold (" + upper + ")");
        }
    }

    static void nonNegative(String name, double v) {
        if (!(v >= 0) || Double.isInfinite(v)) {
            throw new IllegalArgumentException(name + " must be finite and >= 0, got " + v);
        }
    }
}
