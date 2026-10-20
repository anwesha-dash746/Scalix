"""Check the local AWS setup and what the account allows, using read-only calls only.

Run from the project root (needs ``pip install -r requirements-aws.txt``):

    .venv/bin/python -m scalix.aws.check_access
    .venv/bin/python -m scalix.aws.check_access --profile scalix-academy --region us-east-1

What it does:

1. Looks at the local environment: whether the AWS CLI is installed, the boto3 version, the
   *names* of configured profiles, and whether the chosen profile has credentials (and whether
   they are temporary). It never reads, prints or saves keys or tokens.
2. If a profile is configured, makes these read-only calls, one each:
   ``sts:GetCallerIdentity``, ``autoscaling:DescribeAutoScalingGroups``, ``ec2:DescribeInstances``,
   ``cloudwatch:ListMetrics`` and, if a group is configured, ``cloudwatch:GetMetricStatistics``.
   Every call goes through the read-only guard. Nothing is created, changed or deleted.
3. Prints a report and saves it to ``results/aws/access-report-<time>.json`` (git-ignored).
   Account IDs are masked to their last 4 digits, and AccessDenied errors are reported as returned.

Describe and Get calls on these services are normally not billed as resources, but SCALIX does
not promise that any AWS usage is free.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from scalix.config import PROJECT_ROOT, AwsSettings, ConfigError, load_config
from scalix.aws.session import (
    NOT_USED_OPERATIONS,
    READ_ONLY_OPERATIONS,
    credential_info,
    describe_error,
    make_session,
    read_only_client,
    redact,
)

DENIED_CODES = {"AccessDenied", "AccessDeniedException", "UnauthorizedOperation", "UnauthorizedAccess"}
CREDENTIAL_CODES = {"ExpiredToken", "ExpiredTokenException", "RequestExpired", "InvalidClientTokenId",
                    "UnrecognizedClientException", "SignatureDoesNotMatch", "AuthFailure", "NoCredentialsError",
                    "ProfileNotFound"}


@dataclass
class CheckResult:
    check: str
    status: str  # OK, DENIED, ERROR, SKIPPED, INFO, MISSING
    detail: str


@dataclass
class AccessReport:
    generated_utc: str
    environment: dict[str, Any]
    checks: list[CheckResult] = field(default_factory=list)
    calls_made: list[str] = field(default_factory=list)
    prerequisites_missing: list[str] = field(default_factory=list)
    read_only_operations: list[str] = field(
        default_factory=lambda: sorted(f"{s}:{o}" for s, o in READ_ONLY_OPERATIONS))
    not_used: dict[str, str] = field(default_factory=lambda: dict(NOT_USED_OPERATIONS))

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return json.loads(redact(json.dumps(d)))


def _mask_arn(arn: str) -> str:
    parts = redact(arn).split("/")
    if len(parts) >= 3 and ":assumed-role" in parts[0]:
        session = parts[-1]
        parts[-1] = (session[:3] + "…") if len(session) > 3 else session
    return "/".join(parts)


def environment_info(profile: str | None, which: Callable = shutil.which, run: Callable = subprocess.run) -> dict:
    env: dict[str, Any] = {}
    cli = which("aws")
    if cli:
        try:
            out = run([cli, "--version"], capture_output=True, text=True, timeout=15)
            env["aws_cli"] = (out.stdout or out.stderr).strip().split(" ")[0] or "installed"
        except Exception as exc:  # pragma: no cover - depends on the machine
            env["aws_cli"] = f"installed, but '--version' failed: {type(exc).__name__}"
    else:
        env["aws_cli"] = None
    try:
        import boto3

        env["boto3"] = boto3.__version__
        try:
            env["profiles"] = sorted(boto3.Session().available_profiles)
        except Exception as exc:
            env["profiles"] = []
            env["profiles_error"] = redact(f"{type(exc).__name__}: {exc}")
    except ImportError:
        env["boto3"] = None
        env["profiles"] = []
    env["selected_profile"] = profile
    return env


def _call(report: AccessReport, check: str, operation: str, fn: Callable[[], str]) -> str | None:
    """Run one read-only call; record OK / DENIED / ERROR. Returns the AWS error code on failure."""
    report.calls_made.append(operation)
    try:
        report.checks.append(CheckResult(check, "OK", fn()))
        return None
    except Exception as exc:
        err = describe_error(operation, exc)
        status = "DENIED" if err.code in DENIED_CODES else "ERROR"
        report.checks.append(CheckResult(check, status, redact(f"{err.code}: {err.message}"
                                                               + (f" -> {err.hint}" if err.hint else ""))))
        return err.code


def run_access_check(settings: AwsSettings, *, session=None, client_factory: Callable | None = None,
                     which: Callable = shutil.which, run: Callable = subprocess.run,
                     now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> AccessReport:
    report = AccessReport(generated_utc=now().isoformat(timespec="seconds"),
                          environment=environment_info(settings.profile, which, run))
    env = report.environment

    if env["aws_cli"] is None:
        report.checks.append(CheckResult("AWS CLI", "INFO", "not installed. Optional: boto3 is enough, the CLI "
                                         "only helps you create the profile (aws configure --profile ...)"))
    else:
        report.checks.append(CheckResult("AWS CLI", "OK", str(env["aws_cli"])))
    if env["boto3"] is None:
        report.prerequisites_missing.append("boto3: pip install -r requirements-aws.txt")
    if not settings.profile:
        report.prerequisites_missing.append("aws.profile in config/scalix.local.yaml, or --profile")
    if not settings.region:
        report.prerequisites_missing.append("aws.region in config/scalix.local.yaml, or --region")
    if settings.profile and env["boto3"] is not None and session is None and settings.profile not in env["profiles"]:
        report.prerequisites_missing.append(
            f"a profile named '{settings.profile}' in ~/.aws/credentials (found: {', '.join(env['profiles']) or 'none'})")
    if report.prerequisites_missing:
        report.checks.append(CheckResult("AWS calls", "SKIPPED",
                                         "no AWS call was made because prerequisites are missing"))
        return report

    if session is None:
        try:
            session = make_session(settings)
        except Exception as exc:
            err = describe_error("open profile", exc)
            report.checks.append(CheckResult("Profile", "ERROR", f"{err.code}: {err.message} {err.hint}".strip()))
            return report
    info = credential_info(session)
    env["credentials_found"] = info.found
    env["credential_source"] = info.method
    env["temporary_credentials"] = info.temporary
    if not info.found:
        report.prerequisites_missing.append(f"credentials for profile '{settings.profile}'")
        report.checks.append(CheckResult("Credentials", "MISSING", "the profile has no usable credentials; "
                                         "no AWS call was made"))
        return report
    report.checks.append(CheckResult("Credentials", "OK",
                                     f"found via {info.method}; " + ("temporary (session token present; these expire)"
                                                                     if info.temporary else "long-term keys")))
    report.checks.append(CheckResult("Region", "INFO", settings.region))

    factory = client_factory or (lambda service: read_only_client(session, service))

    def identity() -> str:
        r = factory("sts").get_caller_identity()
        return f"account {redact(r.get('Account', '?'))}, identity {_mask_arn(r.get('Arn', '?'))}"

    code = _call(report, "Caller identity", "sts:GetCallerIdentity", identity)
    if code in CREDENTIAL_CODES:
        report.checks.append(CheckResult("Other checks", "SKIPPED",
                                         "skipped because the credentials themselves were rejected"))
        return report

    def groups() -> str:
        kwargs = ({"AutoScalingGroupNames": [settings.auto_scaling_group]} if settings.auto_scaling_group
                  else {"MaxRecords": 10})
        r = factory("autoscaling").describe_auto_scaling_groups(**kwargs)
        found = r.get("AutoScalingGroups", [])
        if settings.auto_scaling_group:
            if not found:
                return f"permitted; group '{settings.auto_scaling_group}' NOT found in {settings.region}"
            g = found[0]
            return (f"permitted; group '{settings.auto_scaling_group}' found: min {g.get('MinSize')}, "
                    f"desired {g.get('DesiredCapacity')}, max {g.get('MaxSize')}, "
                    f"{len(g.get('Instances', []))} instance(s)")
        return f"permitted; {len(found)} group(s) visible in {settings.region}" + (" (first page)" if r.get("NextToken") else "")

    _call(report, "Describe Auto Scaling groups", "autoscaling:DescribeAutoScalingGroups", groups)

    def instances() -> str:
        r = factory("ec2").describe_instances(MaxResults=5)
        insts = [i for res in r.get("Reservations", []) for i in res.get("Instances", [])]
        states: dict[str, int] = {}
        for i in insts:
            s = i.get("State", {}).get("Name", "unknown")
            states[s] = states.get(s, 0) + 1
        summary = ", ".join(f"{n} {s}" for s, n in sorted(states.items())) or "none"
        return f"permitted; first page shows {len(insts)} instance(s): {summary}"

    _call(report, "Describe EC2 instances", "ec2:DescribeInstances", instances)

    def metrics() -> str:
        r = factory("cloudwatch").list_metrics(Namespace="AWS/EC2", MetricName="CPUUtilization")
        return f"permitted; {len(r.get('Metrics', []))} EC2 CPU metric series visible (first page)"

    _call(report, "List CloudWatch metrics", "cloudwatch:ListMetrics", metrics)

    if settings.auto_scaling_group:
        from scalix.aws.monitoring import evaluate_cpu_datapoints

        def cpu() -> str:
            t = now()
            r = factory("cloudwatch").get_metric_statistics(
                Namespace="AWS/EC2", MetricName="CPUUtilization",
                Dimensions=[{"Name": "AutoScalingGroupName", "Value": settings.auto_scaling_group}],
                StartTime=t - timedelta(seconds=settings.max_metric_age_seconds + 2 * settings.metric_period_seconds),
                EndTime=t, Period=settings.metric_period_seconds, Statistics=["Average"], Unit="Percent")
            reading = evaluate_cpu_datapoints(r.get("Datapoints"), t, settings.max_metric_age_seconds)
            return f"permitted; reading {reading.status.value}: {reading.reason}"

        _call(report, "Read CloudWatch CPU for the group", "cloudwatch:GetMetricStatistics", cpu)
    else:
        report.checks.append(CheckResult("Read CloudWatch CPU for the group", "SKIPPED",
                                         "aws.auto_scaling_group not set (no group exists yet)"))
    return report


def render(report: AccessReport) -> str:
    env = report.environment
    lines = ["SCALIX AWS access report (read-only checks)", f"Generated: {report.generated_utc}", "",
             "Environment:",
             f"  AWS CLI:            {env.get('aws_cli') or 'not installed'}",
             f"  boto3:              {env.get('boto3') or 'not installed'}",
             f"  Profiles (names):   {', '.join(env.get('profiles') or []) or 'none found'}",
             f"  Selected profile:   {env.get('selected_profile') or 'none'}"]
    if "temporary_credentials" in env:
        lines.append(f"  Temporary creds:    {env['temporary_credentials']}")
    lines += ["", "Checks:"]
    width = max((len(c.check) for c in report.checks), default=10)
    for c in report.checks:
        lines.append(f"  [{c.status:<7}] {c.check:<{width}}  {c.detail}")
    if report.prerequisites_missing:
        lines += ["", "Missing prerequisites:"] + [f"  - {p}" for p in report.prerequisites_missing]
    lines += ["", f"AWS calls made: {', '.join(report.calls_made) or 'none'}",
              "No resources were created, changed or deleted."]
    return redact("\n".join(lines))


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m scalix.aws.check_access",
                                description="Read-only check of the local AWS setup and account permissions.")
    p.add_argument("--profile", help="named AWS profile (default: aws.profile from config)")
    p.add_argument("--region", help="AWS region (default: aws.region from config)")
    p.add_argument("--group", help="Auto Scaling group name (default: aws.auto_scaling_group from config)")
    p.add_argument("--no-save", action="store_true", help="print only; do not save the JSON report")
    args = p.parse_args(argv)
    try:
        settings = load_config().aws
        overrides = {k: v for k, v in (("profile", args.profile), ("region", args.region),
                                       ("auto_scaling_group", args.group)) if v}
        settings = replace(settings, **overrides)
    except (ConfigError, ValueError) as exc:
        print(f"Invalid setting: {exc}", file=sys.stderr)
        return 2
    report = run_access_check(settings)
    print(render(report))
    if not args.no_save:
        out = PROJECT_ROOT / "results" / "aws"
        out.mkdir(parents=True, exist_ok=True)
        path = out / f"access-report-{datetime.now():%Y%m%d-%H%M%S}.json"
        path.write_text(json.dumps(report.to_dict(), indent=2), encoding="utf-8")
        print(f"\nSaved: {path.relative_to(PROJECT_ROOT)} (git-ignored)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
