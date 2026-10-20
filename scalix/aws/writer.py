"""The only code in SCALIX that can change AWS: one ``SetDesiredCapacity`` call, in LIVE mode only.

``CapacityWriter`` refuses to be created unless ``mode: LIVE`` and ``live.enabled: true`` are set
and the configured group is on ``live.allowed_groups``. Its client is guarded so it can call the
read-only operations plus exactly ``autoscaling:SetDesiredCapacity``, nothing else. Before every
call it checks again: the group is the configured and allowlisted one, the new desired capacity is
within 1 and ``live.max_capacity``, and the global kill switch is off. The call uses
``HonorCooldown=True``, so AWS also enforces the group's own cooldown.

Nothing in this phase calls this against a real account. Tests use Boto3's Stubber.
"""

from __future__ import annotations

from typing import Callable

from scalix.aws.session import WRITE_OPERATIONS, guard_operations, make_session
from scalix.config import ConfigError, Mode, ScalixConfig, kill_switch_active


class LiveActionBlocked(RuntimeError):
    """A safety check right before the write failed; nothing was sent."""


class CapacityWriter:
    def __init__(self, config: ScalixConfig, *, autoscaling_client=None,
                 kill_switch: Callable[[], str | None] = kill_switch_active):
        if config.mode is not Mode.LIVE or not config.live.enabled:
            raise ConfigError("a capacity writer can only be created with mode: LIVE and live.enabled: true")
        group = config.aws.auto_scaling_group
        if not group or group not in config.live.allowed_groups:
            raise ConfigError(f"group {group!r} is not on live.allowed_groups")
        self.config = config
        self.kill_switch = kill_switch
        if autoscaling_client is None:
            from botocore.config import Config

            session = make_session(config.aws)
            autoscaling_client = session.client("autoscaling", config=Config(
                connect_timeout=5, read_timeout=15, retries={"max_attempts": 1, "mode": "standard"}))
        self._client = guard_operations(autoscaling_client, "autoscaling", WRITE_OPERATIONS, label="live-write")

    def set_desired_capacity(self, group: str, desired: int) -> None:
        cfg = self.config
        if group != cfg.aws.auto_scaling_group or group not in cfg.live.allowed_groups:
            raise LiveActionBlocked(f"group {group!r} is not the configured, allowlisted group")
        if isinstance(desired, bool) or not isinstance(desired, int) or not 1 <= desired <= cfg.live.max_capacity:
            raise LiveActionBlocked(f"desired capacity {desired!r} is outside 1..{cfg.live.max_capacity} "
                                    "(live.max_capacity)")
        reason = self.kill_switch()
        if reason:
            raise LiveActionBlocked(f"live actions are disabled: {reason}")
        self._client.set_desired_capacity(AutoScalingGroupName=group, DesiredCapacity=desired, HonorCooldown=True)
