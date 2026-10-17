package com.scalix.scaling;

import com.scalix.config.SimulationConfig;
import org.junit.jupiter.api.Test;

import static com.scalix.scaling.Contexts.ctx;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertInstanceOf;
import static org.junit.jupiter.api.Assertions.assertThrows;

class StaticPolicyAndLimitsTest {

    @Test
    void staticPolicyNeverActs() {
        final StaticPolicy p = new StaticPolicy(5);
        assertEquals(new VmLimits(5, 5, 5), p.limits());
        assertEquals(ScalingAction.NONE, p.decide(ctx().active(5).util(1.0).build()).action());
        assertEquals(ScalingAction.NONE, p.decide(ctx().active(5).util(0.0).build()).action());
    }

    @Test
    void limitsRejectUnsafeValues() {
        assertThrows(IllegalArgumentException.class, () -> new VmLimits(0, 1, 5));
        assertThrows(IllegalArgumentException.class, () -> new VmLimits(3, 3, 2));
        assertThrows(IllegalArgumentException.class, () -> new VmLimits(2, 1, 5));
        assertThrows(IllegalArgumentException.class, () -> new VmLimits(2, 6, 5));
        assertThrows(IllegalArgumentException.class, () -> new StaticPolicy(0));
    }

    @Test
    void limitsClamp() {
        final VmLimits l = new VmLimits(2, 3, 6);
        assertEquals(2, l.clamp(-4));
        assertEquals(6, l.clamp(99));
        assertEquals(4, l.clamp(4));
    }

    @Test
    void decisionRejectsInconsistentDelta() {
        assertThrows(IllegalArgumentException.class, () -> new ScalingDecision(ScalingAction.SCALE_UP, 0, "", Trend.STABLE, 0, false));
        assertThrows(IllegalArgumentException.class, () -> new ScalingDecision(ScalingAction.SCALE_DOWN, 1, "", Trend.STABLE, 0, false));
        assertThrows(IllegalArgumentException.class, () -> new ScalingDecision(ScalingAction.NONE, 1, "", Trend.STABLE, 0, false));
    }

    @Test
    void factoryBuildsConfiguredPolicies() {
        final SimulationConfig cfg = SimulationConfig.defaults();
        assertInstanceOf(StaticPolicy.class, PolicyFactory.create("static", cfg));
        assertInstanceOf(ThresholdPolicy.class, PolicyFactory.create("THRESHOLD", cfg));
        assertInstanceOf(ScalixPolicy.class, PolicyFactory.create(" scalix ", cfg));
        assertThrows(IllegalArgumentException.class, () -> PolicyFactory.create("magic", cfg));
    }

    @Test
    void factoryPropagatesInvalidPolicyConfig() {
        final SimulationConfig bad = SimulationConfig.defaults().with("policy.threshold.lower", "0.9");
        assertThrows(IllegalArgumentException.class, () -> PolicyFactory.create("THRESHOLD", bad));
        final SimulationConfig badMin = SimulationConfig.defaults().with("policy.scaling.minVms", "0");
        assertThrows(IllegalArgumentException.class, () -> PolicyFactory.create("SCALIX", badMin));
    }
}
