package com.scalix.scaling;

import com.scalix.config.SimulationConfig;

import java.util.Locale;

/** Builds policies from configuration so every run uses exactly the configured parameters. */
public final class PolicyFactory {

    private PolicyFactory() { }

    public static ScalingPolicy create(String name, SimulationConfig cfg) {
        return switch (name.trim().toUpperCase(Locale.ROOT)) {
            case StaticPolicy.NAME -> new StaticPolicy(cfg.getInt("policy.static.vmCount"));
            case ThresholdPolicy.NAME -> new ThresholdPolicy(
                scalingLimits(cfg),
                cfg.getDouble("policy.threshold.upper"),
                cfg.getDouble("policy.threshold.lower"),
                cfg.getDouble("policy.threshold.cooldownSeconds"));
            case ScalixPolicy.NAME -> new ScalixPolicy(
                scalingLimits(cfg),
                cfg.getDouble("policy.scalix.upper"),
                cfg.getDouble("policy.scalix.lower"),
                cfg.getDouble("policy.scalix.preemptiveUpper"),
                cfg.getDouble("policy.scalix.targetUtilization"),
                cfg.getInt("policy.scalix.observationWindow"),
                cfg.getDouble("policy.scalix.trendThreshold"),
                cfg.getInt("policy.scalix.projectionIntervals"),
                cfg.getDouble("policy.scalix.scaleUpCooldownSeconds"),
                cfg.getDouble("policy.scalix.scaleDownCooldownSeconds"),
                cfg.getInt("policy.scalix.maxScaleUpStep"));
            default -> throw new IllegalArgumentException("Unknown policy '" + name
                + "'. Valid: " + StaticPolicy.NAME + ", " + ThresholdPolicy.NAME + ", " + ScalixPolicy.NAME);
        };
    }

    private static VmLimits scalingLimits(SimulationConfig cfg) {
        return new VmLimits(
            cfg.getInt("policy.scaling.minVms"),
            cfg.getInt("policy.scaling.initialVms"),
            cfg.getInt("policy.scaling.maxVms"));
    }
}
