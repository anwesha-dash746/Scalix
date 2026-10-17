package com.scalix.scaling;

import org.junit.jupiter.api.Test;

import static com.scalix.scaling.Contexts.ctx;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ThresholdPolicyTest {

    private final ThresholdPolicy policy = new ThresholdPolicy(new VmLimits(1, 2, 6), 0.70, 0.30, 0);

    @Test
    void scalesUpAboveUpperThreshold() {
        final ScalingDecision d = policy.decide(ctx().active(3).util(0.85).build());
        assertEquals(ScalingAction.SCALE_UP, d.action());
        assertEquals(1, d.delta());
    }

    @Test
    void scalesDownBelowLowerThreshold() {
        final ScalingDecision d = policy.decide(ctx().active(3).util(0.10).build());
        assertEquals(ScalingAction.SCALE_DOWN, d.action());
        assertEquals(-1, d.delta());
    }

    @Test
    void holdsInsideBandAndAtExactThresholds() {
        assertEquals(ScalingAction.NONE, policy.decide(ctx().active(3).util(0.50).build()).action());
        assertEquals(ScalingAction.NONE, policy.decide(ctx().active(3).util(0.70).build()).action());
        assertEquals(ScalingAction.NONE, policy.decide(ctx().active(3).util(0.30).build()).action());
    }

    @Test
    void respectsMaximum() {
        final ScalingDecision d = policy.decide(ctx().active(6).util(1.0).build());
        assertEquals(ScalingAction.NONE, d.action());
        assertTrue(d.reason().contains("max"));
    }

    @Test
    void respectsMinimumAndNeverRemovesLastVm() {
        final ScalingDecision d = policy.decide(ctx().active(1).util(0.0).build());
        assertEquals(ScalingAction.NONE, d.action());
        assertTrue(d.reason().contains("min"));
    }

    @Test
    void bootingVmsCountTowardsMaximum() {
        assertEquals(ScalingAction.NONE, policy.decide(ctx().active(4).booting(2).util(0.95).build()).action());
    }

    @Test
    void optionalCooldownBlocksBothDirections() {
        final ThresholdPolicy p = new ThresholdPolicy(new VmLimits(1, 2, 6), 0.70, 0.30, 60);
        final ScalingDecision up = p.decide(ctx().time(100).active(3).util(0.9).lastDown(60).build());
        assertEquals(ScalingAction.NONE, up.action());
        assertTrue(up.cooldownBlocked());
        final ScalingDecision down = p.decide(ctx().time(100).active(3).util(0.1).lastUp(50).build());
        assertTrue(down.cooldownBlocked());
        assertEquals(ScalingAction.SCALE_UP, p.decide(ctx().time(121).active(3).util(0.9).lastUp(60).build()).action());
    }

    @Test
    void invalidParametersRejected() {
        final VmLimits l = new VmLimits(1, 2, 6);
        assertThrows(IllegalArgumentException.class, () -> new ThresholdPolicy(l, 0.3, 0.7, 0));
        assertThrows(IllegalArgumentException.class, () -> new ThresholdPolicy(l, 0.5, 0.5, 0));
        assertThrows(IllegalArgumentException.class, () -> new ThresholdPolicy(l, 1.2, 0.3, 0));
        assertThrows(IllegalArgumentException.class, () -> new ThresholdPolicy(l, 0.7, -0.1, 0));
        assertThrows(IllegalArgumentException.class, () -> new ThresholdPolicy(l, 0.7, 0.3, -1));
        assertThrows(IllegalArgumentException.class, () -> new ThresholdPolicy(l, Double.NaN, 0.3, 0));
    }
}
