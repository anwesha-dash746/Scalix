"""AWS adapter tests with Boto3's Stubber: realistic AWS responses, no network, no credentials.

Every client here is built from fake static keys ("testing") and stubbed, so nothing can reach
AWS. Skipped (with a reason) if boto3 is not installed; install it with
``pip install -r requirements-aws.txt``.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

import pytest

boto3 = pytest.importorskip("boto3", reason="boto3 not installed (pip install -r requirements-aws.txt)")
from botocore.stub import Stubber  # noqa: E402

from scalix.aws.check_access import render, run_access_check  # noqa: E402
from scalix.aws.monitoring import CloudWatchMonitor, MetricStatus, MonitoringError  # noqa: E402
from scalix.aws.session import (  # noqa: E402
    ReadOnlyViolation,
    credential_info,
    guard_read_only,
    make_session,
)
from scalix.config import AwsSettings, ConfigError, ScalixConfig  # noqa: E402
from scalix.sources import DataSource  # noqa: E402

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)
FAKE_SECRET = "FAKEsecretKEYthatMUSTneverAPPEAR0000000000"
SETTINGS = AwsSettings(profile="scalix-test", region="us-east-1", auto_scaling_group="scalix-workers")


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch, tmp_path):
    """Remove every trace of real AWS configuration for these tests."""
    import os

    for key in list(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def fake_session():
    return boto3.Session(aws_access_key_id="AKIAFAKEFAKEFAKEFAKE", aws_secret_access_key=FAKE_SECRET,
                         aws_session_token="FAKEtoken", region_name="us-east-1")


def trapped_client(service):
    """A client whose last hook before the network raises, so nothing can ever be sent."""
    client = fake_session().client(service)
    sent = []

    def trap(request, **kwargs):
        sent.append(request.url)
        raise AssertionError("request reached the network layer")

    client.meta.events.register("before-send", trap)
    return client, sent


def stubbed(service):
    client = guard_read_only(fake_session().client(service), service)
    return client, Stubber(client)


GROUP = {"AutoScalingGroups": [{
    "AutoScalingGroupName": "scalix-workers", "MinSize": 1, "MaxSize": 2, "DesiredCapacity": 1,
    "DefaultCooldown": 300, "AvailabilityZones": ["us-east-1a"], "HealthCheckType": "EC2",
    "CreatedTime": NOW - timedelta(days=1),
    "Instances": [{"InstanceId": "i-0abc", "AvailabilityZone": "us-east-1a", "LifecycleState": "InService",
                   "HealthStatus": "Healthy", "ProtectedFromScaleIn": False}],
}]}


# --------------------------------------------------------------------------- read-only guard

@pytest.mark.parametrize("service, method, kwargs", [
    ("autoscaling", "set_desired_capacity", {"AutoScalingGroupName": "g", "DesiredCapacity": 2}),
    ("autoscaling", "update_auto_scaling_group", {"AutoScalingGroupName": "g", "MaxSize": 5}),
    ("autoscaling", "delete_auto_scaling_group", {"AutoScalingGroupName": "g"}),
    ("ec2", "run_instances", {"ImageId": "ami-1", "MinCount": 1, "MaxCount": 1}),
    ("ec2", "terminate_instances", {"InstanceIds": ["i-1"]}),
    ("cloudwatch", "get_metric_data", {"MetricDataQueries": [], "StartTime": NOW, "EndTime": NOW}),
    ("cloudwatch", "put_metric_data", {"Namespace": "x", "MetricData": []}),
    ("cloudformation", "create_stack", {"StackName": "s", "TemplateBody": "{}"}),
    ("iam", "create_role", {"RoleName": "r", "AssumeRolePolicyDocument": "{}"}),
])
def test_guard_blocks_every_write_or_billed_call_before_it_is_sent(service, method, kwargs):
    client, sent = trapped_client(service)
    guard_read_only(client, service)
    with pytest.raises(ReadOnlyViolation):
        getattr(client, method)(**kwargs)
    assert sent == []  # nothing reached the network layer


def test_without_the_guard_the_trap_proves_a_request_would_be_sent():
    """Control experiment: the trap really fires for an unguarded client (still fully offline)."""
    client, sent = trapped_client("autoscaling")
    with pytest.raises(AssertionError, match="network layer"):
        client.set_desired_capacity(AutoScalingGroupName="g", DesiredCapacity=2)
    assert len(sent) == 1


def test_guarded_reads_pass_the_guard_and_reach_the_trap():
    client, sent = trapped_client("autoscaling")
    guard_read_only(client, "autoscaling")
    with pytest.raises(AssertionError, match="network layer"):
        client.describe_auto_scaling_groups(AutoScalingGroupNames=["g"])
    assert len(sent) == 1


def test_guard_allows_reads():
    client, stub = stubbed("autoscaling")
    stub.add_response("describe_auto_scaling_groups", GROUP, {"AutoScalingGroupNames": ["scalix-workers"]})
    stub.activate()
    assert client.describe_auto_scaling_groups(AutoScalingGroupNames=["scalix-workers"])["AutoScalingGroups"]


# --------------------------------------------------------------------------- monitor

def monitor(asg_stub_setup=None, cw_stub_setup=None):
    asg, asg_stub = stubbed("autoscaling")
    cw, cw_stub = stubbed("cloudwatch")
    if asg_stub_setup:
        asg_stub_setup(asg_stub)
    if cw_stub_setup:
        cw_stub_setup(cw_stub)
    asg_stub.activate()
    cw_stub.activate()
    m = CloudWatchMonitor(SETTINGS, autoscaling_client=asg, cloudwatch_client=cw, clock=lambda: NOW)
    return m, asg_stub, cw_stub


EXPECTED_CPU_REQUEST = {
    "Namespace": "AWS/EC2", "MetricName": "CPUUtilization",
    "Dimensions": [{"Name": "AutoScalingGroupName", "Value": "scalix-workers"}],
    "StartTime": NOW - timedelta(seconds=900 + 600), "EndTime": NOW, "Period": 300,
    "Statistics": ["Average"], "Unit": "Percent",
}


def cpu_response(*points):
    return {"Label": "CPUUtilization", "Datapoints": [
        {"Timestamp": NOW - timedelta(seconds=age), "Average": avg, "Unit": "Percent"} for age, avg in points]}


def test_snapshot_reads_group_and_cpu_with_exact_requests():
    m, a, c = monitor(
        lambda s: s.add_response("describe_auto_scaling_groups", GROUP, {"AutoScalingGroupNames": ["scalix-workers"]}),
        lambda s: s.add_response("get_metric_statistics", cpu_response((660, 30.0), (360, 55.0)), EXPECTED_CPU_REQUEST))
    snap = m.snapshot()
    a.assert_no_pending_responses()
    c.assert_no_pending_responses()
    assert snap.source is DataSource.LIVE_AWS and snap.decision_ready
    assert snap.group.desired_capacity == 1 and snap.group.in_service == 1
    assert snap.cpu.status is MetricStatus.OK and snap.cpu.value == pytest.approx(0.55)


def test_empty_cpu_data_is_missing_not_zero():
    m, *_ = monitor(cw_stub_setup=lambda s: s.add_response("get_metric_statistics", cpu_response(),
                                                           EXPECTED_CPU_REQUEST))
    r = m.cpu()
    assert r.status is MetricStatus.MISSING and r.value is None


def test_delayed_cpu_data_is_stale():
    m, *_ = monitor(cw_stub_setup=lambda s: s.add_response("get_metric_statistics", cpu_response((1400, 20.0)),
                                                           EXPECTED_CPU_REQUEST))
    r = m.cpu()
    assert r.status is MetricStatus.STALE and r.value is None and r.last_value == pytest.approx(0.2)


def test_access_denied_is_an_error_reading_not_zero():
    def deny(s):
        s.add_client_error("get_metric_statistics", service_error_code="AccessDenied",
                           service_message="User arn:aws:sts::123456789012:assumed-role/voclabs/x is not authorized",
                           http_status_code=403)
    m, *_ = monitor(cw_stub_setup=deny)
    r = m.cpu()
    assert r.status is MetricStatus.ERROR and r.value is None
    assert "AccessDenied" in r.reason and "123456789012" not in r.reason


def test_group_not_found_and_errors_are_reported_not_raised_by_snapshot():
    def missing(s):
        s.add_response("describe_auto_scaling_groups", {"AutoScalingGroups": []},
                       {"AutoScalingGroupNames": ["scalix-workers"]})
    m, *_ = monitor(missing, lambda s: s.add_response("get_metric_statistics", cpu_response((300, 40.0)),
                                                      EXPECTED_CPU_REQUEST))
    snap = m.snapshot()
    assert snap.group is None and not snap.decision_ready
    assert any("NOT_FOUND" in e for e in snap.errors)


def test_group_state_raises_on_expired_credentials():
    def expired(s):
        s.add_client_error("describe_auto_scaling_groups", service_error_code="ExpiredToken",
                           service_message="The security token included in the request is expired")
    m, *_ = monitor(expired)
    with pytest.raises(MonitoringError) as e:
        m.group_state()
    assert e.value.code == "ExpiredToken"


def test_monitor_refuses_simulation_mode_and_missing_group():
    with pytest.raises(ConfigError, match="SIMULATION"):
        CloudWatchMonitor.from_config(ScalixConfig())
    with pytest.raises(ConfigError, match="auto_scaling_group"):
        CloudWatchMonitor(AwsSettings(profile="p", region="us-east-1"))


# --------------------------------------------------------------------------- sessions and credentials

def test_session_needs_an_explicit_profile_and_region():
    with pytest.raises(ConfigError, match="profile"):
        make_session(AwsSettings(region="us-east-1"))
    with pytest.raises(ConfigError, match="region"):
        make_session(AwsSettings(profile="p"))


def test_explicit_profile_ignores_environment_credentials(monkeypatch, tmp_path):
    (tmp_path / "credentials").write_text(
        "[scalix-test]\naws_access_key_id = AKIAPROFILEPROFILE00\naws_secret_access_key = profile-secret\n"
        "aws_session_token = profile-token\n")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "AKIAENVIRONMENT00000")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "environment-secret")
    session = make_session(SETTINGS)
    creds = session.get_credentials()
    assert creds.access_key == "AKIAPROFILEPROFILE00"  # the named profile, not the environment
    info = credential_info(session)
    assert info.found and info.temporary and info.method == "shared-credentials-file"


def test_unknown_profile_is_a_clear_error():
    with pytest.raises(Exception) as e:
        make_session(AwsSettings(profile="does-not-exist", region="us-east-1"))
    assert "ProfileNotFound" in str(e.value)


# --------------------------------------------------------------------------- access check

def access_factory(setups):
    clients = {}
    for service, setup in setups.items():
        client, stub = stubbed(service)
        setup(stub)
        stub.activate()
        clients[service] = client
    return lambda service: clients[service]


IDENTITY = {"UserId": "AROAFAKEFAKEFAKE:user", "Account": "123456789012",
            "Arn": "arn:aws:sts::123456789012:assumed-role/voclabs/user3456789=Praneel"}


def full_setups(group=True):
    setups = {
        "sts": lambda s: s.add_response("get_caller_identity", IDENTITY, {}),
        "autoscaling": lambda s: s.add_response("describe_auto_scaling_groups", GROUP,
                                                {"AutoScalingGroupNames": ["scalix-workers"]} if group
                                                else {"MaxRecords": 10}),
        "ec2": lambda s: s.add_response("describe_instances", {"Reservations": [{"Instances": [
            {"InstanceId": "i-0abc", "State": {"Name": "running", "Code": 16}}]}]}, {"MaxResults": 5}),
    }

    def cw(s):
        s.add_response("list_metrics", {"Metrics": [{"Namespace": "AWS/EC2", "MetricName": "CPUUtilization"}]},
                       {"Namespace": "AWS/EC2", "MetricName": "CPUUtilization"})
        if group:
            s.add_response("get_metric_statistics", cpu_response((360, 40.0)), EXPECTED_CPU_REQUEST)
    setups["cloudwatch"] = cw
    return setups


def run_check(setups, settings=SETTINGS):
    return run_access_check(settings, session=fake_session(), client_factory=access_factory(setups),
                            which=lambda _: None, now=lambda: NOW)


def test_access_check_full_read_only_report():
    report = run_check(full_setups())
    by = {c.check: c for c in report.checks}
    assert by["Caller identity"].status == "OK"
    assert "********9012" in by["Caller identity"].detail and "123456789012" not in by["Caller identity"].detail
    assert "Praneel" not in by["Caller identity"].detail  # session name truncated
    assert by["Describe Auto Scaling groups"].status == "OK" and "found" in by["Describe Auto Scaling groups"].detail
    assert by["Describe EC2 instances"].status == "OK" and "1 running" in by["Describe EC2 instances"].detail
    assert by["List CloudWatch metrics"].status == "OK"
    assert by["Read CloudWatch CPU for the group"].status == "OK"
    assert report.calls_made == ["sts:GetCallerIdentity", "autoscaling:DescribeAutoScalingGroups",
                                 "ec2:DescribeInstances", "cloudwatch:ListMetrics", "cloudwatch:GetMetricStatistics"]
    assert by["Credentials"].detail.startswith("found via explicit") or "temporary" in by["Credentials"].detail


def test_access_check_without_a_group_skips_the_cpu_read():
    report = run_check(full_setups(group=False), AwsSettings(profile="scalix-test", region="us-east-1"))
    by = {c.check: c for c in report.checks}
    assert by["Read CloudWatch CPU for the group"].status == "SKIPPED"
    assert "cloudwatch:GetMetricStatistics" not in report.calls_made


def test_access_check_reports_denials_honestly():
    setups = full_setups()
    setups["ec2"] = lambda s: s.add_client_error("describe_instances", "UnauthorizedOperation",
                                                 "You are not authorized to perform this operation.", 403)
    setups["autoscaling"] = lambda s: s.add_client_error("describe_auto_scaling_groups", "AccessDenied",
                                                         "User: arn:aws:sts::123456789012:x is not authorized", 403)
    report = run_check(setups)
    by = {c.check: c for c in report.checks}
    assert by["Describe EC2 instances"].status == "DENIED" and "UnauthorizedOperation" in by["Describe EC2 instances"].detail
    assert by["Describe Auto Scaling groups"].status == "DENIED"
    assert by["List CloudWatch metrics"].status == "OK"  # other checks still run
    assert "123456789012" not in render(report)


def test_access_check_stops_after_rejected_credentials():
    setups = {"sts": lambda s: s.add_client_error("get_caller_identity", "ExpiredToken", "token expired", 403)}
    report = run_check(setups)
    by = {c.check: c for c in report.checks}
    assert by["Caller identity"].status == "ERROR" and "expired" in by["Caller identity"].detail
    assert by["Other checks"].status == "SKIPPED"
    assert report.calls_made == ["sts:GetCallerIdentity"]


def test_no_secret_ever_appears_in_the_report():
    report = run_check(full_setups())
    text = render(report) + json.dumps(report.to_dict())
    for secret in (FAKE_SECRET, "FAKEtoken", "AKIAFAKEFAKEFAKEFAKE", "AROAFAKEFAKEFAKE", "123456789012"):
        assert secret not in text


def test_access_check_lists_unsupported_operations():
    report = run_check(full_setups())
    assert any("GetMetricData" in k for k in report.not_used)
    assert all(op.split(":")[1].startswith(("Describe", "Get", "List")) for op in report.read_only_operations)
