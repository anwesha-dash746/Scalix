"""AWS adapter logic that needs neither boto3 nor credentials: metric judging, group parsing,
redaction, error mapping, mock monitor and data-source labels."""

from __future__ import annotations

import math
from datetime import datetime, timedelta, timezone

import pytest

from scalix.aws.monitoring import (
    GroupState,
    MetricStatus,
    MockMonitor,
    MonitoringError,
    evaluate_cpu_datapoints,
    parse_group,
)
from scalix.aws.session import NOT_USED_OPERATIONS, READ_ONLY_OPERATIONS, describe_error, redact
from scalix.sources import DataSource

NOW = datetime(2026, 10, 20, 12, 0, 0, tzinfo=timezone.utc)


def point(age_s: float, avg, unit="Percent"):
    return {"Timestamp": NOW - timedelta(seconds=age_s), "Average": avg, "Unit": unit}


def judge(points, max_age=900):
    return evaluate_cpu_datapoints(points, NOW, max_age)


# --------------------------------------------------------------------------- CPU readings

class TestCpuReadings:
    def test_fresh_reading_is_ok_and_converted_to_a_fraction(self):
        r = judge([point(360, 42.5)])
        assert r.status is MetricStatus.OK and r.usable
        assert r.value == pytest.approx(0.425) and r.age_seconds == 360
        assert r.source is DataSource.LIVE_AWS

    def test_newest_point_is_used_even_if_unsorted(self):
        r = judge([point(360, 80.0), point(960, 10.0), point(660, 50.0)])
        assert r.value == pytest.approx(0.80) and r.points == 3

    def test_no_data_is_missing_not_zero(self):
        r = judge([])
        assert r.status is MetricStatus.MISSING
        assert r.value is None and r.last_value is None
        assert "not treated as 0%" in r.reason

    def test_old_data_is_stale_and_not_usable(self):
        r = judge([point(1200, 35.0)])
        assert r.status is MetricStatus.STALE and not r.usable
        assert r.value is None and r.last_value == pytest.approx(0.35)

    def test_exactly_at_the_age_limit_is_still_ok(self):
        assert judge([point(900, 35.0)]).status is MetricStatus.OK

    @pytest.mark.parametrize("points, fragment", [
        ("not a list", "Datapoints"),
        ([{"Average": 5.0}], "Timestamp"),
        ([{"Timestamp": NOW.replace(tzinfo=None), "Average": 5.0}], "Timestamp"),
        ([point(60, math.nan)], "numeric"),
        ([point(60, "12")], "numeric"),
        ([point(60, True)], "numeric"),
        ([point(60, None)], "numeric"),
        ([point(60, 150.0)], "outside"),
        ([point(60, -3.0)], "outside"),
        ([point(60, 40.0, unit="Count")], "unit"),
        ([point(-600, 40.0)], "future"),
        (["junk"], "Timestamp"),
    ])
    def test_malformed_data_is_never_used(self, points, fragment):
        r = judge(points)
        assert r.status is MetricStatus.MALFORMED and r.value is None
        assert fragment in r.reason

    def test_malformed_newest_point_is_not_replaced_by_an_older_good_one(self):
        r = judge([point(600, 40.0), point(60, math.nan)])
        assert r.status is MetricStatus.MALFORMED

    def test_small_clock_skew_is_tolerated(self):
        assert judge([point(-60, 40.0)]).status is MetricStatus.OK

    def test_boundaries_0_and_100_percent(self):
        assert judge([point(60, 0.0)]).value == 0.0
        assert judge([point(60, 100.0)]).value == 1.0


# --------------------------------------------------------------------------- group state

def group_response(**overrides):
    g = {"AutoScalingGroupName": "scalix-workers", "MinSize": 1, "MaxSize": 2, "DesiredCapacity": 1,
         "Instances": [{"InstanceId": "i-0abc", "LifecycleState": "InService", "HealthStatus": "Healthy"},
                       {"InstanceId": "i-0def", "LifecycleState": "Pending", "HealthStatus": "Healthy"}]}
    g.update(overrides)
    return {"AutoScalingGroups": [g]}


class TestGroupParsing:
    def test_valid_group(self):
        g = parse_group(group_response(), "scalix-workers", DataSource.LIVE_AWS, NOW)
        assert (g.min_size, g.desired_capacity, g.max_size) == (1, 1, 2)
        assert g.in_service == 1 and g.pending == 1 and g.source is DataSource.LIVE_AWS

    def test_other_groups_are_ignored(self):
        with pytest.raises(MonitoringError) as e:
            parse_group(group_response(AutoScalingGroupName="someone-else"), "scalix-workers",
                        DataSource.LIVE_AWS, NOW)
        assert e.value.code == "NOT_FOUND"

    @pytest.mark.parametrize("response", [
        {}, {"AutoScalingGroups": "x"}, group_response(MinSize=None), group_response(MaxSize=-1),
        group_response(DesiredCapacity=5), group_response(Instances=[{"LifecycleState": "InService"}]),
        group_response(DesiredCapacity=True),
    ])
    def test_malformed_group_responses(self, response):
        with pytest.raises(MonitoringError) as e:
            parse_group(response, "scalix-workers", DataSource.LIVE_AWS, NOW)
        assert e.value.code in {"MALFORMED", "NOT_FOUND"}


# --------------------------------------------------------------------------- redaction, errors

class TestRedactionAndErrors:
    def test_account_ids_are_masked(self):
        text = "arn:aws:sts::123456789012:assumed-role/voclabs/user"
        assert redact(text) == "arn:aws:sts::********9012:assumed-role/voclabs/user"

    def test_key_ids_are_removed(self):
        assert "AKIA" not in redact("key AKIAIOSFODNN7EXAMPLE used")
        assert "ASIA" not in redact("ASIAXXXXXXXXXXXXXXXX")

    def test_short_numbers_are_kept(self):
        assert redact("CPU 42 % at 12:00, 300 s") == "CPU 42 % at 12:00, 300 s"

    def test_client_error_mapping(self):
        class FakeClientError(Exception):
            response = {"Error": {"Code": "ExpiredToken", "Message": "token for 123456789012 expired"}}

        err = describe_error("sts:GetCallerIdentity", FakeClientError())
        assert err.code == "ExpiredToken" and "expired" in err.hint
        assert "123456789012" not in str(err)

    def test_other_exception_mapping(self):
        class EndpointConnectionError(Exception):
            pass

        err = describe_error("ec2:DescribeInstances", EndpointConnectionError("no network"))
        assert err.code == "EndpointConnectionError" and "internet" in err.hint

    def test_allowlist_is_read_only(self):
        for _, op in READ_ONLY_OPERATIONS:
            assert op.startswith(("Describe", "Get", "List"))
        assert ("cloudwatch", "GetMetricData") not in READ_ONLY_OPERATIONS  # always billed
        assert any("GetMetricData" in k for k in NOT_USED_OPERATIONS)


# --------------------------------------------------------------------------- mock monitor + labels

class TestMockMonitorAndLabels:
    def test_mock_values_are_labelled_mocked_and_judged_like_live_data(self):
        m = MockMonitor([40.0, None, math.nan, 90.0], clock=lambda: NOW)
        statuses = [m.snapshot() for _ in range(4)]
        assert all(s.source is DataSource.MOCKED and s.cpu.source is DataSource.MOCKED for s in statuses)
        assert all(s.group.source is DataSource.MOCKED for s in statuses)
        assert [s.cpu.status for s in statuses] == [MetricStatus.OK, MetricStatus.MISSING,
                                                    MetricStatus.MALFORMED, MetricStatus.OK]
        assert statuses[0].decision_ready and not statuses[1].decision_ready
        assert statuses[1].cpu.value is None  # missing is not 0

    def test_mock_stale_reading(self):
        m = MockMonitor([50.0], clock=lambda: NOW, reading_age_seconds=2000)
        assert m.cpu().status is MetricStatus.STALE

    def test_snapshot_reports_that_demand_is_unavailable(self):
        s = MockMonitor([50.0], clock=lambda: NOW).snapshot()
        assert s.demand_rps is None and "load balancer" in s.demand_note

    def test_every_source_has_a_human_label(self):
        assert DataSource.SIMULATED.label.startswith("Simulated")
        assert DataSource.MOCKED.label.startswith("Mocked")
        assert DataSource.LIVE_AWS.label == "Live AWS data"

    def test_simulator_outputs_are_labelled_simulated(self):
        from scalix.algorithms import ThresholdPolicy
        from scalix.simulation import WorkloadSpec, generate, simulate

        r = simulate(ThresholdPolicy(), generate(WorkloadSpec(duration_seconds=120)))
        assert (r.timeseries["data_source"] == "SIMULATED").all()
        assert (r.events["data_source"] == "SIMULATED").all()
        assert r.summary["data_source"] == "SIMULATED"

    def test_group_state_counts(self):
        g = MockMonitor([10.0], in_service=2, desired=2, clock=lambda: NOW).group_state()
        assert isinstance(g, GroupState) and g.in_service == 2 and g.pending == 0
