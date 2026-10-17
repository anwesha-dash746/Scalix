package com.scalix.config;

import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class SimulationConfigTest {

    @Test
    void defaultsLoadAndValidate() {
        final SimulationConfig c = SimulationConfig.defaults();
        assertEquals(42, c.seed());
        assertEquals(List.of("LOW", "MEDIUM", "HIGH", "BURST", "INCREASING", "DECREASING", "FLUCTUATING"), c.workloads());
        assertEquals(List.of("STATIC", "THRESHOLD", "SCALIX"), c.policies());
        assertTrue(c.decisionIntervalSeconds() >= c.tickSeconds());
    }

    @Test
    void overridesApplyAndListsAreNormalised() {
        final SimulationConfig c = SimulationConfig.builder()
            .overrides("simulation.seed=7", "experiment.policies= static , scalix ").build();
        assertEquals(7, c.seed());
        assertEquals(List.of("STATIC", "SCALIX"), c.policies());
    }

    @Test
    void fileOverridesApply(@TempDir Path dir) throws IOException {
        final Path f = dir.resolve("exp.properties");
        Files.writeString(f, "simulation.durationSeconds=120\nvm.bootSeconds=10\n");
        final SimulationConfig c = SimulationConfig.builder().file(f).build();
        assertEquals(120, c.durationSeconds());
        assertEquals(10, c.vmBootSeconds());
    }

    @Test
    void unknownKeysAreRejected() {
        assertThrows(IllegalArgumentException.class, () -> SimulationConfig.builder().overrides("simulation.sed=1"));
        assertThrows(IllegalArgumentException.class, () -> SimulationConfig.builder().overrides("novalue"));
    }

    @Test
    void invalidValuesAreRejected() {
        bad("simulation.durationSeconds=0");
        bad("simulation.durationSeconds=-5");
        bad("simulation.tickSeconds=abc");
        bad("simulation.decisionIntervalSeconds=0.5");   // shorter than the 1 s tick
        bad("simulation.seed=1.5");
        bad("vm.pes=16");                                // larger than a host
        bad("vm.bootSeconds=-1");
        bad("request.lengthVariation=1");
        bad("request.pes=3");                            // more than vm.pes
        bad("pricing.vmPricePerHour=-0.1");
        bad("pricing.billingGranularitySeconds=0");
        bad("experiment.workloads= , ");
        bad("export.requests=maybe");
        bad("simulation.durationSeconds=NaN");
        bad("simulation.durationSeconds=Infinity");
    }

    @Test
    void datacenterMustFitTheLargestFleet() {
        // 10 hosts x (8/2) = 40 VMs fit; ask for 41.
        bad("policy.scaling.maxVms=41");
        bad("policy.static.vmCount=41");
    }

    private static void bad(String kv) {
        assertThrows(IllegalArgumentException.class, () -> SimulationConfig.builder().overrides(kv).build(), kv);
    }
}
