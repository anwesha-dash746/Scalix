package com.scalix.metrics;

import org.junit.jupiter.api.Test;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class MetricsTest {

    @Test
    void percentileInterpolatesLikeNumpy() {
        final double[] xs = {1, 2, 3, 4, 5, 6, 7, 8, 9, 10};
        assertEquals(5.5, Stats.percentile(xs, 50), 1e-12);
        assertEquals(9.55, Stats.percentile(xs, 95), 1e-12);
        assertEquals(1, Stats.percentile(xs, 0), 1e-12);
        assertEquals(10, Stats.percentile(xs, 100), 1e-12);
        assertEquals(7, Stats.percentile(new double[] {7}, 95), 1e-12);
    }

    @Test
    void emptyStatisticsAreNaNNotZero() {
        assertTrue(Double.isNaN(Stats.mean(new double[0])));
        assertTrue(Double.isNaN(Stats.percentile(new double[0], 95)));
        assertTrue(Double.isNaN(Stats.max(new double[0])));
        assertThrows(IllegalArgumentException.class, () -> Stats.percentile(new double[] {1}, 101));
    }

    @Test
    void costModelAppliesGranularityAndMinimum() {
        final CostModel perSecond = new CostModel(0.36, 1, 60);
        assertEquals(60, perSecond.billedSeconds(10), 1e-12);         // minimum applies
        assertEquals(61, perSecond.billedSeconds(60.2), 1e-12);       // rounded up to the second
        assertEquals(0.0061, perSecond.cost(61), 1e-12);              // 61 s at $0.36/h
        final CostModel hourly = new CostModel(1.0, 3600, 0);
        assertEquals(3600, hourly.billedSeconds(1), 1e-12);
        assertEquals(7200, hourly.billedSeconds(3601), 1e-12);
        assertEquals(3600, hourly.billedSeconds(3600.0000000001), 1e-12);
        assertEquals(0, hourly.billedSeconds(0), 1e-12);
        assertThrows(IllegalArgumentException.class, () -> new CostModel(-1, 1, 0));
        assertThrows(IllegalArgumentException.class, () -> new CostModel(1, 0, 0));
        assertThrows(IllegalArgumentException.class, () -> perSecond.billedSeconds(-1));
    }

    @Test
    void requestRecordDerivedTimes() {
        final RequestRecord r = new RequestRecord(1, 10, 10.5, 3, 12, 15, 1, 3000);
        assertTrue(r.completed());
        assertEquals(5, r.responseTime(), 1e-12);
        assertEquals(2, r.waitTime(), 1e-12);
        assertEquals(3, r.serviceTime(), 1e-12);
        assertTrue(!new RequestRecord(2, 1, 1, 3, Double.NaN, Double.NaN, 1, 2000).completed());
    }
}
