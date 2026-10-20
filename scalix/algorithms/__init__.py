"""Scaling policies (STATIC, THRESHOLD, SCALIX).

Phase 1 ports these from the Java engine (``src/main/java/com/scalix/scaling``). Each policy is a
pure function of a measurement snapshot, with no clock, network or AWS access, so the same code
can drive the simulator and, later, the optional AWS controller.
"""
