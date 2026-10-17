package com.scalix.engine;

import com.scalix.config.SimulationConfig;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.io.TempDir;

import java.io.IOException;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.Map;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertFalse;
import static org.junit.jupiter.api.Assertions.assertTrue;

/** Runs a small experiment end to end and validates the CSV files it produces. */
class ExperimentOutputIntegrationTest {

    @Test
    void experimentWritesValidCsvFiles(@TempDir Path out) throws IOException {
        final SimulationConfig cfg = SimulationIntegrationTest.shortConfig().with(Map.of(
            "experiment.workloads", "LOW,BURST",
            "experiment.policies", "STATIC,THRESHOLD,SCALIX",
            "export.outputDir", out.toString(),
            "export.requests", "true"));
        final ExperimentRunner.Experiment exp = new ExperimentRunner(cfg, s -> { }).run("it-exp");
        final Path dir = out.resolve("it-exp");
        assertEquals(dir, exp.outputDir());

        for (String f : List.of("summary.csv", "timeseries.csv", "scaling_events.csv", "workloads.csv", "vms.csv",
            "metric_definitions.csv", "config_used.properties", "requests.csv")) {
            assertTrue(Files.size(dir.resolve(f)) > 0, f + " missing or empty");
        }

        final List<List<String>> summary = read(dir.resolve("summary.csv"));
        final List<String> header = summary.get(0);
        assertEquals(1 + 6, summary.size(), "one row per workload x policy");
        for (String required : List.of("run_id", "workload_type", "policy", "avg_response_time_s",
            "p95_response_time_s", "cpu_utilization", "total_cost_usd", "scale_up_actions", "scale_down_actions",
            "total_scaling_actions", "policy_params", "workload_params")) {
            assertTrue(header.contains(required), "summary lacks " + required);
        }
        for (List<String> row : summary.subList(1, summary.size())) {
            assertEquals(header.size(), row.size());
            assertTrue(row.get(header.indexOf("run_id")).startsWith("it-exp-"));
            final double avg = Double.parseDouble(row.get(header.indexOf("avg_response_time_s")));
            final double p95 = Double.parseDouble(row.get(header.indexOf("p95_response_time_s")));
            final double cost = Double.parseDouble(row.get(header.indexOf("total_cost_usd")));
            assertTrue(avg > 0 && p95 >= avg * 0.5 && cost > 0);
            assertEquals("0", row.get(header.indexOf("requests_unfinished")));
        }

        // Every summary column is classified in metric_definitions.csv.
        final List<List<String>> defs = read(dir.resolve("metric_definitions.csv"));
        assertEquals(header.size(), defs.size() - 1);
        assertTrue(defs.stream().skip(1).allMatch(r -> List.of("ID", "CONFIG", "MEASURED", "CALCULATED").contains(r.get(1))));

        final List<List<String>> events = read(dir.resolve("scaling_events.csv"));
        final int expectedDecisions = exp.runs().stream().mapToInt(r -> r.decisions().size()).sum();
        assertEquals(expectedDecisions, events.size() - 1);
        assertTrue(events.stream().skip(1).noneMatch(r -> r.get(r.size() - 1).isBlank()), "every decision has a reason");

        final List<List<String>> ts = read(dir.resolve("timeseries.csv"));
        assertEquals(exp.runs().stream().mapToInt(r -> r.timeSeries().size()).sum(), ts.size() - 1);

        final List<List<String>> workloads = read(dir.resolve("workloads.csv"));
        assertEquals(1 + 2, workloads.size());

        final List<String> config = Files.readAllLines(dir.resolve("config_used.properties"));
        assertTrue(config.contains("simulation.seed=42"));
        assertFalse(config.isEmpty());
    }

    @Test
    void commandLineEntryPointRuns(@TempDir Path out) throws Exception {
        final int code = com.scalix.SimulationMainAccess.run(new String[] {
            "--workloads", "MEDIUM", "--policies", "STATIC,SCALIX", "--out", out.toString(),
            "--experiment-id", "cli", "--set", "simulation.durationSeconds=60"});
        assertEquals(0, code);
        assertTrue(Files.exists(out.resolve("cli").resolve("summary.csv")));
    }

    /** Small CSV reader for the writer's output (handles quoted fields). */
    static List<List<String>> read(Path f) throws IOException {
        return Files.readAllLines(f).stream().map(ExperimentOutputIntegrationTest::parse).toList();
    }

    private static List<String> parse(String line) {
        final List<String> out = new java.util.ArrayList<>();
        final StringBuilder cur = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            final char c = line.charAt(i);
            if (quoted) {
                if (c == '"' && i + 1 < line.length() && line.charAt(i + 1) == '"') {
                    cur.append('"');
                    i++;
                } else if (c == '"') {
                    quoted = false;
                } else {
                    cur.append(c);
                }
            } else if (c == '"') {
                quoted = true;
            } else if (c == ',') {
                out.add(cur.toString());
                cur.setLength(0);
            } else {
                cur.append(c);
            }
        }
        out.add(cur.toString());
        return out;
    }
}
