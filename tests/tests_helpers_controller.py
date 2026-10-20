"""Shared test helpers for the controller tests (not a test module)."""

from __future__ import annotations

from scalix.aws.monitoring import MockMonitor
from scalix.config import AwsSettings, LiveSettings, Mode, ScalixConfig
from scalix.sources import DataSource

T0 = 1_800_000_000.0
GROUP = "scalix-workers"
AWS = AwsSettings(profile="scalix-test", region="us-east-1", auto_scaling_group=GROUP)


def live_config(**live) -> ScalixConfig:
    return ScalixConfig(mode=Mode.LIVE, aws=AWS,
                        live=LiveSettings(enabled=True, allowed_groups=(GROUP,), **live))


def read_only_config(**live) -> ScalixConfig:
    return ScalixConfig(mode=Mode.READ_ONLY, aws=AWS, live=LiveSettings(**live))


class LiveLikeMonitor(MockMonitor):
    """Test double that labels its scripted data LIVE_AWS, to reach the apply path offline."""

    source = DataSource.LIVE_AWS
