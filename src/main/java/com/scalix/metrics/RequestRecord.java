package com.scalix.metrics;

/**
 * Measured timings of one request. Times are simulated seconds taken from CloudSim Plus; NaN means
 * the event did not happen before the run ended.
 *
 * @param requestId    workload request id
 * @param arrivalTime  arrival at the load balancer
 * @param dispatchTime when the load balancer submitted it to a VM (Cloudlet submission)
 * @param vmId         VM that served it, or -1 if never dispatched
 * @param startTime    CloudSim Plus Cloudlet execution start (after any VM queueing)
 * @param finishTime   CloudSim Plus Cloudlet finish time
 * @param pes          PEs used while executing
 * @param lengthMi     request length in million instructions
 */
public record RequestRecord(long requestId, double arrivalTime, double dispatchTime, long vmId,
                            double startTime, double finishTime, int pes, long lengthMi) {

    public boolean completed() {
        return !Double.isNaN(finishTime);
    }

    /** Arrival → finish: load-balancer wait + VM queue wait + execution. */
    public double responseTime() {
        return finishTime - arrivalTime;
    }

    /** Arrival → execution start. */
    public double waitTime() {
        return startTime - arrivalTime;
    }

    /** Execution time on the VM. */
    public double serviceTime() {
        return finishTime - startTime;
    }
}
