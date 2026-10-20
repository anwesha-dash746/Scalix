"""Append-only audit log for the AWS controller (JSON Lines, one decision per line).

Every controller run writes an entry, whatever the outcome: HOLD, REFUSED, DRY_RUN, APPLYING,
APPLIED or FAILED. The log is also the controller's memory. Cooldowns, duplicate detection and
CPU history are all read back from it, so they survive restarts.

Fail-safe rules:

* An ``APPLYING`` entry is written *before* a capacity change is sent. If the program dies before
  writing APPLIED or FAILED, the unresolved APPLYING still counts as a change, for cooldowns, and
  blocks further changes until a person checks the group.
* If the log cannot be read or parsed, :class:`AuditError` is raised and the controller refuses to
  change anything. It cannot know when it last acted, so it does not guess.

Default location: ``results/aws/audit.jsonl`` (git-ignored). Mock runs use a separate file.
Entries are passed through :func:`scalix.aws.session.redact` before being written.
"""

from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from scalix.aws.session import redact
from scalix.config import PROJECT_ROOT

DEFAULT_AUDIT_PATH = PROJECT_ROOT / "results" / "aws" / "audit.jsonl"
MOCK_AUDIT_PATH = PROJECT_ROOT / "results" / "aws" / "audit-mock.jsonl"


class AuditError(RuntimeError):
    """The audit log is unreadable or corrupt, so no capacity change may be made."""


@dataclass(frozen=True)
class LastChange:
    epoch: float
    action_id: str
    outcome: str        # APPLIED, or APPLYING (unresolved)
    desired_before: int
    desired_after: int

    @property
    def unresolved(self) -> bool:
        return self.outcome == "APPLYING"


class AuditLog:
    def __init__(self, path: str | Path = DEFAULT_AUDIT_PATH):
        self.path = Path(path)

    def append(self, entry: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        line = redact(json.dumps(entry, sort_keys=True, default=str))
        with self.path.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
            f.flush()
            os.fsync(f.fileno())

    def entries(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        try:
            text = self.path.read_text(encoding="utf-8")
        except OSError as exc:
            raise AuditError(f"cannot read audit log {self.path}: {exc}") from None
        out = []
        for n, line in enumerate(text.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
            except json.JSONDecodeError:
                raise AuditError(f"audit log {self.path} line {n} is corrupt; fix or move the file") from None
            if not isinstance(entry, dict) or not isinstance(entry.get("epoch"), (int, float)):
                raise AuditError(f"audit log {self.path} line {n} has no valid 'epoch'")
            out.append(entry)
        return out

    def _for_group(self, group: str) -> list[dict[str, Any]]:
        return [e for e in self.entries() if e.get("group") == group]

    def last_change(self, group: str) -> LastChange | None:
        """The latest capacity change for ``group``: APPLIED, or APPLYING without a result."""
        resolved: dict[str, str] = {}
        candidates = []
        for e in self._for_group(group):
            outcome = e.get("outcome")
            if outcome in ("APPLIED", "FAILED"):
                resolved[e.get("action_id")] = outcome
            if outcome in ("APPLYING", "APPLIED"):
                candidates.append(e)
        latest = None
        for e in candidates:
            if e["outcome"] == "APPLYING" and resolved.get(e.get("action_id")) == "FAILED":
                continue  # the call failed, so nothing changed
            if e["outcome"] == "APPLYING" and resolved.get(e.get("action_id")) == "APPLIED":
                continue  # its APPLIED entry is counted instead
            latest = e
        if latest is None:
            return None
        return LastChange(float(latest["epoch"]), str(latest.get("action_id")), latest["outcome"],
                          int(latest.get("desired_before", -1)), int(latest.get("desired_after", -1)))

    def last_scale_times(self, group: str) -> tuple[float, float]:
        """Epoch seconds of the last applied scale-up and scale-down (or -inf)."""
        up = down = -math.inf
        for e in self._for_group(group):
            if e.get("outcome") != "APPLIED":
                continue
            before, after = e.get("desired_before"), e.get("desired_after")
            if isinstance(before, int) and isinstance(after, int):
                if after > before:
                    up = max(up, float(e["epoch"]))
                elif after < before:
                    down = max(down, float(e["epoch"]))
        return up, down

    def cpu_history(self, group: str, limit: int = 20) -> list[float]:
        """Utilization (0..1) of earlier runs whose CPU reading was OK, oldest first."""
        values = [e["cpu_value"] for e in self._for_group(group)
                  if e.get("cpu_status") == "OK" and isinstance(e.get("cpu_value"), (int, float))
                  and e.get("outcome") not in ("APPLIED", "FAILED")]  # one entry per run
        return [float(v) for v in values[-limit:]]
