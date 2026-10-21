"""Approving one live change from the dashboard (scalix.aws.approval): the same gates as the command line,
plus a typed confirmation, a fresh unchanged proposal, and the kill switch turned on afterwards.
No AWS, no boto3: live-labelled test monitors and a recording fake writer."""

from __future__ import annotations

import json

from scalix.aws import approval
from scalix.aws.audit import AuditLog
from scalix.aws.controller import Outcome
from scalix.config import kill_switch_active

from test_controller import FakeWriter, monitor
from tests_helpers_controller import GROUP, T0, live_config, read_only_config


def setup(tmp_path, cpu=85.0, clock=T0):
    audit = AuditLog(tmp_path / "audit.jsonl")
    result, proposal = approval.get_proposal(live_config(), monitor(cpu=cpu), audit, clock=lambda: clock)
    return audit, result, proposal


def do_approve(tmp_path, proposal, audit, *, typed=GROUP, ack=True, config=None, cpu=85.0, clock=T0, kill=None,
               writer=None):
    writer = writer or FakeWriter()
    out = approval.approve(config or live_config(), proposal, typed, ack, monitor=monitor(cpu=cpu), audit=audit,
                           writer_factory=lambda: writer, clock=lambda: clock,
                           kill_switch_path=tmp_path / "LIVE_DISABLED",
                           kill_switch=lambda: kill, on_ec2=lambda: False, lock_path=tmp_path / "c.lock")
    return out, writer


def test_a_proposal_is_a_dry_run_that_passed_every_check(tmp_path):
    audit, result, proposal = setup(tmp_path)
    assert result.outcome is Outcome.DRY_RUN and proposal is not None
    assert (proposal.group, proposal.desired_before, proposal.desired_proposed) == (GROUP, 1, 2)
    _, held, none = setup(tmp_path, cpu=45.0)
    assert held.outcome is Outcome.HOLD and none is None   # nothing to approve


def test_approved_change_is_applied_once_recorded_and_the_kill_switch_turns_on(tmp_path):
    audit, _, proposal = setup(tmp_path)
    out, writer = do_approve(tmp_path, proposal, audit)
    assert out.outcome is Outcome.APPLIED and writer.calls == [(GROUP, 2)]
    assert kill_switch_active(path=tmp_path / "LIVE_DISABLED", environ={}) is not None
    applied = [e for e in audit.entries() if e["outcome"] in ("APPLYING", "APPLIED")]
    assert [e["approved_via"] for e in applied] == ["dashboard", "dashboard"]
    assert any(g["name"] == "matches the approved proposal" and g["ok"] for g in applied[-1]["live_gates"])


def test_incomplete_approval_never_contacts_aws(tmp_path):
    audit, _, proposal = setup(tmp_path)
    before = len(audit.entries())
    for kw in (dict(ack=False), dict(typed="scalix-worker"), dict(typed=""), dict(config=read_only_config()),
               dict(clock=T0 + approval.PROPOSAL_MAX_AGE_SECONDS + 1)):
        out, writer = do_approve(tmp_path, proposal, audit, **kw)
        assert isinstance(out, str) and writer.calls == [], kw
    assert len(audit.entries()) == before           # not even a dry run was made
    assert not (tmp_path / "LIVE_DISABLED").exists()


def test_a_changed_situation_is_not_applied(tmp_path):
    audit, _, proposal = setup(tmp_path)
    out, writer = do_approve(tmp_path, proposal, audit, cpu=45.0)   # demand fell since the proposal
    assert out.outcome is Outcome.HOLD and writer.calls == []
    stale = approval.Proposal(GROUP, 1, 2, T0, proposal.result)
    other = approval.Proposal(GROUP, 2, 3, T0, proposal.result)       # approved something else
    out, writer = do_approve(tmp_path, other, audit)
    assert out.outcome is Outcome.REFUSED and writer.calls == []
    assert "matches the approved proposal" in out.reason
    assert not (tmp_path / "LIVE_DISABLED").exists()
    assert stale.age(T0 + 5) == 5


def test_every_controller_gate_still_applies(tmp_path):
    audit, _, proposal = setup(tmp_path)
    out, writer = do_approve(tmp_path, proposal, audit, kill="kill switch file LIVE_DISABLED exists")
    assert out.outcome is Outcome.REFUSED and writer.calls == [] and "kill switch off" in out.reason


def test_a_failed_write_is_not_retried_and_still_turns_the_kill_switch_on(tmp_path):
    audit, _, proposal = setup(tmp_path)
    out, writer = do_approve(tmp_path, proposal, audit, writer=FakeWriter(error=RuntimeError("AccessDenied")))
    assert out.outcome is Outcome.FAILED and len(writer.calls) == 1
    assert (tmp_path / "LIVE_DISABLED").exists()


def test_approval_module_can_turn_the_kill_switch_on_but_never_off():
    import inspect

    source = inspect.getsource(approval)
    assert "unlink" not in source and "remove(" not in source and "rmdir" not in source
    assert "touch()" in source
