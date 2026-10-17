package com.scalix.metrics;

import com.scalix.workload.WorkloadSpec;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;

/**
 * Raw output of one simulation run: everything measured, before any aggregation.
 *
 * @param runId            unique id of the run
 * @param workload         workload definition (type, parameters, seed)
 * @param policyName       policy identifier
 * @param policyParameters policy configuration
 * @param requests         one record per generated request
 * @param timeSeries       one point per controller tick
 * @param decisions        one record per policy evaluation
 * @param vms              one record per VM ever provisioned
 * @param simEndTime       simulated time at which the run stopped
 * @param drainTimedOut    true if the run hit {@code simulation.maxDrainSeconds} with requests unfinished
 * @param wallClockMillis  real time the run took
 */
public record RunResult(String runId, WorkloadSpec workload, String policyName,
                        Map<String, String> policyParameters, List<RequestRecord> requests,
                        List<TimeSeriesPoint> timeSeries, List<DecisionRecord> decisions,
                        List<VmRecord> vms, double simEndTime, boolean drainTimedOut,
                        long wallClockMillis) {

    public RunResult {
        policyParameters = Collections.unmodifiableMap(new LinkedHashMap<>(policyParameters));
        requests = List.copyOf(requests);
        timeSeries = List.copyOf(timeSeries);
        decisions = List.copyOf(decisions);
        vms = List.copyOf(vms);
    }
}
