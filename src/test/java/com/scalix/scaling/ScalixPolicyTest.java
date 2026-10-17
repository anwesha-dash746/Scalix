package com.scalix.scaling;

import org.junit.jupiter.api.Test;

import static com.scalix.scaling.Contexts.ctx;
import static com.scalix.scaling.Contexts.falling;
import static com.scalix.scaling.Contexts.rising;
import static com.scalix.scaling.Contexts.stable;
import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class ScalixPolicyTest {

    private static final VmLimits LIMITS = new VmLimits(1, 4, 10);

    /** upper .70, lower .30, preemptive .55, target .60, window 6, trend .03, projection 2, cooldowns 30/90, step 3 */
    private static ScalixPolicy policy() {
        return policy(LIMITS);
    }

    private static ScalixPolicy policy(VmLimits limits) {
        return new ScalixPolicy(limits, 0.70, 0.30, 0.55, 0.60, 6, 0.03, 2, 30, 90, 3);
    }

    // ------------------------------------------------------------ scale-up

    @Test
    void scalesUpWhenOverloadedEvenWithStableDemand() {
        final ScalingDecision d = policy().decide(ctx().active(4).util(0.80).rates(stable()).build());
        assertEquals(ScalingAction.SCALE_UP, d.action());
        assertEquals(Trend.STABLE, d.trend());
        // busy = 4 * 0.8 = 3.2 -> ceil(3.2 / 0.6) = 6 -> +2
        assertEquals(2, d.delta());
    }

    @Test
    void scalesUpPreemptivelyWhenDemandRises() {
        final ScalingDecision d = policy().decide(ctx().active(4).util(0.60).rates(rising()).build());
        assertEquals(ScalingAction.SCALE_UP, d.action());
        assertEquals(Trend.RISING, d.trend());
        assertTrue(d.delta() >= 1);
    }

    @Test
    void doesNotScaleUpEarlyWhenDemandIsStableOrFalling() {
        assertEquals(ScalingAction.NONE, policy().decide(ctx().active(4).util(0.60).rates(stable()).build()).action());
        assertEquals(ScalingAction.NONE, policy().decide(ctx().active(4).util(0.60).rates(falling()).build()).action());
    }

    @Test
    void stepIsCappedByMaxStepAndMaximum() {
        // saturated 9 VMs at max 10 -> only 1 allowed
        assertEquals(1, policy().decide(ctx().active(9).util(1.0).build()).delta());
        // saturated 4 VMs would want ceil(4/0.6)=7 (+3), cap is 3
        assertEquals(3, policy().decide(ctx().active(4).util(1.0).build()).delta());
        final ScalixPolicy smallStep = new ScalixPolicy(LIMITS, 0.70, 0.30, 0.55, 0.60, 6, 0.03, 2, 30, 90, 1);
        assertEquals(1, smallStep.decide(ctx().active(4).util(1.0).build()).delta());
    }

    @Test
    void neverExceedsMaximum() {
        final ScalingDecision d = policy().decide(ctx().active(10).util(1.0).rates(rising()).build());
        assertEquals(ScalingAction.NONE, d.action());
        assertTrue(d.reason().contains("max"));
    }

    @Test
    void bootingVmsThatCoverDemandPreventPreemptiveScaleUp() {
        // rising, util 0.56 on 4 active -> busy 2.24 * growth; 4 booting already make 8 >= desired
        final ScalingDecision d = policy().decide(ctx().active(4).booting(4).util(0.56).rates(rising()).build());
        assertEquals(ScalingAction.NONE, d.action());
        assertTrue(d.reason().contains("booting"));
    }

    @Test
    void overloadAlwaysAddsAtLeastOneVm() {
        // Saturation understates demand, so even if booting VMs "cover" it numerically we add one.
        final ScalingDecision d = policy().decide(ctx().active(2).booting(3).util(0.9).build());
        assertEquals(ScalingAction.SCALE_UP, d.action());
        assertEquals(1, d.delta());
    }

    // ------------------------------------------------------------ cooldown

    @Test
    void scaleUpCooldownIsEnforced() {
        final ScalingDecision blocked = policy().decide(ctx().time(100).active(4).util(0.9).lastUp(80).build());
        assertEquals(ScalingAction.NONE, blocked.action());
        assertTrue(blocked.cooldownBlocked());
        final ScalingDecision allowed = policy().decide(ctx().time(110).active(4).util(0.9).lastUp(80).build());
        assertEquals(ScalingAction.SCALE_UP, allowed.action());
        assertFalse(allowed.cooldownBlocked());
    }

    @Test
    void scaleDownCooldownCountsFromAnyAction() {
        // Scaled up 60 s ago: a scale-down must wait 90 s (anti-oscillation).
        final ScalingDecision afterUp = policy().decide(ctx().time(160).active(6).util(0.1).rates(falling()).lastUp(100).build());
        assertEquals(ScalingAction.NONE, afterUp.action());
        assertTrue(afterUp.cooldownBlocked());
        final ScalingDecision afterDown = policy().decide(ctx().time(160).active(6).util(0.1).rates(falling()).lastDown(100).build());
        assertTrue(afterDown.cooldownBlocked());
        final ScalingDecision ok = policy().decide(ctx().time(191).active(6).util(0.1).rates(falling()).lastUp(100).build());
        assertEquals(ScalingAction.SCALE_DOWN, ok.action());
    }

    @Test
    void scaleUpIsNotBlockedByRecentScaleDown() {
        final ScalingDecision d = policy().decide(ctx().time(100).active(4).util(0.95).lastDown(95).build());
        assertEquals(ScalingAction.SCALE_UP, d.action());
    }

    // ------------------------------------------------------------ scale-down

    @Test
    void scalesDownWhenUnderloadedAndDemandFallingOrStable() {
        assertEquals(-1, policy().decide(ctx().active(6).util(0.2).rates(falling()).build()).delta());
        assertEquals(-1, policy().decide(ctx().active(6).util(0.2).rates(stable()).build()).delta());
    }

    @Test
    void holdsWhenUnderloadedButDemandRisingOrUnknown() {
        assertEquals(ScalingAction.NONE, policy().decide(ctx().active(6).util(0.2).rates(rising()).build()).action());
        assertEquals(ScalingAction.NONE, policy().decide(ctx().active(6).util(0.2).rates(3.0).build()).action());
    }

    @Test
    void refusesScaleDownThatWouldOverloadRemainingVms() {
        // 2 active at 0.30 -> one VM would run at 0.60... exactly target is allowed
        assertEquals(ScalingAction.SCALE_DOWN, policy().decide(ctx().active(2).util(0.30).rates(stable()).build()).action());
        // 2 active at 0.30 with target 0.55 -> projected 0.60 > 0.55, refused
        final ScalixPolicy strict = new ScalixPolicy(LIMITS, 0.70, 0.30, 0.55, 0.55, 6, 0.03, 2, 30, 90, 3);
        final ScalingDecision d = strict.decide(ctx().active(2).util(0.30).rates(stable()).build());
        assertEquals(ScalingAction.NONE, d.action());
        assertTrue(d.reason().contains("raise utilization"));
    }

    @Test
    void neverRemovesTheLastVm() {
        final ScalingDecision d = policy().decide(ctx().active(1).util(0.0).rates(falling()).build());
        assertEquals(ScalingAction.NONE, d.action());
        assertTrue(d.reason().contains("min"));
    }

    @Test
    void respectsConfiguredMinimum() {
        final ScalixPolicy p = policy(new VmLimits(3, 4, 10));
        assertEquals(ScalingAction.NONE, p.decide(ctx().active(3).util(0.0).rates(falling()).build()).action());
        assertEquals(ScalingAction.SCALE_DOWN, p.decide(ctx().active(4).util(0.0).rates(falling()).build()).action());
    }

    @Test
    void holdsInsideBand() {
        assertEquals(ScalingAction.NONE, policy().decide(ctx().active(4).util(0.45).rates(stable()).build()).action());
    }

    // ------------------------------------------------------------ validation

    @Test
    void invalidParametersRejected() {
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.3, 0.7, 0.55, 0.6, 6, 0.03, 2, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.80, 0.6, 6, 0.03, 2, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.25, 0.6, 6, 0.03, 2, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.9, 6, 0.03, 2, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.6, 1, 0.03, 2, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.6, 6, -0.1, 2, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.6, 6, 0.03, -1, 30, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.6, 6, 0.03, 2, -5, 90, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.6, 6, 0.03, 2, 30, Double.POSITIVE_INFINITY, 3));
        assertThrows(IllegalArgumentException.class, () -> new ScalixPolicy(LIMITS, 0.7, 0.3, 0.55, 0.6, 6, 0.03, 2, 30, 90, 0));
    }

    @Test
    void contextRejectsInvalidMeasurements() {
        assertThrows(IllegalArgumentException.class, () -> ctx().util(1.5).build());
        assertThrows(IllegalArgumentException.class, () -> ctx().util(-0.1).build());
        assertThrows(IllegalArgumentException.class, () -> ctx().util(Double.NaN).build());
        assertThrows(IllegalArgumentException.class, () -> ctx().time(-1).build());
    }
}
