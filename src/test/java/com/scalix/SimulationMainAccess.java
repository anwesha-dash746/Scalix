package com.scalix;

/** Exposes {@link SimulationMain#run(String[])} (package-private) to tests in other packages. */
public final class SimulationMainAccess {

    private SimulationMainAccess() { }

    public static int run(String[] args) throws Exception {
        return SimulationMain.run(args);
    }
}
