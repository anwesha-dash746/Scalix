package com.scalix.allocation;

/** SCALIX-level lifecycle of a VM (independent of CloudSim Plus internal states). */
public enum VmState {
    /** Requested and billed, but not yet accepting traffic (boot delay). */
    BOOTING,
    /** Accepting new requests from the load balancer. */
    ACTIVE,
    /** Selected for removal: receives no new requests and is destroyed once its queue is empty. */
    DRAINING,
    /** Destroyed; no longer billed. */
    TERMINATED
}
