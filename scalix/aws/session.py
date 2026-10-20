"""Creating AWS clients safely, plus error handling and redaction.

Safety rules enforced here:

1. **Explicit profile only.** A session is created only from a named profile in
   ``~/.aws/credentials`` / ``~/.aws/config``. With an explicit profile, Boto3 ignores any
   ``AWS_ACCESS_KEY_ID`` environment variables, so SCALIX never quietly uses someone else's
   credentials. A test checks this.
2. **Read-only guard.** Every client gets a hook that runs before each API call and refuses any
   operation that is not on :data:`READ_ONLY_OPERATIONS`. Write calls such as
   ``SetDesiredCapacity``, ``RunInstances`` or ``CreateStack`` raise :class:`ReadOnlyViolation`
   before anything is sent. LIVE mode (later phase) will use a separate, explicitly approved path.
3. **Fail fast.** Short timeouts and limited retries, so a network problem shows up as a clear
   error instead of a hang.
4. **No secrets in output.** :func:`redact` masks account IDs and access key IDs in any text
   SCALIX prints or saves. Secret keys and tokens are never read by SCALIX.

boto3 is imported inside functions, so ``import scalix`` works without it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from scalix.config import AwsSettings, ConfigError

#: The only AWS operations SCALIX's read-only code is allowed to call (service, operation).
READ_ONLY_OPERATIONS: frozenset[tuple[str, str]] = frozenset({
    ("sts", "GetCallerIdentity"),
    ("autoscaling", "DescribeAutoScalingGroups"),
    ("ec2", "DescribeInstances"),
    ("cloudwatch", "GetMetricStatistics"),
    ("cloudwatch", "ListMetrics"),
})

#: Operations deliberately NOT used, and why (shown in docs and the access report).
NOT_USED_OPERATIONS = {
    "cloudwatch:GetMetricData": "always billed, even on the free tier; GetMetricStatistics is used instead",
    "autoscaling:SetDesiredCapacity / UpdateAutoScalingGroup": "changes capacity; LIVE mode only, after approval",
    "ec2:RunInstances / TerminateInstances": "creates or deletes servers; never called by SCALIX",
    "cloudformation:CreateStack / DeleteStack": "creates or deletes infrastructure; manual, approved step only",
    "iam:*": "SCALIX never creates or changes permissions",
}


class ReadOnlyViolation(RuntimeError):
    """Code tried to call an AWS operation that is not on the read-only allowlist."""


class AwsAccessError(RuntimeError):
    """An AWS call failed. ``code`` is AWS's error code; ``hint`` says what to do."""

    def __init__(self, operation: str, code: str, message: str, hint: str = ""):
        self.operation, self.code, self.message, self.hint = operation, code, redact(message), hint
        super().__init__(f"{operation}: {code}: {self.message}" + (f" ({hint})" if hint else ""))


_ACCOUNT_RE = re.compile(r"(?<!\d)(\d{8})(\d{4})(?!\d)")
_KEY_ID_RE = re.compile(r"\b(?:AKIA|ASIA|AIDA|AROA)[A-Z0-9]{12,}\b")


def redact(text: Any) -> str:
    """Mask 12-digit account IDs (keep the last 4) and AWS key/role IDs in ``text``."""
    s = str(text)
    s = _KEY_ID_RE.sub("<id-redacted>", s)
    return _ACCOUNT_RE.sub(lambda m: "********" + m.group(2), s)


_HINTS = {
    "ExpiredToken": "temporary credentials expired; copy fresh ones into your profile (AWS Academy: AWS Details)",
    "ExpiredTokenException": "temporary credentials expired; copy fresh ones into your profile",
    "RequestExpired": "temporary credentials expired or the computer clock is wrong",
    "InvalidClientTokenId": "the access key in this profile is not valid",
    "UnrecognizedClientException": "the access key in this profile is not valid",
    "SignatureDoesNotMatch": "the secret key in this profile does not match the access key",
    "AuthFailure": "AWS rejected the credentials in this profile",
    "AccessDenied": "this account/role is not permitted to do this (expected on restricted lab accounts)",
    "AccessDeniedException": "this account/role is not permitted to do this",
    "UnauthorizedOperation": "this account/role is not permitted to do this",
    "OptInRequired": "this region is not enabled for the account",
}


def describe_error(operation: str, exc: BaseException) -> AwsAccessError:
    """Turn any Boto3/botocore exception into an AwsAccessError with a readable hint."""
    if isinstance(exc, AwsAccessError):
        return exc
    response = getattr(exc, "response", None)
    if isinstance(response, dict) and "Error" in response:
        code = str(response["Error"].get("Code", "Unknown"))
        message = str(response["Error"].get("Message", ""))
        return AwsAccessError(operation, code, message, _HINTS.get(code, ""))
    name = type(exc).__name__
    hints = {
        "NoCredentialsError": "no credentials found for this profile",
        "ProfileNotFound": "this profile name does not exist in ~/.aws/credentials or ~/.aws/config",
        "EndpointConnectionError": "could not reach AWS; check the internet connection and region",
        "ConnectTimeoutError": "connection to AWS timed out",
        "ReadTimeoutError": "AWS did not answer in time",
        "NoRegionError": "no region set; add aws.region to config/scalix.local.yaml",
    }
    return AwsAccessError(operation, name, str(exc), hints.get(name, ""))


@dataclass(frozen=True)
class CredentialInfo:
    """Facts about the profile's credentials, never the credentials themselves."""

    found: bool
    method: str | None      # e.g. "shared-credentials-file", "sso", "assume-role"
    temporary: bool | None  # True if a session token is present (AWS Academy uses these)


def make_session(settings: AwsSettings):
    """A Boto3 session for the configured named profile and region."""
    if not settings.profile:
        raise ConfigError("aws.profile is not set. SCALIX only uses an explicitly named AWS profile "
                          "(see config/scalix.local.example.yaml).")
    if not settings.region:
        raise ConfigError("aws.region is not set (for example us-east-1).")
    try:
        import boto3
    except ImportError:
        raise ConfigError("boto3 is not installed. Install the optional AWS extras: "
                          "pip install -r requirements-aws.txt") from None
    try:
        return boto3.Session(profile_name=settings.profile, region_name=settings.region)
    except Exception as exc:  # botocore.exceptions.ProfileNotFound and friends
        raise describe_error("open profile", exc) from None


def credential_info(session) -> CredentialInfo:
    """Whether the session has credentials, without reading or returning any secret."""
    try:
        creds = session.get_credentials()
    except Exception:
        return CredentialInfo(False, None, None)
    if creds is None:
        return CredentialInfo(False, None, None)
    method = getattr(creds, "method", None)
    token = getattr(creds, "token", None)  # inspected only for presence, never shown
    return CredentialInfo(True, method, token is not None and token != "")


def _guard_handler(service: str):
    def guard(model=None, **kwargs):
        name = getattr(model, "name", None)
        if (service, name) not in READ_ONLY_OPERATIONS:
            raise ReadOnlyViolation(
                f"{service}:{name} is not a read-only operation; SCALIX's read-only code may only call "
                f"{', '.join(sorted(f'{s}:{o}' for s, o in READ_ONLY_OPERATIONS))}")
    return guard


def guard_read_only(client, service: str):
    """Attach the read-only guard to an existing client (used for real and test clients).

    The guard runs at two points: before the request parameters are built, and again just before
    the request would be sent (``before-call``). If anything else blocks the first hook, the
    second one still stops a write before it leaves the computer.
    """
    model = client.meta.service_model
    event_id = model.service_id.hyphenize()  # botocore's event name for the service, e.g. "auto-scaling"
    handler = _guard_handler(service)
    # Handlers on the exact "<event>.<service>.<Operation>" name are the most specific, so botocore runs
    # them before any wildcard hook. Every operation that is not allowlisted gets one.
    for op in model.operation_names:
        if (service, op) not in READ_ONLY_OPERATIONS:
            client.meta.events.register_first(f"before-parameter-build.{event_id}.{op}", handler)
            client.meta.events.register_first(f"before-call.{event_id}.{op}", handler)
    # Catch-all for the service as a second line of defence (also checks allowlisted names).
    client.meta.events.register_first(f"before-call.{event_id}", handler)
    return client


def read_only_client(session, service: str):
    """A client for ``service`` that can only call allowlisted read-only operations."""
    from botocore.config import Config

    config = Config(connect_timeout=5, read_timeout=15, retries={"max_attempts": 2, "mode": "standard"})
    return guard_read_only(session.client(service, config=config), service)
