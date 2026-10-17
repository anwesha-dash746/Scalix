package com.scalix.workload;

import java.util.Locale;

/** The demand patterns SCALIX is evaluated against. */
public enum WorkloadType {
    LOW,
    MEDIUM,
    HIGH,
    BURST,
    INCREASING,
    DECREASING,
    FLUCTUATING;

    public static WorkloadType parse(String name) {
        try {
            return valueOf(name.trim().toUpperCase(Locale.ROOT));
        } catch (IllegalArgumentException e) {
            throw new IllegalArgumentException("Unknown workload type '" + name + "'. Valid: "
                + java.util.Arrays.toString(values()), e);
        }
    }
}
