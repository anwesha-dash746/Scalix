package com.scalix.metrics;

import com.scalix.config.SimulationConfig;

/**
 * Explicit, assumed pricing model. Cost is a CALCULATED ESTIMATE derived from measured VM lifetimes;
 * it is not produced by CloudSim Plus and does not reflect any real provider's price list.
 *
 * <pre>
 * billedSeconds(vm) = max(minimumBillingSeconds, ceil(lifetime / granularity) * granularity)
 * cost(vm)          = billedSeconds(vm) / 3600 * pricePerHour
 * </pre>
 */
public record CostModel(double pricePerHour, double granularitySeconds, double minimumBillingSeconds) {

    public CostModel {
        if (!(pricePerHour >= 0) || !(granularitySeconds > 0) || !(minimumBillingSeconds >= 0)) {
            throw new IllegalArgumentException("Invalid cost model parameters");
        }
    }

    public static CostModel fromConfig(SimulationConfig cfg) {
        return new CostModel(cfg.vmPricePerHour(), cfg.billingGranularitySeconds(), cfg.minimumBillingSeconds());
    }

    public double billedSeconds(double lifetimeSeconds) {
        if (lifetimeSeconds < 0) {
            throw new IllegalArgumentException("lifetime must be >= 0");
        }
        // Subtract a tiny epsilon so floating-point noise (e.g. 60.0000000001) does not bill an extra unit.
        final double units = Math.ceil(lifetimeSeconds / granularitySeconds - 1e-9);
        return Math.max(minimumBillingSeconds, Math.max(0, units) * granularitySeconds);
    }

    public double cost(double lifetimeSeconds) {
        return billedSeconds(lifetimeSeconds) / 3600.0 * pricePerHour;
    }

    public String describe() {
        return "price/VM-hour=" + pricePerHour + ";granularity_s=" + granularitySeconds + ";minimum_s=" + minimumBillingSeconds;
    }
}
