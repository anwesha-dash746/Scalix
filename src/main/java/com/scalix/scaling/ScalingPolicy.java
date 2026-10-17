package com.scalix.scaling;

import java.util.Map;

/**
 * Common contract for every VM allocation strategy, so all of them run under identical conditions.
 *
 * <p>Implementations must be deterministic functions of the {@link ScalingContext} (plus their own
 * configuration) and must never return a decision that leaves the provisioned VM count outside
 * {@link #limits()}. The engine re-checks this as a safety net.
 */
public interface ScalingPolicy {

    /** Short identifier used in output files, e.g. {@code STATIC}. */
    String name();

    /** VM-count bounds, including the count the run starts with. */
    VmLimits limits();

    /** Evaluates one decision interval. */
    ScalingDecision decide(ScalingContext context);

    /** The policy's parameters, recorded with every run. */
    Map<String, String> parameters();
}
