package com.scalix.allocation;

import org.cloudsimplus.vms.Vm;

/**
 * A CloudSim Plus {@link Vm} plus the SCALIX lifecycle and bookkeeping needed for load balancing,
 * draining and billing.
 */
public final class ManagedVm {

    private final long id;
    private final Vm vm;
    private final int pes;
    private final double requestTime;
    private final double readyTime;
    private VmState state;
    private int outstanding;
    private double activatedTime = Double.NaN;
    private double drainStartTime = Double.NaN;
    private double terminationTime = Double.NaN;

    /**
     * @param id          SCALIX id (equal to the CloudSim Plus VM id)
     * @param vm          underlying CloudSim Plus VM (may be {@link Vm#NULL} in unit tests)
     * @param pes         number of PEs
     * @param requestTime simulated time the VM was requested (billing starts here)
     * @param bootSeconds delay before the VM may receive traffic
     */
    public ManagedVm(long id, Vm vm, int pes, double requestTime, double bootSeconds) {
        if (bootSeconds < 0) {
            throw new IllegalArgumentException("bootSeconds must be >= 0");
        }
        this.id = id;
        this.vm = vm;
        this.pes = pes;
        this.requestTime = requestTime;
        this.readyTime = requestTime + bootSeconds;
        this.state = VmState.BOOTING;
    }

    /** Moves BOOTING → ACTIVE once the boot delay has elapsed and CloudSim Plus has placed the VM. */
    public boolean activateIfReady(double now, boolean placedOnHost) {
        if (state == VmState.BOOTING && placedOnHost && now + 1e-9 >= readyTime) {
            state = VmState.ACTIVE;
            activatedTime = now;
            return true;
        }
        return false;
    }

    public void startDraining(double now) {
        if (state != VmState.ACTIVE && state != VmState.BOOTING) {
            throw new IllegalStateException("VM " + id + " cannot drain from state " + state);
        }
        state = VmState.DRAINING;
        drainStartTime = now;
    }

    public void markTerminated(double now) {
        if (state == VmState.TERMINATED) {
            return;
        }
        state = VmState.TERMINATED;
        terminationTime = now;
    }

    public void requestDispatched() {
        if (state != VmState.ACTIVE) {
            throw new IllegalStateException("Request dispatched to VM " + id + " in state " + state);
        }
        outstanding++;
    }

    public void requestFinished() {
        if (outstanding <= 0) {
            throw new IllegalStateException("VM " + id + " finished more requests than it received");
        }
        outstanding--;
    }

    /** Seconds this VM has been (or was) provisioned and billable up to {@code now}. */
    public double lifetime(double now) {
        final double end = Double.isNaN(terminationTime) ? now : terminationTime;
        return Math.max(0, end - requestTime);
    }

    public long id() { return id; }
    public Vm vm() { return vm; }
    public int pes() { return pes; }
    public VmState state() { return state; }
    public int outstanding() { return outstanding; }
    public double requestTime() { return requestTime; }
    public double readyTime() { return readyTime; }
    public double activatedTime() { return activatedTime; }
    public double drainStartTime() { return drainStartTime; }
    public double terminationTime() { return terminationTime; }

    @Override
    public String toString() {
        return "ManagedVm{id=" + id + ", state=" + state + ", outstanding=" + outstanding + '}';
    }
}
