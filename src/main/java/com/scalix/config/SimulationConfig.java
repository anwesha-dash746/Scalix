package com.scalix.config;

import java.io.IOException;
import java.io.InputStream;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Properties;
import java.util.TreeMap;

/**
 * Immutable, validated view of every experimental parameter.
 *
 * <p>Values come from {@code scalix-default.properties} on the classpath, optionally overridden by a
 * user properties file and then by individual {@code key=value} overrides. Nothing experimental is
 * hardcoded in the engine or the policies; they read it from here.
 */
public final class SimulationConfig {

    public static final String DEFAULTS_RESOURCE = "/scalix-default.properties";

    private final Map<String, String> values;

    private SimulationConfig(Map<String, String> values) {
        this.values = Collections.unmodifiableMap(new TreeMap<>(values));
        validate();
    }

    /** Loads the bundled defaults only. */
    public static SimulationConfig defaults() {
        return builder().build();
    }

    public static Builder builder() {
        return new Builder();
    }

    // ---------------------------------------------------------------- simulation

    public long seed() { return getLong("simulation.seed"); }
    public double durationSeconds() { return getDouble("simulation.durationSeconds"); }
    public double tickSeconds() { return getDouble("simulation.tickSeconds"); }
    public double decisionIntervalSeconds() { return getDouble("simulation.decisionIntervalSeconds"); }
    public double maxDrainSeconds() { return getDouble("simulation.maxDrainSeconds"); }

    // ---------------------------------------------------------------- infrastructure

    public int hostCount() { return getInt("host.count"); }
    public int hostPes() { return getInt("host.pes"); }
    public double hostMipsPerPe() { return getDouble("host.mipsPerPe"); }
    public long hostRamMb() { return getLong("host.ramMb"); }
    public long hostBwMbps() { return getLong("host.bwMbps"); }
    public long hostStorageMb() { return getLong("host.storageMb"); }

    public int vmPes() { return getInt("vm.pes"); }
    public double vmMipsPerPe() { return getDouble("vm.mipsPerPe"); }
    public long vmRamMb() { return getLong("vm.ramMb"); }
    public long vmBwMbps() { return getLong("vm.bwMbps"); }
    public long vmSizeMb() { return getLong("vm.sizeMb"); }
    public double vmBootSeconds() { return getDouble("vm.bootSeconds"); }

    // ---------------------------------------------------------------- pricing

    public double vmPricePerHour() { return getDouble("pricing.vmPricePerHour"); }
    public double billingGranularitySeconds() { return getDouble("pricing.billingGranularitySeconds"); }
    public double minimumBillingSeconds() { return getDouble("pricing.minimumBillingSeconds"); }

    // ---------------------------------------------------------------- requests

    public double requestMeanLengthMi() { return getDouble("request.meanLengthMi"); }
    public double requestLengthVariation() { return getDouble("request.lengthVariation"); }
    public int requestPes() { return getInt("request.pes"); }

    // ---------------------------------------------------------------- experiment

    public List<String> workloads() { return getList("experiment.workloads"); }
    public List<String> policies() { return getList("experiment.policies"); }
    public String outputDir() { return getString("export.outputDir"); }
    public boolean exportRequests() { return getBoolean("export.requests"); }

    // ---------------------------------------------------------------- generic accessors

    public String getString(String key) {
        final String v = values.get(key);
        if (v == null) {
            throw new IllegalArgumentException("Missing configuration key: " + key);
        }
        return v.trim();
    }

    public double getDouble(String key) {
        final String raw = getString(key);
        try {
            final double d = Double.parseDouble(raw);
            if (Double.isNaN(d) || Double.isInfinite(d)) {
                throw new IllegalArgumentException("Configuration key " + key + " must be finite, got " + raw);
            }
            return d;
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Configuration key " + key + " is not a number: " + raw, e);
        }
    }

    public long getLong(String key) {
        final String raw = getString(key);
        try {
            return Long.parseLong(raw);
        } catch (NumberFormatException e) {
            throw new IllegalArgumentException("Configuration key " + key + " is not an integer: " + raw, e);
        }
    }

    public int getInt(String key) {
        final long v = getLong(key);
        if (v > Integer.MAX_VALUE || v < Integer.MIN_VALUE) {
            throw new IllegalArgumentException("Configuration key " + key + " is out of int range: " + v);
        }
        return (int) v;
    }

    public boolean getBoolean(String key) {
        final String raw = getString(key).toLowerCase(Locale.ROOT);
        if (!raw.equals("true") && !raw.equals("false")) {
            throw new IllegalArgumentException("Configuration key " + key + " must be true or false, got " + raw);
        }
        return Boolean.parseBoolean(raw);
    }

    public List<String> getList(String key) {
        final List<String> out = new ArrayList<>();
        for (String s : getString(key).split(",")) {
            if (!s.isBlank()) {
                out.add(s.trim().toUpperCase(Locale.ROOT));
            }
        }
        return Collections.unmodifiableList(out);
    }

    /** All keys with a given prefix, prefix stripped, in sorted order. */
    public Map<String, String> withPrefix(String prefix) {
        final Map<String, String> out = new LinkedHashMap<>();
        values.forEach((k, v) -> {
            if (k.startsWith(prefix)) {
                out.put(k.substring(prefix.length()), v.trim());
            }
        });
        return out;
    }

    /** Every effective key/value pair, sorted by key. */
    public Map<String, String> asMap() {
        return values;
    }

    /** Returns a copy with the given overrides applied (and re-validated). */
    public SimulationConfig with(Map<String, String> overrides) {
        final Map<String, String> copy = new TreeMap<>(values);
        copy.putAll(overrides);
        return new SimulationConfig(copy);
    }

    public SimulationConfig with(String key, String value) {
        return with(Map.of(key, value));
    }

    // ---------------------------------------------------------------- validation

    private void validate() {
        seed();
        requirePositive("simulation.durationSeconds", durationSeconds());
        requirePositive("simulation.tickSeconds", tickSeconds());
        requirePositive("simulation.decisionIntervalSeconds", decisionIntervalSeconds());
        if (decisionIntervalSeconds() < tickSeconds()) {
            throw new IllegalArgumentException("simulation.decisionIntervalSeconds must be >= simulation.tickSeconds");
        }
        requireNonNegative("simulation.maxDrainSeconds", maxDrainSeconds());

        requirePositive("host.count", hostCount());
        requirePositive("host.pes", hostPes());
        requirePositive("host.mipsPerPe", hostMipsPerPe());
        requirePositive("host.ramMb", hostRamMb());
        requirePositive("host.bwMbps", hostBwMbps());
        requirePositive("host.storageMb", hostStorageMb());

        requirePositive("vm.pes", vmPes());
        requirePositive("vm.mipsPerPe", vmMipsPerPe());
        requirePositive("vm.ramMb", vmRamMb());
        requirePositive("vm.bwMbps", vmBwMbps());
        requirePositive("vm.sizeMb", vmSizeMb());
        requireNonNegative("vm.bootSeconds", vmBootSeconds());
        if (vmPes() > hostPes() || vmMipsPerPe() > hostMipsPerPe() || vmRamMb() > hostRamMb()
            || vmBwMbps() > hostBwMbps() || vmSizeMb() > hostStorageMb()) {
            throw new IllegalArgumentException("A single VM does not fit on a host; check vm.* against host.*");
        }

        requireNonNegative("pricing.vmPricePerHour", vmPricePerHour());
        requirePositive("pricing.billingGranularitySeconds", billingGranularitySeconds());
        requireNonNegative("pricing.minimumBillingSeconds", minimumBillingSeconds());

        requirePositive("request.meanLengthMi", requestMeanLengthMi());
        final double variation = requestLengthVariation();
        if (variation < 0 || variation >= 1) {
            throw new IllegalArgumentException("request.lengthVariation must be in [0, 1), got " + variation);
        }
        requirePositive("request.pes", requestPes());
        if (requestPes() > vmPes()) {
            throw new IllegalArgumentException("request.pes must not exceed vm.pes");
        }

        if (workloads().isEmpty()) {
            throw new IllegalArgumentException("experiment.workloads must list at least one workload");
        }
        if (policies().isEmpty()) {
            throw new IllegalArgumentException("experiment.policies must list at least one policy");
        }
        getString("export.outputDir");
        exportRequests();

        // Every VM a policy may ever request must fit in the datacenter, otherwise scale-ups would
        // silently fail inside CloudSim Plus and the comparison would be unfair.
        final int maxVmsAnyPolicy = Math.max(getInt("policy.static.vmCount"), getInt("policy.scaling.maxVms"));
        final long vmsPerHostByPes = hostPes() / vmPes();
        final long vmsPerHostByRam = hostRamMb() / vmRamMb();
        final long vmsPerHostByBw = hostBwMbps() / vmBwMbps();
        final long vmsPerHostBySize = hostStorageMb() / vmSizeMb();
        final long vmsPerHost = Math.min(Math.min(vmsPerHostByPes, vmsPerHostByRam), Math.min(vmsPerHostByBw, vmsPerHostBySize));
        if (vmsPerHost * hostCount() < maxVmsAnyPolicy) {
            throw new IllegalArgumentException("Datacenter capacity (" + vmsPerHost * hostCount()
                + " VMs) is smaller than the largest VM count a policy may request (" + maxVmsAnyPolicy + ")");
        }
    }

    private static void requirePositive(String key, double v) {
        if (!(v > 0)) {
            throw new IllegalArgumentException(key + " must be > 0, got " + v);
        }
    }

    private static void requireNonNegative(String key, double v) {
        if (!(v >= 0)) {
            throw new IllegalArgumentException(key + " must be >= 0, got " + v);
        }
    }

    @Override
    public String toString() {
        return values.toString();
    }

    // ---------------------------------------------------------------- builder

    public static final class Builder {
        private final Map<String, String> values = new TreeMap<>();

        private Builder() {
            try (InputStream in = SimulationConfig.class.getResourceAsStream(DEFAULTS_RESOURCE)) {
                if (in == null) {
                    throw new IllegalStateException("Missing classpath resource " + DEFAULTS_RESOURCE);
                }
                putAll(load(in));
            } catch (IOException e) {
                throw new IllegalStateException("Cannot read " + DEFAULTS_RESOURCE, e);
            }
        }

        /** Overlays a user properties file. Unknown keys are rejected to catch typos early. */
        public Builder file(Path path) throws IOException {
            try (Reader r = Files.newBufferedReader(path, StandardCharsets.UTF_8)) {
                final Properties p = new Properties();
                p.load(r);
                final Map<String, String> m = new TreeMap<>();
                p.stringPropertyNames().forEach(k -> m.put(k, p.getProperty(k)));
                return overrides(m);
            }
        }

        /** Overlays individual keys. Unknown keys are rejected to catch typos early. */
        public Builder overrides(Map<String, String> overrides) {
            for (Map.Entry<String, String> e : overrides.entrySet()) {
                if (!values.containsKey(e.getKey())) {
                    throw new IllegalArgumentException("Unknown configuration key: " + e.getKey()
                        + " (see " + DEFAULTS_RESOURCE + " for valid keys)");
                }
                values.put(e.getKey(), e.getValue());
            }
            return this;
        }

        /** Parses {@code key=value} strings. */
        public Builder overrides(String... keyValues) {
            final Map<String, String> m = new LinkedHashMap<>();
            for (String kv : keyValues) {
                final int eq = kv.indexOf('=');
                if (eq <= 0) {
                    throw new IllegalArgumentException("Override must be key=value, got: " + kv);
                }
                m.put(kv.substring(0, eq).trim(), kv.substring(eq + 1).trim());
            }
            return overrides(m);
        }

        public SimulationConfig build() {
            return new SimulationConfig(values);
        }

        private void putAll(Properties p) {
            p.stringPropertyNames().forEach(k -> values.put(k, p.getProperty(k)));
        }

        private static Properties load(InputStream in) throws IOException {
            final Properties p = new Properties();
            p.load(new java.io.InputStreamReader(in, StandardCharsets.UTF_8));
            return p;
        }
    }
}
