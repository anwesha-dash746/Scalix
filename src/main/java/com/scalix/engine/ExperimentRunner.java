package com.scalix.engine;

import com.scalix.config.SimulationConfig;
import com.scalix.export.ResultExporter;
import com.scalix.metrics.RunResult;
import com.scalix.scaling.PolicyFactory;
import com.scalix.scaling.ScalingPolicy;
import com.scalix.workload.Request;
import com.scalix.workload.WorkloadGenerator;
import com.scalix.workload.WorkloadSpec;
import com.scalix.workload.WorkloadType;

import java.io.IOException;
import java.nio.file.Path;
import java.time.LocalDateTime;
import java.time.format.DateTimeFormatter;
import java.util.ArrayList;
import java.util.List;
import java.util.function.Consumer;

/**
 * Runs every configured policy against every configured workload. For a given workload the request
 * sequence is generated once and reused for all policies, and VM type, durations, simulation
 * settings and pricing come from the same configuration, so runs differ only in the policy.
 */
public final class ExperimentRunner {

    /** Result of a full experiment. */
    public record Experiment(String experimentId, Path outputDir, List<RunResult> runs) { }

    private final SimulationConfig cfg;
    private final Consumer<String> log;

    public ExperimentRunner(SimulationConfig cfg, Consumer<String> log) {
        this.cfg = cfg;
        this.log = log;
    }

    public static String newExperimentId() {
        return "exp-" + LocalDateTime.now().format(DateTimeFormatter.ofPattern("yyyyMMdd-HHmmss"));
    }

    public Experiment run(String experimentId) throws IOException {
        // Validate all names before spending time simulating.
        final List<WorkloadType> workloads = new ArrayList<>();
        for (String w : cfg.workloads()) {
            workloads.add(WorkloadType.parse(w));
        }
        for (String p : cfg.policies()) {
            PolicyFactory.create(p, cfg);
        }

        final List<RunResult> runs = new ArrayList<>();
        for (WorkloadType type : workloads) {
            final WorkloadSpec spec = WorkloadSpec.fromConfig(type, cfg);
            final List<Request> requests = WorkloadGenerator.generate(spec);
            log.accept(String.format("Workload %-11s seed=%d requests=%d (%s)", type, spec.seed(), requests.size(),
                spec.paramsString()));
            for (String policyName : cfg.policies()) {
                final ScalingPolicy policy = PolicyFactory.create(policyName, cfg);
                final String runId = experimentId + "-" + type + "-" + policy.name();
                final RunResult r = ScalixSimulation.run(runId, cfg, policy, spec, requests);
                runs.add(r);
                log.accept(String.format("  %-9s done: sim end %.0fs, %d decisions, %d ms%s", policy.name(),
                    r.simEndTime(), r.decisions().size(), r.wallClockMillis(),
                    r.drainTimedOut() ? "  [DRAIN TIMED OUT: unfinished requests]" : ""));
            }
        }

        final Path out = Path.of(cfg.outputDir()).resolve(experimentId);
        ResultExporter.export(out, experimentId, cfg, runs);
        return new Experiment(experimentId, out, runs);
    }
}
