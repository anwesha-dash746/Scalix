package com.scalix.workload;

import com.scalix.config.SimulationConfig;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.EnumSource;

import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertNotEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class WorkloadGeneratorTest {

    private static final SimulationConfig CFG = SimulationConfig.defaults();

    @ParameterizedTest
    @EnumSource(WorkloadType.class)
    void everyWorkloadIsGeneratedWithinDurationAndSorted(WorkloadType type) {
        final WorkloadSpec spec = WorkloadSpec.fromConfig(type, CFG);
        final List<Request> reqs = WorkloadGenerator.generate(spec);
        assertTrue(!reqs.isEmpty());
        double prev = -1;
        final double minLen = spec.meanLengthMi() * (1 - spec.lengthVariation());
        final double maxLen = spec.meanLengthMi() * (1 + spec.lengthVariation());
        for (int i = 0; i < reqs.size(); i++) {
            final Request r = reqs.get(i);
            assertEquals(i, r.id());
            assertTrue(r.arrivalTime() >= prev && r.arrivalTime() < spec.durationSeconds());
            assertTrue(r.lengthMi() >= Math.floor(minLen) && r.lengthMi() <= Math.ceil(maxLen));
            prev = r.arrivalTime();
        }
        // Poisson count: within 5 standard deviations of the expectation.
        final double expected = spec.expectedRequests();
        assertTrue(Math.abs(reqs.size() - expected) < 5 * Math.sqrt(expected),
            type + ": got " + reqs.size() + ", expected ~" + expected);
    }

    @ParameterizedTest
    @EnumSource(WorkloadType.class)
    void sameSeedReproducesExactly(WorkloadType type) {
        final WorkloadSpec spec = WorkloadSpec.fromConfig(type, CFG);
        assertEquals(WorkloadGenerator.generate(spec), WorkloadGenerator.generate(spec));
    }

    @Test
    void differentSeedsDiffer() {
        final WorkloadSpec a = WorkloadSpec.fromConfig(WorkloadType.MEDIUM, CFG);
        final WorkloadSpec b = WorkloadSpec.fromConfig(WorkloadType.MEDIUM, CFG.with("simulation.seed", "43"));
        assertNotEquals(a.seed(), b.seed());
        assertNotEquals(WorkloadGenerator.generate(a), WorkloadGenerator.generate(b));
    }

    @Test
    void intensitiesAreOrdered() {
        final int low = WorkloadGenerator.generate(WorkloadSpec.fromConfig(WorkloadType.LOW, CFG)).size();
        final int med = WorkloadGenerator.generate(WorkloadSpec.fromConfig(WorkloadType.MEDIUM, CFG)).size();
        final int high = WorkloadGenerator.generate(WorkloadSpec.fromConfig(WorkloadType.HIGH, CFG)).size();
        assertTrue(low < med && med < high);
    }

    @Test
    void burstConcentratesArrivals() {
        final WorkloadSpec spec = WorkloadSpec.fromConfig(WorkloadType.BURST, CFG);
        final List<Request> reqs = WorkloadGenerator.generate(spec);
        // default burst: [300, 420) at 16/s vs 4/s base
        final double inBurstRate = count(reqs, 300, 420) / 120.0;
        final double beforeRate = count(reqs, 0, 300) / 300.0;
        assertTrue(inBurstRate > 2.5 * beforeRate, "burst " + inBurstRate + " vs base " + beforeRate);
    }

    @Test
    void rampsMoveInTheConfiguredDirection() {
        final List<Request> inc = WorkloadGenerator.generate(WorkloadSpec.fromConfig(WorkloadType.INCREASING, CFG));
        final List<Request> dec = WorkloadGenerator.generate(WorkloadSpec.fromConfig(WorkloadType.DECREASING, CFG));
        assertTrue(count(inc, 0, 300) < count(inc, 300, 600) && count(inc, 300, 600) < count(inc, 600, 900));
        assertTrue(count(dec, 0, 300) > count(dec, 300, 600) && count(dec, 300, 600) > count(dec, 600, 900));
    }

    @Test
    void fluctuatingFollowsTheSineWave() {
        final WorkloadSpec spec = WorkloadSpec.fromConfig(WorkloadType.FLUCTUATING, CFG);
        assertEquals(10.0, spec.rateAt(60), 1e-9);   // peak at period/4
        assertEquals(2.0, spec.rateAt(180), 1e-9);   // trough at 3*period/4
        final List<Request> reqs = WorkloadGenerator.generate(spec);
        assertTrue(count(reqs, 30, 90) > 2 * count(reqs, 150, 210));
    }

    @Test
    void rateIsZeroOutsideDuration() {
        final WorkloadSpec spec = WorkloadSpec.fromConfig(WorkloadType.HIGH, CFG);
        assertEquals(0, spec.rateAt(-1));
        assertEquals(0, spec.rateAt(spec.durationSeconds()));
    }

    @Test
    void invalidWorkloadsRejected() {
        assertThrows(IllegalArgumentException.class, () -> WorkloadType.parse("spiky"));
        assertThrows(IllegalArgumentException.class,
            () -> WorkloadSpec.of(WorkloadType.LOW, 100, Map.of("rate", 0.0), 1000, 0.5, 1, 1));
        assertThrows(IllegalArgumentException.class,
            () -> WorkloadSpec.of(WorkloadType.LOW, 100, Map.of("rate", -1.0), 1000, 0.5, 1, 1));
        assertThrows(IllegalArgumentException.class,
            () -> WorkloadSpec.of(WorkloadType.LOW, 0, Map.of("rate", 1.0), 1000, 0.5, 1, 1));
        assertThrows(IllegalArgumentException.class,
            () -> WorkloadSpec.of(WorkloadType.BURST, 100,
                Map.of("baseRate", 1.0, "peakRate", 5.0, "startSeconds", 150.0, "durationSeconds", 10.0), 1000, 0.5, 1, 1));
        assertThrows(IllegalStateException.class,
            () -> WorkloadSpec.of(WorkloadType.INCREASING, 100, Map.of("startRate", 1.0), 1000, 0.5, 1, 1));
        assertThrows(IllegalArgumentException.class, () -> new Request(0, -1, 10, 1));
        assertThrows(IllegalArgumentException.class, () -> new Request(0, 1, 0, 1));
    }

    private static long count(List<Request> reqs, double from, double to) {
        return reqs.stream().filter(r -> r.arrivalTime() >= from && r.arrivalTime() < to).count();
    }
}
