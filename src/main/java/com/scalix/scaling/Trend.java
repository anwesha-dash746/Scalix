package com.scalix.scaling;

/** Direction of recent demand, as estimated by {@link TrendAnalyzer}. */
public enum Trend {
    RISING,
    FALLING,
    STABLE,
    /** Not enough history in the observation window to estimate a trend. */
    UNKNOWN
}
