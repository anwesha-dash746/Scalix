"""The real CapacityWriter with Boto3's Stubber: exact request, AWS errors, and its write guard.

Offline: fake static keys, stubbed responses, and a network trap. Skipped without boto3.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone

import pytest

boto3 = pytest.importorskip("boto3", reason="boto3 not installed (pip install -r requirements-aws.txt)")
from botocore.stub import Stubber  # noqa: E402

from scalix.aws.audit import AuditLog  # noqa: E402
from scalix.aws.controller import Controller, Outcome  # noqa: E402
from scalix.aws.session import OperationNotAllowed, ReadOnlyViolation, guard_read_only  # noqa: E402
from scalix.aws.writer import CapacityWriter  # noqa: E402
from tests_helpers_controller import GROUP, T0, LiveLikeMonitor, live_config  # noqa: E402


@pytest.fixture(autouse=True)
def no_real_aws(monkeypatch, tmp_path):
    for key in list(os.environ):
        if key.startswith("AWS_"):
            monkeypatch.delenv(key)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("AWS_CONFIG_FILE", str(tmp_path / "config"))
    monkeypatch.setenv("AWS_SHARED_CREDENTIALS_FILE", str(tmp_path / "credentials"))
    monkeypatch.setenv("AWS_EC2_METADATA_DISABLED", "true")


def raw_client():
    return boto3.Session(aws_access_key_id="testing", aws_secret_access_key="testing",
                         region_name="us-east-1").client("autoscaling")


def trapped():
    client, sent = raw_client(), []

    def trap(request, **kwargs):
        sent.append(request.url)
        raise AssertionError("request reached the network layer")

    client.meta.events.register("before-send", trap)
    return client, sent


EXPECTED = {"AutoScalingGroupName": GROUP, "DesiredCapacity": 2, "HonorCooldown": True}


def stubbed_writer(setup):
    client = raw_client()
    stub = Stubber(client)
    setup(stub)
    stub.activate()
    writer = CapacityWriter(live_config(), autoscaling_client=client, kill_switch=lambda: None)
    return writer, stub


def run_controller(tmp_path, writer):
    mon = LiveLikeMonitor([85.0], desired=1, in_service=1, min_size=1, max_size=2, name=GROUP,
                          clock=lambda: datetime.fromtimestamp(T0, timezone.utc))
    c = Controller(live_config(), mon, audit=AuditLog(tmp_path / "audit.jsonl"), writer_factory=lambda: writer,
                   clock=lambda: T0, kill_switch=lambda: None, on_ec2=lambda: False,
                   lock_path=tmp_path / "controller.lock")
    return c.tick(apply=True, confirm_group=GROUP)


def test_writer_sends_exactly_one_set_desired_capacity_with_honor_cooldown(tmp_path):
    writer, stub = stubbed_writer(lambda s: s.add_response("set_desired_capacity", {}, EXPECTED))
    r = run_controller(tmp_path, writer)
    assert r.outcome is Outcome.APPLIED
    stub.assert_no_pending_responses()


@pytest.mark.parametrize("code, status", [("ScalingActivityInProgress", 400), ("ResourceContention", 500),
                                          ("ValidationError", 400), ("AccessDenied", 403)])
def test_aws_error_responses_are_failed_not_retried(tmp_path, code, status):
    writer, stub = stubbed_writer(lambda s: s.add_client_error("set_desired_capacity", code, "refused", status,
                                                               expected_params=EXPECTED))
    r = run_controller(tmp_path, writer)
    assert r.outcome is Outcome.FAILED and code in r.reason
    stub.assert_no_pending_responses()  # exactly one attempt was consumed; a retry would raise


@pytest.mark.parametrize("method, kwargs", [
    ("update_auto_scaling_group", {"AutoScalingGroupName": GROUP, "MaxSize": 10}),
    ("delete_auto_scaling_group", {"AutoScalingGroupName": GROUP}),
    ("terminate_instance_in_auto_scaling_group", {"InstanceId": "i-1", "ShouldDecrementDesiredCapacity": True}),
    ("set_instance_protection", {"InstanceIds": ["i-1"], "AutoScalingGroupName": GROUP,
                                 "ProtectedFromScaleIn": True}),
    ("put_scaling_policy", {"AutoScalingGroupName": GROUP, "PolicyName": "p"}),
])
def test_writer_client_may_only_set_desired_capacity(method, kwargs):
    client, sent = trapped()
    writer = CapacityWriter(live_config(), autoscaling_client=client, kill_switch=lambda: None)
    with pytest.raises(OperationNotAllowed):
        getattr(writer._client, method)(**kwargs)
    assert sent == []


def test_writer_set_desired_capacity_passes_its_guard():
    client, sent = trapped()
    writer = CapacityWriter(live_config(), autoscaling_client=client, kill_switch=lambda: None)
    with pytest.raises(AssertionError, match="network layer"):
        writer.set_desired_capacity(GROUP, 2)
    assert len(sent) == 1  # it would have been sent; the trap stopped it


def test_read_only_clients_still_cannot_set_capacity():
    client, sent = trapped()
    guard_read_only(client, "autoscaling")
    with pytest.raises(ReadOnlyViolation):
        client.set_desired_capacity(AutoScalingGroupName=GROUP, DesiredCapacity=2)
    assert sent == []
