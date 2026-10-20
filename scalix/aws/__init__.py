"""Optional AWS integration (Phase 4): CloudWatch reader and EC2 Auto Scaling adapter.

Rules for this subpackage:

* It is used only in READ_ONLY and LIVE modes. SIMULATION mode never imports it.
* boto3 is imported inside functions, never at module level, so ``import scalix`` works
  without boto3 installed (it is listed in ``requirements-aws.txt``, not ``requirements.txt``).
* Credentials come from Boto3's normal lookup (a named AWS CLI profile or temporary
  credentials). They are never hard-coded, logged or written to disk by SCALIX.
"""
