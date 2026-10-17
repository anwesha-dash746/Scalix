package com.scalix.engine;

import com.scalix.config.SimulationConfig;
import com.scalix.metrics.CostModel;
import com.scalix.metrics.DecisionRecord;
import com.scalix.metrics.RequestRecord;
import com.scalix.metrics.RunResult;
import com.scalix.metrics.RunSummary;
import com.scalix.metrics.TimeSeriesPoint;
import com.scalix.metrics.VmRecord;
import com.scalix.scaling.PolicyFactory;
import com.scalix.scaling.ScalingPolicy;
import com.scalix.workload.Request;
import com.scalix.workload.WorkloadGenerator;
import com.scalix.workload.WorkloadSpec;
import com.scalix.workload.WorkloadType;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.api.Test;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Runs the real CloudSim Plus engine on shortened workloads and checks invariants of the output. */
class SimulationIntegrationTest {

    /** Short runs so the suite stays fast; burst/fluctuation shapes are scaled to fit 180 s. */
    static SimulationConfig shortConfig() {
        return SimulationConfig.builder().overrides(
            "simulation.durationSeconds=180",
            "simulation.maxDrainSeconds=1200",
            "workload.burst.startSeconds=60",
            "workload.burst.durationSeconds=40",
            "workload.fluctuating.periodSeconds=90",
            "vm.bootSeconds=20").build();
    }

    static Stream<Arguments> allCombinations() {
        return Stream.of(WorkloadType.values()).flatMap(w ->
            Stream.of("STATIC", "THRESHOLD", "SCALIX").map(p -> Arguments.of(w, p)));
    }

    static RunResult run(SimulationConfig cfg, WorkloadType w, String policyName) {
        final WorkloadSpec spec = WorkloadSpec.fromConfig(w, cfg);
        final List<Request> reqs = WorkloadGenerator.generate(spec);
        final ScalingPolicy policy = PolicyFactory.create(policyName, cfg);
        return ScalixSimulation.run("it-" + w + "-" + policyName, cfg, policy, spec, reqs);
    }

    @ParameterizedTest(name = "{0} x {1}")
    @MethodSource("allCombinations")
    void everyWorkloadAndPolicyCompletesWithConsistentOutput(WorkloadType w, String policyName) {
        final SimulationConfig cfg = shortConfig();
        final ScalingPolicy policy = PolicyFactory.create(policyName, cfg);
        final RunResult r = run(cfg, w, policyName);

        assertFalse(r.drainTimedOut(), "run should finish all requests within the drain limit");
        assertTrue(r.simEndTime() >= cfg.durationSeconds() - cfg.tickSeconds());
        assertFalse(r.requests().isEmpty());

        final Map<Long, VmRecord> vms = new HashMap<>();
        r.vms().forEach(v -> vms.put(v.vmId(), v));

        for (RequestRecord q : r.requests()) {
            assertTrue(q.completed(), "request " + q.requestId() + " unfinished");
            assertTrue(q.dispatchTime() + 1e-9 >= q.arrivalTime(), "dispatched before arrival");
            assertTrue(q.startTime() + 1e-6 >= q.arrivalTime(), "started before arrival");
            assertTrue(q.finishTime() > q.startTime(), "non-positive service time");
            final VmRecord vm = vms.get(q.vmId());
            assertTrue(vm != null, "served by unknown VM");
            assertTrue(q.dispatchTime() + 1e-9 >= vm.activatedTime(), "dispatched to VM before it was active");
            assertTrue(q.finishTime() <= vm.terminationTime() + cfg.tickSeconds() + 1e-6, "finished after VM terminated");
        }

        for (TimeSeriesPoint p : r.timeSeries()) {
            assertTrue(p.provisionedVms() >= policy.limits().min() || p.time() == 0);
            assertTrue(p.provisionedVms() <= policy.limits().max());
            assertTrue(Double.isNaN(p.utilization()) || (p.utilization() >= 0 && p.utilization() <= 1));
        }
        for (DecisionRecord d : r.decisions()) {
            assertTrue(d.vmsAfter() >= policy.limits().min() && d.vmsAfter() <= policy.limits().max());
            assertEquals(d.vmsBefore() + d.appliedDelta(), d.vmsAfter());
            assertFalse(d.reason().isBlank());
        }
        final long expectedDecisions = (long) Math.floor(r.simEndTime() / cfg.decisionIntervalSeconds() + 1e-9);
        assertEquals(expectedDecisions, r.decisions().size());

        for (VmRecord v : r.vms()) {
            assertFalse(Double.isNaN(v.terminationTime()), "every VM lifetime is closed");
            assertTrue(v.lifetimeSeconds() >= 0);
        }

        final RunSummary s = RunSummary.of(r, CostModel.fromConfig(cfg));
        assertEquals(0, s.requestsUnfinished());
        assertTrue(s.p95ResponseTime() >= s.p50ResponseTime());
        assertTrue(s.avgResponseTime() >= s.avgServiceTime() - 1e-9);
        assertTrue(s.cpuUtilization() > 0 && s.cpuUtilization() <= 1.0 + 1e-9);
        assertTrue(s.totalCost() > 0);
        assertEquals(s.scaleUpActions() + s.scaleDownActions(), s.totalScalingActions());

        if (policyName.equals("STATIC")) {
            assertEquals(0, s.totalScalingActions());
            assertEquals(policy.limits().initial(), r.vms().size());
        }
    }

    @Test
    void runsAreDeterministic() {
        final SimulationConfig cfg = shortConfig();
        final CostModel cost = CostModel.fromConfig(cfg);
        final RunResult a = run(cfg, WorkloadType.FLUCTUATING, "SCALIX");
        final RunResult b = run(cfg, WorkloadType.FLUCTUATING, "SCALIX");
        assertEquals(RunSummary.of(a, cost), RunSummary.of(b, cost));
        assertEquals(a.decisions(), b.decisions());
    }

    @Test
    void measuredUtilizationMatchesOfferedLoadForStaticFleet() {
        // LOW: 2 req/s * 2 s mean service on 1 PE = 4 busy PEs out of 8 VMs * 2 PEs = 0.25.
        final SimulationConfig cfg = shortConfig();
        final RunSummary s = RunSummary.of(run(cfg, WorkloadType.LOW, "STATIC"), CostModel.fromConfig(cfg));
        assertEquals(0.25, s.cpuUtilization(), 0.05);
        assertEquals(s.cpuUtilization(), s.meanActiveVmUtilization(), 0.03);
        assertEquals(2.0, s.avgServiceTime(), 0.15);
    }

    @Test
    void scalersReactToColdStartAndStaticDoesNot() {
        final SimulationConfig cfg = shortConfig().with(Map.of("policy.scaling.initialVms", "2"));
        final CostModel cost = CostModel.fromConfig(cfg);
        final RunSummary threshold = RunSummary.of(run(cfg, WorkloadType.HIGH, "THRESHOLD"), cost);
        final RunSummary scalix = RunSummary.of(run(cfg, WorkloadType.HIGH, "SCALIX"), cost);
        assertTrue(threshold.scaleUpActions() > 0);
        assertTrue(scalix.scaleUpActions() > 0);
        assertTrue(threshold.peakBillableVms() > 2 && scalix.peakBillableVms() > 2);
    }

    @Test
    void scaleDownDrainsAndTerminatesVms() {
        // Over-provisioned start on a low workload must shed VMs, each fully drained.
        final SimulationConfig cfg = shortConfig().with(Map.of("policy.scaling.initialVms", "12"));
        final RunResult r = run(cfg, WorkloadType.LOW, "THRESHOLD");
        final RunSummary s = RunSummary.of(r, CostModel.fromConfig(cfg));
        assertTrue(s.scaleDownActions() > 0);
        final long drainedEarly = r.vms().stream()
            .filter(v -> !Double.isNaN(v.drainStartTime()) && v.terminationTime() < r.simEndTime()).count();
        assertTrue(drainedEarly > 0);
        assertTrue(r.timeSeries().stream().allMatch(p -> p.provisionedVms() >= 1));
    }
}
