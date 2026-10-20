import com.scalix.scaling.ScalingContext;
import com.scalix.scaling.ScalingDecision;
import com.scalix.scaling.ScalingPolicy;
import com.scalix.scaling.ScalixPolicy;
import com.scalix.scaling.StaticPolicy;
import com.scalix.scaling.ThresholdPolicy;
import com.scalix.scaling.VmLimits;

import java.io.BufferedReader;
import java.io.IOException;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

/**
 * Test-only harness: runs the original Java policies on contexts written by
 * tests/test_java_parity.py and prints one decision per line, so the Python port can be
 * compared with the Java implementation decision by decision.
 *
 * Input (tab-separated), first line = policy:
 *   STATIC n
 *   THRESHOLD min initial max upper lower cooldown
 *   SCALIX min initial max upper lower preemptive target window trendThr projection upCd downCd maxStep
 * then one context per line:
 *   time provisioned active booting utilization rates(comma-separated or -) lastUp lastDown
 * Output per context: action \t delta \t trend \t slope \t cooldownBlocked \t reason
 */
public final class PolicyParity {

    public static void main(String[] args) throws IOException {
        final List<String> lines = Files.readAllLines(Path.of(args[0]), StandardCharsets.UTF_8);
        final ScalingPolicy policy = policy(lines.get(0).split("\t"));
        final StringBuilder out = new StringBuilder();
        for (int i = 1; i < lines.size(); i++) {
            final String[] f = lines.get(i).split("\t");
            final double u = Double.parseDouble(f[4]);
            final ScalingContext ctx = new ScalingContext(
                Double.parseDouble(f[0]), Integer.parseInt(f[1]), Integer.parseInt(f[2]),
                Integer.parseInt(f[3]), u, List.of(u), rates(f[5]),
                Double.parseDouble(f[6]), Double.parseDouble(f[7]));
            final ScalingDecision d = policy.decide(ctx);
            out.append(d.action()).append('\t').append(d.delta()).append('\t').append(d.trend())
                .append('\t').append(Double.toString(d.relativeSlope())).append('\t')
                .append(d.cooldownBlocked()).append('\t').append(d.reason()).append('\n');
        }
        System.out.print(out);
    }

    private static List<Double> rates(String field) {
        final List<Double> r = new ArrayList<>();
        if (!field.equals("-")) {
            for (String s : field.split(",")) {
                r.add(Double.parseDouble(s));
            }
        }
        return r;
    }

    private static ScalingPolicy policy(String[] p) {
        switch (p[0]) {
            case "STATIC":
                return new StaticPolicy(Integer.parseInt(p[1]));
            case "THRESHOLD":
                return new ThresholdPolicy(limits(p), d(p[4]), d(p[5]), d(p[6]));
            case "SCALIX":
                return new ScalixPolicy(limits(p), d(p[4]), d(p[5]), d(p[6]), d(p[7]),
                    Integer.parseInt(p[8]), d(p[9]), Integer.parseInt(p[10]), d(p[11]), d(p[12]),
                    Integer.parseInt(p[13]));
            default:
                throw new IllegalArgumentException("unknown policy " + p[0]);
        }
    }

    private static VmLimits limits(String[] p) {
        return new VmLimits(Integer.parseInt(p[1]), Integer.parseInt(p[2]), Integer.parseInt(p[3]));
    }

    private static double d(String s) {
        return Double.parseDouble(s);
    }
}
