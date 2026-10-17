package com.scalix;

import com.scalix.config.SimulationConfig;
import com.scalix.engine.ExperimentRunner;
import com.scalix.metrics.CostModel;
import com.scalix.metrics.RunResult;
import com.scalix.metrics.RunSummary;

import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.Locale;

/**
 * Command-line entry point.
 *
 * <pre>
 * java -jar target/scalix-1.0.0-SNAPSHOT-all.jar [options]
 *   --config FILE          properties file overriding the defaults
 *   --set KEY=VALUE        override one key (repeatable; applied after --config)
 *   --workloads A,B,...    shortcut for experiment.workloads
 *   --policies X,Y,...     shortcut for experiment.policies
 *   --seed N               shortcut for simulation.seed
 *   --out DIR              shortcut for export.outputDir
 *   --experiment-id ID     name of the output sub-directory (default: exp-yyyyMMdd-HHmmss)
 *   --help
 * </pre>
 */
public final class SimulationMain {

    private SimulationMain() { }

    public static void main(String[] args) {
        try {
            System.exit(run(args));
        } catch (IllegalArgumentException e) {
            System.err.println("Configuration error: " + e.getMessage());
            System.exit(2);
        } catch (Exception e) {
            System.err.println("Simulation failed: " + e);
            e.printStackTrace(System.err);
            System.exit(1);
        }
    }

    static int run(String[] args) throws Exception {
        final SimulationConfig.Builder builder = SimulationConfig.builder();
        final List<String> overrides = new ArrayList<>();
        String experimentId = null;
        Path configFile = null;

        for (int i = 0; i < args.length; i++) {
            final String a = args[i];
            switch (a) {
                case "--help", "-h" -> {
                    printUsage();
                    return 0;
                }
                case "--config" -> configFile = Path.of(value(args, ++i, a));
                case "--set" -> overrides.add(value(args, ++i, a));
                case "--workloads" -> overrides.add("experiment.workloads=" + value(args, ++i, a));
                case "--policies" -> overrides.add("experiment.policies=" + value(args, ++i, a));
                case "--seed" -> overrides.add("simulation.seed=" + value(args, ++i, a));
                case "--out" -> overrides.add("export.outputDir=" + value(args, ++i, a));
                case "--experiment-id" -> experimentId = value(args, ++i, a);
                default -> throw new IllegalArgumentException("Unknown argument: " + a + " (use --help)");
            }
        }
        if (configFile != null) {
            builder.file(configFile);
        }
        builder.overrides(overrides.toArray(String[]::new));
        final SimulationConfig cfg = builder.build();
        if (experimentId == null) {
            experimentId = ExperimentRunner.newExperimentId();
        }
        if (!experimentId.matches("[A-Za-z0-9._-]+")) {
            throw new IllegalArgumentException("--experiment-id may only contain letters, digits, '.', '_' and '-'");
        }

        System.out.println("SCALIX Part 1 simulation (CloudSim Plus 8.5.7)");
        System.out.println("Experiment " + experimentId + ": workloads=" + cfg.workloads() + " policies=" + cfg.policies()
            + " seed=" + cfg.seed());
        final ExperimentRunner.Experiment exp = new ExperimentRunner(cfg, System.out::println).run(experimentId);

        printTable(exp.runs(), CostModel.fromConfig(cfg));
        System.out.println();
        System.out.println("Results written to " + exp.outputDir().toAbsolutePath());
        final long timedOut = exp.runs().stream().filter(RunResult::drainTimedOut).count();
        if (timedOut > 0) {
            System.out.println("WARNING: " + timedOut + " run(s) hit simulation.maxDrainSeconds with unfinished requests;"
                + " their response-time metrics cover completed requests only.");
        }
        return 0;
    }

    private static void printTable(List<RunResult> runs, CostModel cost) {
        System.out.println();
        System.out.println(String.format(Locale.ROOT, "%-11s %-9s %8s %8s %8s %7s %7s %8s %4s %4s %6s",
            "workload", "policy", "avgRT(s)", "p95RT(s)", "cpuUtil", "avgVMs", "peakVM", "cost($)", "up", "down", "unfin"));
        for (RunResult r : runs) {
            final RunSummary s = RunSummary.of(r, cost);
            System.out.println(String.format(Locale.ROOT, "%-11s %-9s %8.2f %8.2f %8.3f %7.2f %7d %8.4f %4d %4d %6d",
                r.workload().type(), r.policyName(), s.avgResponseTime(), s.p95ResponseTime(), s.cpuUtilization(),
                s.avgBillableVms(), s.peakBillableVms(), s.totalCost(), s.scaleUpActions(), s.scaleDownActions(),
                s.requestsUnfinished()));
        }
    }

    private static String value(String[] args, int i, String flag) {
        if (i >= args.length) {
            throw new IllegalArgumentException(flag + " needs a value");
        }
        return args[i];
    }

    private static void printUsage() {
        System.out.println("""
            Usage: java -jar target/scalix-1.0.0-SNAPSHOT-all.jar [options]
              --config FILE          properties file overriding the defaults
              --set KEY=VALUE        override one key (repeatable; applied after --config)
              --workloads A,B,...    LOW,MEDIUM,HIGH,BURST,INCREASING,DECREASING,FLUCTUATING
              --policies X,Y,...     STATIC,THRESHOLD,SCALIX
              --seed N               master random seed
              --out DIR              output root directory (default: results)
              --experiment-id ID     output sub-directory name (default: exp-yyyyMMdd-HHmmss)
            All keys and defaults: src/main/resources/scalix-default.properties""");
    }
}
