"""Read-only monitoring: CloudWatch CPU utilization and EC2 Auto Scaling group state.

This module only *reads*. It does not import the scaling algorithms and does not decide
anything. It turns AWS responses into plain, validated values, each labelled with its source
(LIVE_AWS or MOCKED), so later code can decide what to do with them.

How CPU readings are judged (the important safety rule: **missing data is never treated as 0%**):

==========  =============================================================  ===============
Status      When                                                           ``value``
==========  =============================================================  ===============
OK          newest data point is valid and younger than the max age        CPU as 0..1
STALE       newest valid point is older than ``max_metric_age_seconds``    None
MISSING     CloudWatch returned no data points                             None
MALFORMED   the newest point is unusable (not a number, outside 0-100 %,   None
            from the future, wrong unit) or the response has the wrong
            shape
ERROR       the AWS call itself failed (denied, expired credentials, ...)  None
==========  =============================================================  ===============

``value`` is only set when the status is OK. ``last_value`` keeps the newest number seen, for
display, even when it must not be used for decisions.

CPU is read with ``GetMetricStatistics`` (``AWS/EC2`` / ``CPUUtilization``, dimension
``AutoScalingGroupName``, statistic Average). ``GetMetricData`` is not used because AWS always
bills it. EC2 basic monitoring publishes every 5 minutes, so a reading is normally 5-10 minutes old.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Callable, Sequence

from scalix.config import AwsSettings, ConfigError, ScalixConfig
from scalix.sources import DataSource

CLOCK_SKEW_SECONDS = 120


class MetricStatus(str, Enum):
    OK = "OK"
    STALE = "STALE"
    MISSING = "MISSING"
    MALFORMED = "MALFORMED"
    ERROR = "ERROR"


class MonitoringError(RuntimeError):
    """Reading the group failed. ``code`` is e.g. NOT_FOUND, MALFORMED or an AWS error code."""

    def __init__(self, code: str, message: str):
        self.code, self.message = code, message
        super().__init__(f"{code}: {message}")


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


@dataclass(frozen=True)
class MetricReading:
    status: MetricStatus
    value: float | None
    last_value: float | None
    timestamp: datetime | None
    age_seconds: float | None
    reason: str
    source: DataSource
    points: int = 0

    @property
    def usable(self) -> bool:
        return self.status is MetricStatus.OK


@dataclass(frozen=True)
class InstanceInfo:
    instance_id: str
    lifecycle_state: str
    health_status: str


@dataclass(frozen=True)
class GroupState:
    name: str
    min_size: int
    max_size: int
    desired_capacity: int
    instances: tuple[InstanceInfo, ...]
    source: DataSource
    fetched_at: datetime

    @property
    def in_service(self) -> int:
        return sum(1 for i in self.instances if i.lifecycle_state == "InService")

    @property
    def pending(self) -> int:
        return sum(1 for i in self.instances if i.lifecycle_state.startswith("Pending"))


@dataclass(frozen=True)
class MonitoringSnapshot:
    fetched_at: datetime
    source: DataSource
    group: GroupState | None
    cpu: MetricReading
    errors: tuple[str, ...] = ()
    #: Requests/s is not available from EC2/ASG metrics alone (it needs a load balancer).
    demand_rps: float | None = None
    demand_note: str = "request rate not available without a load balancer metric; SCALIX's trend input is missing"

    @property
    def decision_ready(self) -> bool:
        """True only if both the group state and a fresh, valid CPU reading are available."""
        return self.group is not None and self.cpu.usable and not self.errors


def _number(x: Any) -> float | None:
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    x = float(x)
    return x if math.isfinite(x) else None


def evaluate_cpu_datapoints(datapoints: Any, now: datetime, max_age_seconds: float,
                            source: DataSource = DataSource.LIVE_AWS) -> MetricReading:
    """Judge GetMetricStatistics data points. Pure function: no AWS, easy to test."""
    def reading(status, reason, value=None, last=None, ts=None, age=None, n=0):
        return MetricReading(status, value, last, ts, age, reason, source, n)

    if not isinstance(datapoints, list):
        return reading(MetricStatus.MALFORMED, "CloudWatch response has no Datapoints list")
    if not datapoints:
        return reading(MetricStatus.MISSING, "CloudWatch returned no CPU data points for the period "
                       "(the group may have no running instances, or data is delayed); not treated as 0%")

    parsed = []
    for p in datapoints:
        ts = p.get("Timestamp") if isinstance(p, dict) else None
        if not isinstance(ts, datetime) or ts.tzinfo is None:
            return reading(MetricStatus.MALFORMED, f"data point without a valid timezone-aware Timestamp: {p!r}")
        parsed.append((ts, p))
    parsed.sort(key=lambda tp: tp[0])
    ts, newest = parsed[-1]
    age = (now - ts).total_seconds()
    avg = _number(newest.get("Average"))
    unit = newest.get("Unit", "Percent")

    if age < -CLOCK_SKEW_SECONDS:
        return reading(MetricStatus.MALFORMED, f"newest data point is {-age:.0f} s in the future; check the clock",
                       ts=ts, age=age, n=len(parsed))
    if avg is None:
        return reading(MetricStatus.MALFORMED, f"newest data point has no numeric Average: {newest!r}",
                       ts=ts, age=age, n=len(parsed))
    if unit != "Percent":
        return reading(MetricStatus.MALFORMED, f"unexpected unit {unit!r} (expected Percent)", ts=ts, age=age,
                       n=len(parsed))
    if not -1e-6 <= avg <= 100 + 1e-6:
        return reading(MetricStatus.MALFORMED, f"CPU {avg}% is outside 0-100%", ts=ts, age=age, n=len(parsed))

    fraction = min(1.0, max(0.0, avg / 100.0))
    if age > max_age_seconds:
        return reading(MetricStatus.STALE, f"newest CPU data point is {age:.0f} s old (limit {max_age_seconds:.0f} s); "
                       "not used for decisions", last=fraction, ts=ts, age=age, n=len(parsed))
    return reading(MetricStatus.OK, f"average CPU {avg:.1f}% for the period starting {ts.astimezone(timezone.utc):%H:%M:%S} UTC "
                   f"({age:.0f} s ago)", value=fraction, last=fraction, ts=ts, age=age, n=len(parsed))


def parse_group(response: Any, name: str, source: DataSource, fetched_at: datetime) -> GroupState:
    """Validate a DescribeAutoScalingGroups response for exactly the group ``name``."""
    groups = response.get("AutoScalingGroups") if isinstance(response, dict) else None
    if not isinstance(groups, list):
        raise MonitoringError("MALFORMED", "response has no AutoScalingGroups list")
    matches = [g for g in groups if isinstance(g, dict) and g.get("AutoScalingGroupName") == name]
    if not matches:
        raise MonitoringError("NOT_FOUND", f"Auto Scaling group {name!r} was not found in this region/account")
    g = matches[0]
    sizes = {}
    for key in ("MinSize", "MaxSize", "DesiredCapacity"):
        v = g.get(key)
        if isinstance(v, bool) or not isinstance(v, int) or v < 0:
            raise MonitoringError("MALFORMED", f"group field {key} is missing or invalid: {v!r}")
        sizes[key] = v
    if not sizes["MinSize"] <= sizes["DesiredCapacity"] <= sizes["MaxSize"]:
        raise MonitoringError("MALFORMED", f"inconsistent sizes min={sizes['MinSize']} "
                              f"desired={sizes['DesiredCapacity']} max={sizes['MaxSize']}")
    instances = []
    for inst in g.get("Instances", []) or []:
        if not isinstance(inst, dict) or not isinstance(inst.get("InstanceId"), str):
            raise MonitoringError("MALFORMED", f"instance entry without an InstanceId: {inst!r}")
        instances.append(InstanceInfo(inst["InstanceId"], str(inst.get("LifecycleState", "Unknown")),
                                      str(inst.get("HealthStatus", "Unknown"))))
    return GroupState(name, sizes["MinSize"], sizes["MaxSize"], sizes["DesiredCapacity"], tuple(instances),
                      source, fetched_at)


class CloudWatchMonitor:
    """Reads one Auto Scaling group's state and CPU from AWS, read-only.

    Clients can be injected (tests use Boto3's Stubber); they are wrapped with the read-only
    guard either way.
    """

    source = DataSource.LIVE_AWS

    def __init__(self, settings: AwsSettings, *, autoscaling_client=None, cloudwatch_client=None,
                 clock: Callable[[], datetime] = utc_now):
        from scalix.aws.session import guard_read_only, make_session, read_only_client

        if not settings.auto_scaling_group:
            raise ConfigError("aws.auto_scaling_group is not set; there is no group to monitor yet")
        self.settings = settings
        self.clock = clock
        if autoscaling_client is None or cloudwatch_client is None:
            session = make_session(settings)
            autoscaling_client = autoscaling_client or read_only_client(session, "autoscaling")
            cloudwatch_client = cloudwatch_client or read_only_client(session, "cloudwatch")
        self._asg = guard_read_only(autoscaling_client, "autoscaling")
        self._cw = guard_read_only(cloudwatch_client, "cloudwatch")

    @classmethod
    def from_config(cls, config: ScalixConfig, **kwargs) -> "CloudWatchMonitor":
        if not config.mode.uses_aws:
            raise ConfigError("mode is SIMULATION, which never contacts AWS. Set mode: READ_ONLY in "
                              "config/scalix.local.yaml to read from AWS.")
        return cls(config.aws, **kwargs)

    def group_state(self) -> GroupState:
        from scalix.aws.session import describe_error

        name = self.settings.auto_scaling_group
        try:
            response = self._asg.describe_auto_scaling_groups(AutoScalingGroupNames=[name])
        except Exception as exc:
            err = describe_error("autoscaling:DescribeAutoScalingGroups", exc)
            raise MonitoringError(err.code, str(err)) from None
        return parse_group(response, name, self.source, self.clock())

    def cpu(self) -> MetricReading:
        from scalix.aws.session import describe_error

        s = self.settings
        now = self.clock()
        lookback = s.max_metric_age_seconds + 2 * s.metric_period_seconds
        try:
            response = self._cw.get_metric_statistics(
                Namespace="AWS/EC2", MetricName="CPUUtilization",
                Dimensions=[{"Name": "AutoScalingGroupName", "Value": s.auto_scaling_group}],
                StartTime=now - timedelta(seconds=lookback), EndTime=now,
                Period=s.metric_period_seconds, Statistics=["Average"], Unit="Percent")
        except Exception as exc:
            err = describe_error("cloudwatch:GetMetricStatistics", exc)
            return MetricReading(MetricStatus.ERROR, None, None, None, None, str(err), self.source)
        points = response.get("Datapoints") if isinstance(response, dict) else None
        return evaluate_cpu_datapoints(points, now, s.max_metric_age_seconds, self.source)

    def snapshot(self) -> MonitoringSnapshot:
        """Group state + CPU. AWS problems are reported in ``errors``, never raised or zero-filled."""
        errors: list[str] = []
        try:
            group = self.group_state()
        except MonitoringError as exc:
            group = None
            errors.append(str(exc))
        cpu = self.cpu()
        if cpu.status is MetricStatus.ERROR:
            errors.append(cpu.reason)
        return MonitoringSnapshot(self.clock(), self.source, group, cpu, tuple(errors))


@dataclass
class MockMonitor:
    """Scripted, AWS-shaped data for demos and tests. Every value is labelled MOCKED.

    ``cpu_percents`` is played back one value per call; ``None`` simulates a missing data point
    and ``float('nan')`` a malformed one. Readings are judged by the same rules as live data.
    """

    cpu_percents: Sequence[float | None]
    desired: int = 2
    in_service: int = 2
    min_size: int = 1
    max_size: int = 2
    name: str = "mock-scalix-workers"
    clock: Callable[[], datetime] = utc_now
    reading_age_seconds: float = 360.0
    max_metric_age_seconds: float = 900.0
    _index: int = field(default=0, init=False)

    source = DataSource.MOCKED

    def group_state(self) -> GroupState:
        insts = tuple(InstanceInfo(f"i-mock{n:04d}", "InService", "Healthy") for n in range(self.in_service))
        return GroupState(self.name, self.min_size, self.max_size, self.desired, insts, self.source, self.clock())

    def cpu(self) -> MetricReading:
        value = self.cpu_percents[self._index % len(self.cpu_percents)] if self.cpu_percents else None
        self._index += 1
        now = self.clock()
        points = [] if value is None else [{"Timestamp": now - timedelta(seconds=self.reading_age_seconds),
                                            "Average": value, "Unit": "Percent"}]
        return evaluate_cpu_datapoints(points, now, self.max_metric_age_seconds, self.source)

    def snapshot(self) -> MonitoringSnapshot:
        return MonitoringSnapshot(self.clock(), self.source, self.group_state(), self.cpu())
