"""Optional AWS integration. This phase is **read-only**: a CloudWatch / Auto Scaling monitor and an access check.

Rules for this subpackage:

* It is used only in READ_ONLY and LIVE modes. SIMULATION mode never contacts AWS.
* boto3 is imported inside functions, never at module level, so ``import scalix`` works
  without it (boto3 is in ``requirements-aws.txt``, not ``requirements.txt``).
* Sessions come only from an explicitly named AWS profile. Credentials are never hard-coded,
  logged or written to disk by SCALIX, and account IDs are masked in output.
* Every client is wrapped by a guard that refuses any operation outside
  :data:`scalix.aws.session.READ_ONLY_OPERATIONS`.

Modules: ``session`` (safe clients, errors, redaction), ``monitoring`` (CloudWatchMonitor,
MockMonitor) and ``check_access`` (run ``python -m scalix.aws.check_access``).
"""
