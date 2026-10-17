package com.scalix.metrics;

/**
 * Lifetime of one VM in a run.
 *
 * @param vmId            VM id
 * @param pes             PEs
 * @param requestTime     scale-up request (or run start); billing starts here
 * @param activatedTime   when it started accepting traffic (NaN if never)
 * @param drainStartTime  when it was selected for removal (NaN if never)
 * @param terminationTime when it was destroyed, or the run end time
 */
public record VmRecord(long vmId, int pes, double requestTime, double activatedTime,
                       double drainStartTime, double terminationTime) {

    public double lifetimeSeconds() {
        return Math.max(0, terminationTime - requestTime);
    }
}
