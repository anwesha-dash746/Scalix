package com.scalix.metrics;

import com.scalix.scaling.ScalingAction;

/**
 * Aggregated metrics of one run. Every field is either MEASURED (derived directly from CloudSim Plus
 * event timings / VM state) or CALCULATED (an estimate built on top of measurements using an
 * assumed model, currently only cost). See {@code docs/METRICS.md} for exact definitions.
 */
public record RunSummary(
    int requestsTotal,
    int requestsCompleted,
    int requestsUnfinished,
    double avgResponseTime,
    double p50ResponseTime,
    double p95ResponseTime,
    double p99ResponseTime,
    double maxResponseTime,
    double avgWaitTime,
    double avgServiceTime,
    double cpuUtilization,
    double meanActiveVmUtilization,
    double avgBillableVms,
    int peakBillableVms,
    int minProvisionedVms,
    double vmSeconds,
    double billedVmSeconds,
    double totalCost,
    int scaleUpActions,
    int scaleDownActions,
    int totalScalingActions,
    int vmsAdded,
    int vmsRemoved,
    int cooldownSuppressions,
    double simEndTime,
    boolean drainTimedOut) {

    public static RunSummary of(RunResult r, CostModel cost) {
        final double[] response = r.requests().stream().filter(RequestRecord::completed)
            .mapToDouble(RequestRecord::responseTime).toArray();
        final double[] wait = r.requests().stream().filter(RequestRecord::completed)
            .mapToDouble(RequestRecord::waitTime).toArray();
        final double[] service = r.requests().stream().filter(RequestRecord::completed)
            .mapToDouble(RequestRecord::serviceTime).toArray();

        // Busy PE-seconds actually consumed by completed requests vs PE-seconds provisioned.
        double busyPeSeconds = 0;
        for (RequestRecord q : r.requests()) {
            if (q.completed()) {
                busyPeSeconds += q.serviceTime() * q.pes();
            }
        }
        double provisionedPeSeconds = 0;
        double vmSeconds = 0;
        double billed = 0;
        double totalCost = 0;
        for (VmRecord v : r.vms()) {
            final double life = v.lifetimeSeconds();
            vmSeconds += life;
            provisionedPeSeconds += life * v.pes();
            billed += cost.billedSeconds(life);
            totalCost += cost.cost(life);
        }

        double utilSum = 0;
        int utilN = 0;
        double billableSum = 0;
        int peak = 0;
        int minProvisioned = Integer.MAX_VALUE;
        for (TimeSeriesPoint p : r.timeSeries()) {
            if (!Double.isNaN(p.utilization())) {
                utilSum += p.utilization();
                utilN++;
            }
            billableSum += p.billableVms();
            peak = Math.max(peak, p.billableVms());
            minProvisioned = Math.min(minProvisioned, p.provisionedVms());
        }

        int ups = 0;
        int downs = 0;
        int added = 0;
        int removed = 0;
        int suppressed = 0;
        for (DecisionRecord d : r.decisions()) {
            if (d.appliedDelta() > 0) {
                ups++;
                added += d.appliedDelta();
            } else if (d.appliedDelta() < 0) {
                downs++;
                removed -= d.appliedDelta();
            }
            if (d.cooldownBlocked() && d.action() == ScalingAction.NONE) {
                suppressed++;
            }
        }

        final int completed = response.length;
        return new RunSummary(
            r.requests().size(),
            completed,
            r.requests().size() - completed,
            Stats.mean(response),
            Stats.percentile(response, 50),
            Stats.percentile(response, 95),
            Stats.percentile(response, 99),
            Stats.max(response),
            Stats.mean(wait),
            Stats.mean(service),
            provisionedPeSeconds > 0 ? busyPeSeconds / provisionedPeSeconds : Double.NaN,
            utilN > 0 ? utilSum / utilN : Double.NaN,
            r.timeSeries().isEmpty() ? Double.NaN : billableSum / r.timeSeries().size(),
            peak,
            minProvisioned == Integer.MAX_VALUE ? 0 : minProvisioned,
            vmSeconds,
            billed,
            totalCost,
            ups,
            downs,
            ups + downs,
            added,
            removed,
            suppressed,
            r.simEndTime(),
            r.drainTimedOut());
    }
}
