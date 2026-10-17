package com.scalix.scaling;

import org.junit.jupiter.api.Test;

import java.util.Arrays;
import java.util.List;

import static org.junit.jupiter.api.Assertions.assertEquals;
import static org.junit.jupiter.api.Assertions.assertThrows;
import static org.junit.jupiter.api.Assertions.assertTrue;

class TrendAnalyzerTest {

    @Test
    void risingSeriesIsRising() {
        final var e = TrendAnalyzer.estimate(List.of(4.0, 5.0, 6.0, 7.0, 8.0), 5, 0.03);
        assertEquals(Trend.RISING, e.trend());
        // slope 1 per interval, mean 6 -> relative slope 1/6
        assertEquals(1.0 / 6.0, e.relativeSlope(), 1e-12);
        assertEquals(5, e.samples());
    }

    @Test
    void fallingSeriesIsFalling() {
        final var e = TrendAnalyzer.estimate(List.of(10.0, 9.0, 8.0, 7.0), 4, 0.03);
        assertEquals(Trend.FALLING, e.trend());
        assertTrue(e.relativeSlope() < 0);
    }

    @Test
    void flatOrSmallChangeIsStable() {
        assertEquals(Trend.STABLE, TrendAnalyzer.estimate(List.of(5.0, 5.0, 5.0, 5.0), 4, 0.03).trend());
        // slope 0.1 on mean ~5 = 2% < 3% threshold
        assertEquals(Trend.STABLE, TrendAnalyzer.estimate(List.of(4.85, 4.95, 5.05, 5.15), 4, 0.03).trend());
    }

    @Test
    void tooFewSamplesIsUnknown() {
        assertEquals(Trend.UNKNOWN, TrendAnalyzer.estimate(List.of(), 4, 0.03).trend());
        assertEquals(Trend.UNKNOWN, TrendAnalyzer.estimate(List.of(3.0), 4, 0.03).trend());
        assertEquals(Trend.UNKNOWN, TrendAnalyzer.estimate(null, 4, 0.03).trend());
    }

    @Test
    void onlyTheObservationWindowIsUsed() {
        // Long rise followed by a fall: a 3-sample window sees only the fall.
        final List<Double> s = Arrays.asList(1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 5.0, 4.0);
        assertEquals(Trend.FALLING, TrendAnalyzer.estimate(s, 3, 0.03).trend());
        assertEquals(Trend.RISING, TrendAnalyzer.estimate(s, 8, 0.03).trend());
    }

    @Test
    void zeroMeanSeriesIsHandled() {
        assertEquals(Trend.STABLE, TrendAnalyzer.estimate(List.of(0.0, 0.0, 0.0), 3, 0.03).trend());
        final var rising = TrendAnalyzer.estimate(List.of(-1.0, 0.0, 1.0), 3, 0.03);
        assertEquals(Trend.RISING, rising.trend());
        assertTrue(Double.isInfinite(rising.relativeSlope()));
    }

    @Test
    void invalidArgumentsRejected() {
        assertThrows(IllegalArgumentException.class, () -> TrendAnalyzer.estimate(List.of(1.0, 2.0), 1, 0.03));
        assertThrows(IllegalArgumentException.class, () -> TrendAnalyzer.estimate(List.of(1.0, 2.0), 3, -0.1));
        assertThrows(IllegalArgumentException.class, () -> TrendAnalyzer.estimate(List.of(1.0, 2.0), 3, Double.NaN));
        assertThrows(IllegalArgumentException.class, () -> TrendAnalyzer.estimate(List.of(1.0, Double.NaN), 3, 0.03));
    }
}
