package com.scalix.allocation;

import org.cloudsimplus.vms.Vm;
import org.junit.jupiter.api.Test;

import java.util.ArrayList;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class VmPoolTest {

    /** Fake provisioner: every VM is placed immediately; records destructions. */
    private static final class FakeProvisioner implements VmPool.Provisioner {
        final List<Vm> destroyed = new ArrayList<>();
        boolean placed = true;

        @Override public Vm create(long id) { return Vm.NULL; }
        @Override public void destroy(Vm vm) { destroyed.add(vm); }
        @Override public boolean isPlaced(Vm vm) { return placed; }
    }

    @Test
    void initialVmsSkipBootAndScaleUpVmsBoot() {
        final VmPool pool = new VmPool(new FakeProvisioner(), 2, 30);
        pool.provisionInitial(2, 0);
        pool.activateReady(0);
        assertEquals(2, pool.count(VmState.ACTIVE));

        pool.scaleUp(1, 100);
        assertEquals(1, pool.count(VmState.BOOTING));
        assertEquals(3, pool.provisioned());
        pool.activateReady(129);
        assertEquals(1, pool.count(VmState.BOOTING));
        pool.activateReady(130);
        assertEquals(3, pool.count(VmState.ACTIVE));
    }

    @Test
    void unplacedVmDoesNotActivate() {
        final FakeProvisioner prov = new FakeProvisioner();
        prov.placed = false;
        final VmPool pool = new VmPool(prov, 2, 0);
        pool.provisionInitial(1, 0);
        pool.activateReady(10);
        assertEquals(1, pool.count(VmState.BOOTING));
    }

    @Test
    void scaleDownDrainsBeforeDestroying() {
        final FakeProvisioner prov = new FakeProvisioner();
        final VmPool pool = new VmPool(prov, 2, 0);
        pool.provisionInitial(3, 0);
        pool.activateReady(0);
        for (ManagedVm m : pool.all()) {
            m.requestDispatched();      // every VM busy with one request
        }
        final List<ManagedVm> victims = pool.scaleDown(1, 50);
        assertEquals(1, victims.size());
        assertEquals(VmState.DRAINING, victims.get(0).state());
        assertTrue(prov.destroyed.isEmpty(), "busy VM must not be destroyed");
        assertEquals(2, pool.provisioned());

        victims.get(0).requestFinished();
        pool.reapDrained(55);
        assertEquals(VmState.TERMINATED, victims.get(0).state());
        assertEquals(1, prov.destroyed.size());
        assertEquals(55, victims.get(0).lifetime(999), 1e-9);
    }

    @Test
    void idleVmIsDestroyedImmediately() {
        final FakeProvisioner prov = new FakeProvisioner();
        final VmPool pool = new VmPool(prov, 2, 0);
        pool.provisionInitial(2, 0);
        pool.activateReady(0);
        pool.scaleDown(1, 10);
        assertEquals(1, pool.count(VmState.TERMINATED));
    }

    @Test
    void removalPrefersBootingThenLeastLoadedNewest() {
        final VmPool pool = new VmPool(new FakeProvisioner(), 2, 30);
        pool.provisionInitial(3, 0);          // ids 0,1,2 active
        pool.activateReady(0);
        pool.all().get(0).requestDispatched();
        pool.all().get(0).requestDispatched();
        pool.all().get(2).requestDispatched();
        pool.scaleUp(1, 10);                  // id 3 booting

        final List<ManagedVm> chosen = VmPool.selectForRemoval(pool.all(), 2);
        assertEquals(List.of(3L, 1L), chosen.stream().map(ManagedVm::id).toList());
    }

    @Test
    void removalAlwaysKeepsOneServingVm() {
        final VmPool pool = new VmPool(new FakeProvisioner(), 2, 30);
        pool.provisionInitial(1, 0);
        pool.activateReady(0);
        pool.scaleUp(2, 5);
        final List<ManagedVm> chosen = VmPool.selectForRemoval(pool.all(), 10);
        assertEquals(2, chosen.size());
        assertTrue(chosen.stream().allMatch(m -> m.state() == VmState.BOOTING));
        assertTrue(VmPool.selectForRemoval(List.of(pool.all().get(0)), 1).isEmpty());
        assertThrows(IllegalArgumentException.class, () -> VmPool.selectForRemoval(pool.all(), -1));
    }

    @Test
    void loadBalancerPicksLeastOutstandingActiveVm() {
        final VmPool pool = new VmPool(new FakeProvisioner(), 2, 30);
        pool.provisionInitial(3, 0);
        pool.activateReady(0);
        pool.all().get(0).requestDispatched();
        pool.all().get(1).requestDispatched();
        assertEquals(2, LoadBalancer.select(pool.all()).orElseThrow().id());
        pool.all().get(2).requestDispatched();
        assertEquals(0, LoadBalancer.select(pool.all()).orElseThrow().id(), "ties go to lowest id");
        pool.scaleUp(1, 1);                     // booting VM has 0 outstanding but must not be chosen
        assertEquals(0, LoadBalancer.select(pool.all()).orElseThrow().id());
    }

    @Test
    void misuseIsDetected() {
        final ManagedVm m = new ManagedVm(0, Vm.NULL, 2, 0, 10);
        assertThrows(IllegalStateException.class, m::requestDispatched);   // still booting
        assertThrows(IllegalStateException.class, m::requestFinished);
        assertThrows(IllegalArgumentException.class, () -> new ManagedVm(1, Vm.NULL, 2, 0, -1));
    }
}
