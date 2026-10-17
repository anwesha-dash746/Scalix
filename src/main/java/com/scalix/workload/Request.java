package com.scalix.workload;

/**
 * One user request, generated before the simulation so that every policy receives the identical
 * sequence. It becomes a CloudSim Plus Cloudlet when it is dispatched to a VM.
 *
 * @param id          sequential id within the workload
 * @param arrivalTime simulated time (s) at which the request reaches the load balancer
 * @param lengthMi    CPU work in million instructions
 * @param pes         number of PEs the request needs
 */
public record Request(long id, double arrivalTime, long lengthMi, int pes) {
    public Request {
        if (arrivalTime < 0) {
            throw new IllegalArgumentException("arrivalTime must be >= 0");
        }
        if (lengthMi <= 0) {
            throw new IllegalArgumentException("lengthMi must be > 0");
        }
        if (pes <= 0) {
            throw new IllegalArgumentException("pes must be > 0");
        }
    }
}
