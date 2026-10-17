package com.scalix.allocation;

import java.util.Collection;
import java.util.Comparator;
import java.util.Optional;

/**
 * Routes each request to the ACTIVE VM with the fewest outstanding requests (ties go to the lowest
 * id, which keeps runs deterministic). Identical for every policy, so only scaling differs.
 */
public final class LoadBalancer {

    private static final Comparator<ManagedVm> LEAST_OUTSTANDING =
        Comparator.comparingInt(ManagedVm::outstanding).thenComparingLong(ManagedVm::id);

    private LoadBalancer() { }

    public static Optional<ManagedVm> select(Collection<ManagedVm> vms) {
        return vms.stream()
            .filter(v -> v.state() == VmState.ACTIVE)
            .min(LEAST_OUTSTANDING);
    }
}
