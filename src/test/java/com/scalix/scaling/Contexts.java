package com.scalix.scaling;

import java.util.List;

/** Fluent builder for {@link ScalingContext} in tests. */
final class Contexts {

    private double time = 300;
    private int active = 4;
    private int booting = 0;
    private double utilization = 0.5;
    private List<Double> rates = List.of(5.0, 5.0, 5.0, 5.0, 5.0, 5.0);
    private double lastUp = Double.NEGATIVE_INFINITY;
    private double lastDown = Double.NEGATIVE_INFINITY;

    static Contexts ctx() {
        return new Contexts();
    }

    Contexts time(double t) { this.time = t; return this; }
    Contexts active(int n) { this.active = n; return this; }
    Contexts booting(int n) { this.booting = n; return this; }
    Contexts util(double u) { this.utilization = u; return this; }
    Contexts rates(Double... r) { this.rates = List.of(r); return this; }
    Contexts lastUp(double t) { this.lastUp = t; return this; }
    Contexts lastDown(double t) { this.lastDown = t; return this; }

    static List<Double> rising() { return List.of(4.0, 5.0, 6.0, 7.0, 8.0, 9.0); }
    static List<Double> falling() { return List.of(9.0, 8.0, 7.0, 6.0, 5.0, 4.0); }
    static List<Double> stable() { return List.of(5.0, 5.0, 5.0, 5.0, 5.0, 5.0); }

    Contexts rates(List<Double> r) { this.rates = r; return this; }

    ScalingContext build() {
        return new ScalingContext(time, active + booting, active, booting, utilization,
            List.of(utilization), rates, lastUp, lastDown);
    }
}
