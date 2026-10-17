package com.scalix.allocation;

import org.cloudsimplus.vms.Vm;

import java.util.ArrayList;
import java.util.Collections;
import java.util.Comparator;
import java.util.List;

/**
 * Owns the fleet of {@link ManagedVm}s for one run and applies scale-up / scale-down actions.
 *
 * <p>Talks to the simulator only through {@link Provisioner}, so its logic can be tested without
 * CloudSim Plus. Scale-down never destroys a VM with queued work: the VM is drained first and
 * destroyed by {@link #reapDrained(double)} once its outstanding count reaches zero.
 */
public final class VmPool {

    /** Bridge to the simulator. */
    public interface Provisioner {
        /** Requests a new VM with the given id; returns the simulator object. */
        Vm create(long id);

        /** Destroys a VM that has no outstanding work. */
        void destroy(Vm vm);

        /** True once the simulator has placed the VM on a host. */
        boolean isPlaced(Vm vm);
    }

    private final Provisioner provisioner;
    private final int vmPes;
    private final double bootSeconds;
    private final List<ManagedVm> vms = new ArrayList<>();
    private long nextId;

    public VmPool(Provisioner provisioner, int vmPes, double bootSeconds) {
        this.provisioner = provisioner;
        this.vmPes = vmPes;
        this.bootSeconds = bootSeconds;
    }

    /**
     * Creates the initial fleet. Initial VMs are treated as already booted (they exist before the
     * workload starts), so they skip the boot delay.
     */
    public void provisionInitial(int count, double now) {
        for (int i = 0; i < count; i++) {
            vms.add(new ManagedVm(nextId, provisioner.create(nextId), vmPes, now, 0));
            nextId++;
        }
    }

    /** Adds {@code count} VMs in BOOTING state. */
    public List<ManagedVm> scaleUp(int count, double now) {
        final List<ManagedVm> added = new ArrayList<>();
        for (int i = 0; i < count; i++) {
            final ManagedVm m = new ManagedVm(nextId, provisioner.create(nextId), vmPes, now, bootSeconds);
            nextId++;
            vms.add(m);
            added.add(m);
        }
        return added;
    }

    /** Marks {@code count} VMs for removal (see {@link #selectForRemoval}). Returns the VMs chosen. */
    public List<ManagedVm> scaleDown(int count, double now) {
        final List<ManagedVm> victims = selectForRemoval(vms, count);
        for (ManagedVm m : victims) {
            m.startDraining(now);
        }
        reapDrained(now);
        return victims;
    }

    /**
     * Chooses VMs to remove: booting VMs first (newest first, they serve nothing yet), then active
     * VMs with the fewest outstanding requests (newest first on ties). Never selects more VMs than
     * would leave at least one ACTIVE or BOOTING VM.
     */
    public static List<ManagedVm> selectForRemoval(List<ManagedVm> fleet, int count) {
        if (count < 0) {
            throw new IllegalArgumentException("count must be >= 0");
        }
        final List<ManagedVm> booting = new ArrayList<>();
        final List<ManagedVm> active = new ArrayList<>();
        for (ManagedVm m : fleet) {
            if (m.state() == VmState.BOOTING) {
                booting.add(m);
            } else if (m.state() == VmState.ACTIVE) {
                active.add(m);
            }
        }
        booting.sort(Comparator.comparingLong(ManagedVm::id).reversed());
        active.sort(Comparator.comparingInt(ManagedVm::outstanding)
            .thenComparing(Comparator.comparingLong(ManagedVm::id).reversed()));

        // Booting VMs come first and the most loaded active VM comes last, so keeping the final
        // candidate always keeps a serving VM when one exists. At least one VM always survives.
        final List<ManagedVm> candidates = new ArrayList<>(booting);
        candidates.addAll(active);
        final int n = Math.min(count, Math.max(0, candidates.size() - 1));
        return new ArrayList<>(candidates.subList(0, n));
    }

    /** Activates VMs whose boot delay has elapsed. */
    public void activateReady(double now) {
        for (ManagedVm m : vms) {
            if (m.state() == VmState.BOOTING) {
                m.activateIfReady(now, provisioner.isPlaced(m.vm()));
            }
        }
    }

    /**
     * Destroys draining VMs that have no outstanding requests. A VM the simulator has not placed yet
     * is kept until it is placed, so SCALIX never stops billing a VM that still exists in CloudSim Plus.
     */
    public void reapDrained(double now) {
        for (ManagedVm m : vms) {
            if (m.state() == VmState.DRAINING && m.outstanding() == 0 && provisioner.isPlaced(m.vm())) {
                provisioner.destroy(m.vm());
                m.markTerminated(now);
            }
        }
    }

    /** Terminates every remaining VM (end of run) so lifetimes are closed for billing. */
    public void terminateAll(double now) {
        for (ManagedVm m : vms) {
            if (m.state() != VmState.TERMINATED) {
                m.markTerminated(now);
            }
        }
    }

    public int count(VmState state) {
        int c = 0;
        for (ManagedVm m : vms) {
            if (m.state() == state) {
                c++;
            }
        }
        return c;
    }

    /** VMs counted against policy limits: ACTIVE + BOOTING. */
    public int provisioned() {
        return count(VmState.ACTIVE) + count(VmState.BOOTING);
    }

    public List<ManagedVm> all() {
        return Collections.unmodifiableList(vms);
    }
}
