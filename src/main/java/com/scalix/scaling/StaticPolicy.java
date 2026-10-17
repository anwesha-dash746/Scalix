package com.scalix.scaling;

import java.util.LinkedHashMap;
import java.util.Map;

/** Policy A: a fixed number of VMs for the whole run. */
public final class StaticPolicy implements ScalingPolicy {

    public static final String NAME = "STATIC";

    private final VmLimits limits;

    public StaticPolicy(int vmCount) {
        this.limits = new VmLimits(vmCount, vmCount, vmCount);
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
    public ScalingDecision decide(ScalingContext context) {
        return ScalingDecision.none("static allocation: fleet size is fixed at " + limits.initial());
    }

    @Override
    public Map<String, String> parameters() {
        final Map<String, String> m = new LinkedHashMap<>();
        m.put("vmCount", Integer.toString(limits.initial()));
        return m;
    }
}
