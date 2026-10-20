"""SCALIX: workload-aware auto-scaling for cost-efficient cloud resource management.

Python package for the AWS edition of SCALIX. It is being filled in phase by phase:

* ``scalix.algorithms``  scaling policies: STATIC, THRESHOLD and SCALIX (Phase 1)
* ``scalix.simulation``  workload generator and simulation engine (Phase 2)
* ``scalix.metrics``     metrics, statistics and result files (Phase 2)
* ``scalix.aws``         optional AWS adapters, READ_ONLY and LIVE modes only (Phase 4)
* ``scalix.config``      run-mode configuration; SIMULATION by default

Importing this package never imports boto3, never reads AWS credentials and never makes
network calls. The simulation and dashboard work on a machine with no AWS account at all.
"""

__version__ = "0.1.0"
