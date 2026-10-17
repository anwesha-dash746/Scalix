"""SCALIX dashboard core: experiment execution, result loading, analysis and export.

Nothing in this package imports Streamlit, so all of it is unit-testable.
"""

WORKLOADS = ("LOW", "MEDIUM", "HIGH", "BURST", "INCREASING", "DECREASING", "FLUCTUATING")
POLICIES = ("STATIC", "THRESHOLD", "SCALIX")
