"""Approving one live scaling change from the dashboard, with the same safety as the command line.

The dashboard never builds a writer or calls the controller's apply path itself; it calls the two
functions here. The rules (also tested in ``tests/test_approval.py`` and ``tests/test_ui.py``):

1. **Only when the config file already says LIVE.** ``mode: LIVE`` and ``live.enabled: true`` must be
   set in ``config/scalix.local.yaml``; nothing here can change the config.
2. **A proposal first.** :func:`get_proposal` runs one dry run on live data. Only a ``DRY_RUN``
   outcome (a change that passed every safety check) can be approved.
3. **Explicit approval.** :func:`approve` needs the exact group name typed by the operator and a
   ticked acknowledgement.
4. **Fresh and unchanged.** The proposal must be at most ``PROPOSAL_MAX_AGE_SECONDS`` old, and at the
   moment of approval the controller re-reads AWS and applies only if it proposes exactly the same
   change (``expected`` live gate). Every other controller gate still applies: allowlist, approved
   maximum, step, cooldown, duplicate, kill switch, LIVE_AWS data, one call, no retry.
5. **Kill switch on afterwards.** Once the write was attempted (APPLIED or FAILED), the kill switch
   file is created, so a second click can't change anything. This module can turn the kill switch on,
   never off.
6. **Recorded.** The audit log marks the change ``approved_via: dashboard``.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from scalix.aws.audit import AuditLog
from scalix.aws.controller import Controller, ControllerResult, Outcome
from scalix.config import KILL_SWITCH_PATH, Mode, ScalixConfig

PROPOSAL_MAX_AGE_SECONDS = 120
APPROVED_VIA = "dashboard"


@dataclass(frozen=True)
class Proposal:
    group: str
    desired_before: int
    desired_proposed: int
    created_epoch: float
    result: ControllerResult

    def age(self, now: float) -> float:
        return now - self.created_epoch


def live_approval_enabled(config: ScalixConfig) -> bool:
    """True only if the config file itself enables live changes for a configured group."""
    return config.mode is Mode.LIVE and config.live.enabled and bool(config.aws.auto_scaling_group)


def get_proposal(config: ScalixConfig, monitor: Any, audit: AuditLog, *,
                 clock: Callable[[], float] = time.time) -> tuple[ControllerResult, Proposal | None]:
    """One dry run on live data. Returns the result, and a Proposal if there is a change to approve."""
    result = Controller(config, monitor, audit=audit, clock=clock).tick()
    if result.outcome is not Outcome.DRY_RUN or result.desired_before is None or result.desired_proposed is None:
        return result, None
    return result, Proposal(group=result.group, desired_before=result.desired_before,
                            desired_proposed=result.desired_proposed, created_epoch=clock(), result=result)


def engage_kill_switch(path: Path | None = None) -> Path:
    """Create the kill switch file (the safe direction only)."""
    file = KILL_SWITCH_PATH if path is None else Path(path)
    file.parent.mkdir(parents=True, exist_ok=True)
    file.touch()
    return file


def approve(config: ScalixConfig, proposal: Proposal, typed_group: str, acknowledged: bool, *,
            monitor: Any, audit: AuditLog, writer_factory: Callable[[], Any] | None = None,
            clock: Callable[[], float] = time.time, kill_switch_path: Path | None = None,
            **controller_kw: Any) -> ControllerResult | str:
    """Apply exactly the approved proposal, once. Returns the controller result, or a refusal message
    (a string) if the approval itself is incomplete, in which case AWS isn't contacted at all."""
    if not live_approval_enabled(config):
        return "Live changes are off: set mode: LIVE and live.enabled: true in config/scalix.local.yaml first."
    if not acknowledged:
        return "Tick the box to approve this one change."
    if (typed_group or "").strip() != proposal.group or proposal.group != config.aws.auto_scaling_group:
        return f"Type the group name exactly ({proposal.group}) to confirm."
    if proposal.age(clock()) > PROPOSAL_MAX_AGE_SECONDS:
        return (f"This proposal is {proposal.age(clock()):.0f} s old (limit {PROPOSAL_MAX_AGE_SECONDS} s). "
                "Get a new proposal first.")
    if writer_factory is None:
        from scalix.aws.writer import CapacityWriter

        writer_factory = lambda: CapacityWriter(config)  # noqa: E731  (created only at the moment of use)
    controller = Controller(config, monitor, audit=audit, writer_factory=writer_factory, clock=clock,
                            **controller_kw)
    result = controller.tick(apply=True, confirm_group=proposal.group,
                             expected=(proposal.desired_before, proposal.desired_proposed),
                             approved_via=APPROVED_VIA)
    if result.outcome in (Outcome.APPLIED, Outcome.FAILED):
        engage_kill_switch(kill_switch_path)
    return result
