package com.scalix.workload;

import com.scalix.config.SimulationConfig;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Map;

/**
 * Defines a workload: its duration, its arrival pattern (a time-varying Poisson rate) and its
 * intensity (the rate parameters plus the request size distribution).
 */
public final class WorkloadSpec {

    private final WorkloadType type;
    private final double durationSeconds;
    private final Map<String, Double> params;
    private final double meanLengthMi;
    private final double lengthVariation;
    private final int requestPes;
    private final long seed;

    private WorkloadSpec(WorkloadType type, double durationSeconds, Map<String, Double> params,
                         double meanLengthMi, double lengthVariation, int requestPes, long seed) {
        if (!(durationSeconds > 0)) {
            throw new IllegalArgumentException("Workload duration must be > 0");
        }
        params.forEach((k, v) -> {
            if (v < 0 || Double.isNaN(v) || Double.isInfinite(v)) {
                throw new IllegalArgumentException("Workload parameter " + k + " must be finite and >= 0, got " + v);
            }
        });
        this.type = type;
        this.durationSeconds = durationSeconds;
        this.params = Collections.unmodifiableMap(new LinkedHashMap<>(params));
        this.meanLengthMi = meanLengthMi;
        this.lengthVariation = lengthVariation;
        this.requestPes = requestPes;
        this.seed = seed;
        validateShape();
    }

    /** Builds the spec for a workload type from configuration. */
    public static WorkloadSpec fromConfig(WorkloadType type, SimulationConfig cfg) {
        final Map<String, Double> p = new LinkedHashMap<>();
        switch (type) {
            case LOW -> p.put("rate", cfg.getDouble("workload.low.rate"));
            case MEDIUM -> p.put("rate", cfg.getDouble("workload.medium.rate"));
            case HIGH -> p.put("rate", cfg.getDouble("workload.high.rate"));
            case BURST -> {
                p.put("baseRate", cfg.getDouble("workload.burst.baseRate"));
                p.put("peakRate", cfg.getDouble("workload.burst.peakRate"));
                p.put("startSeconds", cfg.getDouble("workload.burst.startSeconds"));
                p.put("durationSeconds", cfg.getDouble("workload.burst.durationSeconds"));
            }
            case INCREASING -> {
                p.put("startRate", cfg.getDouble("workload.increasing.startRate"));
                p.put("endRate", cfg.getDouble("workload.increasing.endRate"));
            }
            case DECREASING -> {
                p.put("startRate", cfg.getDouble("workload.decreasing.startRate"));
                p.put("endRate", cfg.getDouble("workload.decreasing.endRate"));
            }
            case FLUCTUATING -> {
                p.put("baseRate", cfg.getDouble("workload.fluctuating.baseRate"));
                p.put("amplitude", cfg.getDouble("workload.fluctuating.amplitude"));
                p.put("periodSeconds", cfg.getDouble("workload.fluctuating.periodSeconds"));
            }
        }
        return new WorkloadSpec(type, cfg.durationSeconds(), p, cfg.requestMeanLengthMi(),
            cfg.requestLengthVariation(), cfg.requestPes(), deriveSeed(cfg.seed(), type));
    }

    /** Direct construction, mainly for tests. */
    public static WorkloadSpec of(WorkloadType type, double durationSeconds, Map<String, Double> params,
                                  double meanLengthMi, double lengthVariation, int requestPes, long seed) {
        return new WorkloadSpec(type, durationSeconds, params, meanLengthMi, lengthVariation, requestPes, seed);
    }

    /** Seed for one workload: stable for a given master seed and workload type. */
    public static long deriveSeed(long masterSeed, WorkloadType type) {
        long z = masterSeed + 0x9E3779B97F4A7C15L * (type.ordinal() + 1);
        z = (z ^ (z >>> 30)) * 0xBF58476D1CE4E5B9L;
        z = (z ^ (z >>> 27)) * 0x94D049BB133111EBL;
        return z ^ (z >>> 31);
    }

    private void validateShape() {
        switch (type) {
            case BURST -> {
                if (param("startSeconds") >= durationSeconds) {
                    throw new IllegalArgumentException("Burst must start before the workload ends");
                }
                if (param("durationSeconds") <= 0) {
                    throw new IllegalArgumentException("Burst duration must be > 0");
                }
            }
            case FLUCTUATING -> {
                if (param("periodSeconds") <= 0) {
                    throw new IllegalArgumentException("Fluctuation period must be > 0");
                }
            }
            default -> { }
        }
        if (maxRate() <= 0) {
            throw new IllegalArgumentException("Workload " + type + " has a zero arrival rate everywhere");
        }
        if (!(meanLengthMi > 0) || lengthVariation < 0 || lengthVariation >= 1 || requestPes <= 0) {
            throw new IllegalArgumentException("Invalid request size parameters");
        }
    }

    /** Expected arrival rate (requests/second) at simulated time {@code t}. Zero outside [0, duration). */
    public double rateAt(double t) {
        if (t < 0 || t >= durationSeconds) {
            return 0;
        }
        return switch (type) {
            case LOW, MEDIUM, HIGH -> param("rate");
            case BURST -> {
                final double start = param("startSeconds");
                final boolean inBurst = t >= start && t < start + param("durationSeconds");
                yield inBurst ? param("peakRate") : param("baseRate");
            }
            case INCREASING, DECREASING -> {
                final double frac = t / durationSeconds;
                yield param("startRate") + (param("endRate") - param("startRate")) * frac;
            }
            case FLUCTUATING -> Math.max(0, param("baseRate")
                + param("amplitude") * Math.sin(2 * Math.PI * t / param("periodSeconds")));
        };
    }

    /** Upper bound of {@link #rateAt(double)} over the workload, used for Poisson thinning. */
    public double maxRate() {
        return switch (type) {
            case LOW, MEDIUM, HIGH -> param("rate");
            case BURST -> Math.max(param("baseRate"), param("peakRate"));
            case INCREASING, DECREASING -> Math.max(param("startRate"), param("endRate"));
            case FLUCTUATING -> param("baseRate") + param("amplitude");
        };
    }

    /** Human-readable arrival pattern description, recorded with every run. */
    public String arrivalPattern() {
        return switch (type) {
            case LOW, MEDIUM, HIGH -> "homogeneous Poisson, constant rate";
            case BURST -> "Poisson, base rate with a rectangular burst at peak rate";
            case INCREASING -> "non-homogeneous Poisson, rate rises linearly";
            case DECREASING -> "non-homogeneous Poisson, rate falls linearly";
            case FLUCTUATING -> "non-homogeneous Poisson, sinusoidal rate";
        };
    }

    /** Expected number of requests: the integral of the rate over the duration (numerically). */
    public double expectedRequests() {
        final int steps = 10_000;
        final double dt = durationSeconds / steps;
        double sum = 0;
        for (int i = 0; i < steps; i++) {
            sum += rateAt((i + 0.5) * dt) * dt;
        }
        return sum;
    }

    private double param(String name) {
        final Double v = params.get(name);
        if (v == null) {
            throw new IllegalStateException("Workload " + type + " is missing parameter " + name);
        }
        return v;
    }

    public WorkloadType type() { return type; }
    public double durationSeconds() { return durationSeconds; }
    public Map<String, Double> params() { return params; }
    public double meanLengthMi() { return meanLengthMi; }
    public double lengthVariation() { return lengthVariation; }
    public int requestPes() { return requestPes; }
    public long seed() { return seed; }

    /** Compact {@code k=v;k=v} representation of the rate parameters for CSV output. */
    public String paramsString() {
        final StringBuilder sb = new StringBuilder();
        params.forEach((k, v) -> {
            if (!sb.isEmpty()) {
                sb.append(';');
            }
            sb.append(k).append('=').append(v);
        });
        return sb.toString();
    }
}
