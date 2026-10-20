"""The AWS auto-scaling controller: monitoring -> policy -> safety checks -> (dry run | one approved write).

One call to :meth:`Controller.tick` does exactly one decision:

1. **Read** the group and its CPU through the read-only monitor.
2. **Data checks.** The controller HOLDs, and never acts, if the data is not decision-ready
   (missing, stale, malformed or errored CPU; group missing or inconsistent), if the group is
   still converging from an earlier change, or if its desired capacity is already above the
   approved maximum.
3. **Decide** with the configured policy (THRESHOLD, SCALIX or STATIC). It's the same code the
   simulator uses, with the capacity limits set to the group's sizes capped at ``live.max_capacity``.
4. **Action checks.** A proposed change is REFUSED unless: the group is allowlisted, the
   controller's own cooldown has passed, it isn't a duplicate (or an earlier change is unresolved),
   the audit log is readable, and the new size is within the approved maximum. A step bigger than
   ``live.max_step`` is reduced.
5. **Dry run (default).** Report what *would* happen. Nothing is sent.
6. **Apply.** Only if *every* live gate passes: apply was requested, mode is LIVE,
   ``live.enabled`` is true, the kill switch is off, ``confirm_group`` matches the group exactly,
   the data is LIVE_AWS (never mocked), this isn't running on an EC2 instance, and the single-run
   lock is free. The controller then writes APPLYING to the audit log, makes **one**
   ``SetDesiredCapacity`` call, writes APPLIED or FAILED, and never retries.

Every run, whatever its outcome, is appended to the audit log.
"""

from __future__ import annotations

import os
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable

from scalix.algorithms import Action, ScalingContext, ScalingDecision, create_policy
from scalix.aws.audit import AuditError, AuditLog
from scalix.aws.monitoring import MetricStatus, MonitoringSnapshot
from scalix.aws.session import describe_error, redact
from scalix.config import Mode, ScalixConfig, kill_switch_active
from scalix.sources import DataSource

LOCK_STALE_SECONDS = 900


class Outcome(str, Enum):
    HOLD = "HOLD"          # nothing to do, or data not fit for a decision
    REFUSED = "REFUSED"    # the policy wanted a change but a safety check blocked it
    DRY_RUN = "DRY_RUN"    # a change passed the action checks; reported, not applied
    APPLIED = "APPLIED"    # one SetDesiredCapacity call succeeded
    FAILED = "FAILED"      # the call was made and AWS returned an error (not retried)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


@dataclass(frozen=True)
class ControllerResult:
    time_utc: str
    outcome: Outcome
    group: str | None
    data_source: DataSource
    desired_before: int | None
    desired_proposed: int | None
    reason: str
    dry_run: bool
    decision: ScalingDecision | None = None
    cpu_status: str | None = None
    cpu_value: float | None = None
    in_service: int | None = None
    checks: tuple[Check, ...] = ()
    live_gates: tuple[Check, ...] = ()

    def render(self) -> str:
        src = self.data_source.label
        lines = [f"SCALIX controller [{self.data_source.value}: {src}] {self.time_utc}",
                 f"  Group:     {self.group or '-'}",
                 f"  CPU:       {self.cpu_status or '-'}"
                 + (f" ({self.cpu_value:.1%})" if self.cpu_value is not None else ""),
                 f"  Capacity:  desired {self.desired_before if self.desired_before is not None else '-'}"
                 + (f" -> {self.desired_proposed}" if self.desired_proposed is not None
                    and self.desired_proposed != self.desired_before else "")]
        if self.decision is not None:
            lines.append(f"  Policy:    {self.decision.action.value} {self.decision.delta:+d}: {self.decision.reason}")
        lines.append(f"  OUTCOME:   {self.outcome.value}: {self.reason}")
        if self.checks:
            lines.append("  Safety checks:")
            lines += [f"    [{'pass' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in self.checks]
        if self.live_gates:
            lines.append("  Live gates (all must pass to apply):")
            lines += [f"    [{'pass' if c.ok else 'FAIL'}] {c.name}: {c.detail}" for c in self.live_gates]
        if self.outcome is not Outcome.APPLIED:
            lines.append("  No AWS change was made.")
        return redact("\n".join(lines))


def running_on_ec2() -> bool:
    """True if this machine looks like an EC2 instance. Reads local files only, no network.

    The controller must not run on the workers it manages.
    """
    probes = {
        "/sys/hypervisor/uuid": lambda s: s.lower().startswith("ec2"),
        "/sys/devices/virtual/dmi/id/board_asset_tag": lambda s: s.startswith("i-"),
        "/sys/devices/virtual/dmi/id/sys_vendor": lambda s: "amazon" in s.lower(),
    }
    for path, test in probes.items():
        try:
            if test(Path(path).read_text(errors="ignore").strip()):
                return True
        except OSError:
            continue
    return False


class Controller:
    def __init__(self, config: ScalixConfig, monitor: Any, *, audit: AuditLog,
                 writer_factory: Callable[[], Any] | None = None,
                 clock: Callable[[], float] = time.time,
                 kill_switch: Callable[[], str | None] = kill_switch_active,
                 on_ec2: Callable[[], bool] = running_on_ec2,
                 lock_path: str | Path | None = None):
        source = getattr(monitor, "source", None)
        if source is not DataSource.MOCKED and not config.mode.uses_aws:
            raise ValueError("SIMULATION mode never contacts AWS; use --mock for a demo, or set mode: READ_ONLY")
        self.config = config
        self.monitor = monitor
        self.audit = audit
        self.writer_factory = writer_factory
        self.clock = clock
        self.kill_switch = kill_switch
        self.on_ec2 = on_ec2
        self.lock_path = Path(lock_path) if lock_path else audit.path.with_suffix(".lock")
        self.group = config.aws.auto_scaling_group or (getattr(monitor, "name", None) if source is DataSource.MOCKED else None)

    # ------------------------------------------------------------------ helpers
    def _now_utc(self, epoch: float) -> str:
        return datetime.fromtimestamp(epoch, timezone.utc).isoformat(timespec="seconds")

    def _record(self, result: ControllerResult, epoch: float, action_id: str | None = None,
                outcome: str | None = None) -> None:
        d = result.decision
        entry = {
            "epoch": epoch, "time_utc": result.time_utc, "group": result.group, "mode": self.config.mode.value,
            "data_source": result.data_source.value, "outcome": outcome or result.outcome.value,
            "dry_run": result.dry_run, "action_id": action_id,
            "desired_before": result.desired_before, "desired_after": result.desired_proposed,
            "cpu_status": result.cpu_status, "cpu_value": result.cpu_value, "in_service": result.in_service,
            "policy": self.config.controller.policy,
            "decision": None if d is None else {"action": d.action.value, "delta": d.delta, "reason": d.reason,
                                                 "cooldown_blocked": d.cooldown_blocked},
            "reason": result.reason,
            "checks": [c.__dict__ for c in result.checks],
            "live_gates": [c.__dict__ for c in result.live_gates],
        }
        self.audit.append(entry)

    @contextmanager
    def _lock(self):
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            if self.lock_path.exists() and self.clock() - self.lock_path.stat().st_mtime > LOCK_STALE_SECONDS:
                self.lock_path.unlink()  # left behind by a crashed run long ago
            fd = os.open(self.lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            yield False
            return
        try:
            os.write(fd, f"pid {os.getpid()}\n".encode())
            os.close(fd)
            yield True
        finally:
            try:
                self.lock_path.unlink()
            except FileNotFoundError:
                pass

    # ------------------------------------------------------------------ one decision
    def tick(self, apply: bool = False, confirm_group: str | None = None) -> ControllerResult:
        epoch = float(self.clock())
        stamp = self._now_utc(epoch)
        cfg, live = self.config, self.config.live
        snap: MonitoringSnapshot = self.monitor.snapshot()
        source = snap.source
        group = snap.group
        cpu = snap.cpu
        base = dict(time_utc=stamp, group=self.group, data_source=source, dry_run=not apply,
                    cpu_status=cpu.status.value, cpu_value=cpu.value,
                    in_service=group.in_service if group is not None else None)

        def finish(outcome: Outcome, reason: str, **kw) -> ControllerResult:
            result = ControllerResult(outcome=outcome, reason=reason, **base, **kw)
            self._record(result, epoch)
            return result

        # ---- 1. data checks: never decide on data we cannot trust -------------------------------
        data_checks: list[Check] = []
        data_checks.append(Check("target group configured", bool(self.group),
                                 self.group or "aws.auto_scaling_group is not set"))
        data_checks.append(Check("monitoring errors", not snap.errors, "; ".join(snap.errors) or "none"))
        data_checks.append(Check("group state available", group is not None,
                                 "read" if group is not None else "group could not be read"))
        data_checks.append(Check("CPU reading usable", cpu.status is MetricStatus.OK,
                                 f"{cpu.status.value}: {cpu.reason}"))
        if group is not None:
            data_checks.append(Check("group is the target", group.name == self.group, group.name))
            data_checks.append(Check("group has at least one worker", group.desired_capacity >= 1,
                                     f"desired {group.desired_capacity}" + ("" if group.desired_capacity >= 1
                                                                            else ": SCALIX never starts a group from zero")))
            converged = group.in_service == group.desired_capacity and group.pending == 0
            data_checks.append(Check("group stable (no change in progress)", converged,
                                     f"desired {group.desired_capacity}, in service {group.in_service}, "
                                     f"pending {group.pending}"))
            within = group.desired_capacity <= live.max_capacity
            data_checks.append(Check("desired within approved maximum", within,
                                     f"desired {group.desired_capacity}, approved max {live.max_capacity}"
                                     + ("" if within else ": SCALIX will not act; fix the group manually")))
        failed = [c for c in data_checks if not c.ok]
        if failed:
            return finish(Outcome.HOLD, "not acting: " + "; ".join(f"{c.name} ({c.detail})" for c in failed),
                          desired_before=group.desired_capacity if group else None, desired_proposed=None,
                          checks=tuple(data_checks))

        # ---- 2. decide with the policy ----------------------------------------------------------
        desired = group.desired_capacity
        eff_min = max(1, group.min_size)
        eff_max = min(group.max_size, live.max_capacity)
        try:
            history = self.audit.cpu_history(self.group)
            busy = self.audit.busy_history(self.group)
            last_up, last_down = self.audit.last_scale_times(self.group)
            last_change = self.audit.last_change(self.group)
            audit_ok, audit_detail = True, f"readable ({self.audit.path.name})"
        except AuditError as exc:
            history, busy, last_up, last_down, last_change = [], [], float("-inf"), float("-inf"), None
            audit_ok, audit_detail = False, str(exc)

        if cfg.controller.policy == "STATIC":
            policy = create_policy("STATIC", worker_count=desired)
        else:
            policy = create_policy(cfg.controller.policy, **cfg.controller.policy_settings,
                                   min_capacity=min(eff_min, desired), initial_capacity=desired,
                                   max_capacity=max(eff_max, desired))
        ctx = ScalingContext(time=epoch, provisioned=desired, active=group.in_service, booting=group.pending,
                             utilization=cpu.value, utilization_history=tuple(history) + (cpu.value,),
                             # No request rate without a load balancer. With demand_signal CPU, SCALIX gets
                             # CPU x in-service instances instead (equals served load; see docs/CONTROLLER.md).
                             arrival_rate_history=(tuple(busy) + (cpu.value * group.in_service,)
                                                   if cfg.controller.demand_signal == "CPU" else ()),
                             last_scale_up_time=last_up, last_scale_down_time=last_down)
        decision = policy.decide(ctx)
        base.update(decision=decision, desired_before=desired)

        if decision.action is Action.NONE:
            return finish(Outcome.HOLD, f"policy {policy.name} proposes no change", desired_proposed=desired,
                          checks=tuple(data_checks))

        step = max(-live.max_step, min(live.max_step, decision.delta))
        proposed = max(eff_min, min(eff_max, desired + step))
        notes = []
        if step != decision.delta:
            notes.append(f"step {decision.delta:+d} reduced to {step:+d} (live.max_step)")
        if proposed != desired + step:
            notes.append(f"limited to {eff_min}..{eff_max} (group sizes and live.max_capacity)")
        if proposed == desired:
            return finish(Outcome.HOLD, f"policy wants {decision.delta:+d} but the group is already at its "
                          f"limit ({eff_min}..{eff_max})", desired_proposed=desired, checks=tuple(data_checks))

        # ---- 3. action checks (apply to dry runs too: a dry run shows what would be refused) ------
        action_checks = list(data_checks)
        action_checks.append(Check("group on allowlist", self.group in live.allowed_groups,
                                   f"live.allowed_groups = {list(live.allowed_groups) or '[] (empty)'}"))
        action_checks.append(Check("audit log readable", audit_ok, audit_detail))
        since = epoch - last_change.epoch if last_change else None
        action_checks.append(Check("controller cooldown", since is None or since >= live.min_seconds_between_actions,
                                   "no earlier change" if since is None else
                                   f"{since:.0f} s since last change (needs {live.min_seconds_between_actions} s)"))
        dup = last_change is not None and (last_change.unresolved or
                                           (last_change.desired_after == proposed and desired == proposed))
        action_checks.append(Check("not a duplicate / nothing unresolved", not dup,
                                   "ok" if not dup else
                                   ("an earlier change has no recorded result; check the group by hand"
                                    if last_change.unresolved else f"desired is already {proposed}")))
        action_checks.append(Check("within approved maximum", 1 <= proposed <= live.max_capacity,
                                   f"proposed {proposed}, approved max {live.max_capacity}"))
        base.update(desired_proposed=proposed)
        blocked = [c for c in action_checks if not c.ok]
        note = (" [" + "; ".join(notes) + "]") if notes else ""
        if blocked:
            return finish(Outcome.REFUSED, f"would change desired {desired} -> {proposed}{note}, refused: "
                          + "; ".join(f"{c.name} ({c.detail})" for c in blocked), checks=tuple(action_checks))

        # ---- 4. live gates -------------------------------------------------------------------------
        kill = self.kill_switch()
        gates = (
            Check("apply requested", apply, "yes" if apply else "no (dry run)"),
            Check("mode is LIVE", cfg.mode is Mode.LIVE, f"mode {cfg.mode.value}"),
            Check("live.enabled", live.enabled, str(live.enabled).lower()),
            Check("kill switch off", kill is None, kill or "off"),
            Check("group confirmed by operator", confirm_group == self.group,
                  "matches" if confirm_group == self.group else "pass --confirm <exact group name>"),
            Check("data is live AWS data", source is DataSource.LIVE_AWS, source.value),
            Check("not running on an EC2 instance", not self.on_ec2(), "controller host is not a worker"),
            Check("writer available", self.writer_factory is not None,
                  "yes" if self.writer_factory is not None else "no writer (READ_ONLY or mock)"),
        )
        if not apply:
            return finish(Outcome.DRY_RUN, f"would change desired {desired} -> {proposed}{note}; dry run, "
                          "not applied", checks=tuple(action_checks), live_gates=gates)
        failed_gates = [g for g in gates if not g.ok]
        if failed_gates:
            return finish(Outcome.REFUSED, "apply requested but live gate(s) failed: "
                          + "; ".join(f"{g.name} ({g.detail})" for g in failed_gates),
                          checks=tuple(action_checks), live_gates=gates)

        # ---- 5. one write, recorded before and after, never retried --------------------------------
        with self._lock() as locked:
            if not locked:
                return finish(Outcome.REFUSED, f"another controller run holds {self.lock_path.name}",
                              checks=tuple(action_checks), live_gates=gates)
            action_id = uuid.uuid4().hex
            intent = ControllerResult(outcome=Outcome.DRY_RUN, reason=f"applying desired {desired} -> {proposed}",
                                      checks=tuple(action_checks), live_gates=gates, **base)
            self._record(intent, epoch, action_id=action_id, outcome="APPLYING")
            try:
                self.writer_factory().set_desired_capacity(self.group, proposed)
            except Exception as exc:
                err = describe_error("autoscaling:SetDesiredCapacity", exc)
                result = ControllerResult(outcome=Outcome.FAILED, reason=f"{err.code}: {err.message}"
                                          + (f" ({err.hint})" if err.hint else "") + "; not retried",
                                          checks=tuple(action_checks), live_gates=gates, **base)
                self._record(result, epoch, action_id=action_id)
                return result
            result = ControllerResult(outcome=Outcome.APPLIED, reason=f"set desired {desired} -> {proposed}{note}",
                                      checks=tuple(action_checks), live_gates=gates, **base)
            self._record(result, epoch, action_id=action_id)
            return result


# ---------------------------------------------------------------------- command line

MOCK_SCENARIOS = {
    "overload": dict(cpu_percents=[85.0], desired=1, in_service=1, min_size=1, max_size=2),
    "calm": dict(cpu_percents=[45.0], desired=1, in_service=1, min_size=1, max_size=2),
    "idle": dict(cpu_percents=[10.0], desired=2, in_service=2, min_size=1, max_size=2),
    "at-max": dict(cpu_percents=[95.0], desired=2, in_service=2, min_size=1, max_size=2),
    "missing": dict(cpu_percents=[None], desired=1, in_service=1, min_size=1, max_size=2),
    "stale": dict(cpu_percents=[60.0], desired=1, in_service=1, min_size=1, max_size=2, reading_age_seconds=2400),
    "malformed": dict(cpu_percents=[float("nan")], desired=1, in_service=1, min_size=1, max_size=2),
    "converging": dict(cpu_percents=[85.0], desired=2, in_service=1, min_size=1, max_size=2),
}


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys
    from dataclasses import replace

    from scalix.aws.audit import DEFAULT_AUDIT_PATH, MOCK_AUDIT_PATH
    from scalix.aws.monitoring import CloudWatchMonitor, MockMonitor
    from scalix.config import ConfigError, load_config

    p = argparse.ArgumentParser(
        prog="python -m scalix.aws.controller",
        description="One SCALIX controller decision. Dry run unless --apply AND every live gate passes.")
    p.add_argument("--mock", choices=sorted(MOCK_SCENARIOS),
                   help="use scripted MOCKED data instead of AWS (no AWS contact; can never apply)")
    p.add_argument("--apply", action="store_true", help="request a real change (LIVE mode only; one action)")
    p.add_argument("--confirm", metavar="GROUP", help="exact group name, required together with --apply")
    p.add_argument("--watch", type=int, metavar="N", default=1,
                   help="repeat N dry-run decisions, controller.decision_interval_seconds apart (not with --apply)")
    p.add_argument("--audit", type=Path, help="audit log path (default results/aws/audit.jsonl)")
    args = p.parse_args(argv)

    if args.apply and args.watch != 1:
        print("--apply makes exactly one decision; it cannot be combined with --watch", file=sys.stderr)
        return 2
    if args.watch < 1:
        print("--watch must be >= 1", file=sys.stderr)
        return 2
    try:
        config = load_config()
        writer_factory = None
        if args.mock:
            monitor = MockMonitor(name="mock-scalix-workers", **MOCK_SCENARIOS[args.mock])
            # Demo only: allowlist the mock group so the action checks can be shown. MOCKED data can
            # never pass the live gates, and no writer exists, so nothing can be applied.
            config = replace(config, live=replace(config.live, allowed_groups=(monitor.name,)))
            audit = AuditLog(args.audit or MOCK_AUDIT_PATH)
        else:
            if not config.mode.uses_aws:
                print("mode is SIMULATION, which never contacts AWS. Use --mock for a demo, or set "
                      "mode: READ_ONLY in config/scalix.local.yaml.", file=sys.stderr)
                return 2
            monitor = CloudWatchMonitor.from_config(config)
            audit = AuditLog(args.audit or DEFAULT_AUDIT_PATH)
            if config.mode is Mode.LIVE and config.live.enabled and args.apply:
                from scalix.aws.writer import CapacityWriter

                writer_factory = lambda: CapacityWriter(config)  # noqa: E731  (created only at the moment of use)
        controller = Controller(config, monitor, audit=audit, writer_factory=writer_factory)
    except (ConfigError, ValueError) as exc:
        print(f"Invalid setting: {exc}", file=sys.stderr)
        return 2

    for i in range(args.watch):
        if i:
            time.sleep(config.controller.decision_interval_seconds)
        result = controller.tick(apply=args.apply, confirm_group=args.confirm)
        print(result.render())
        print(f"  Audit: {audit.path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
