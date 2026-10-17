"""Mock cloud adapter: records intended VM operations, provisions nothing, makes no network calls."""

from __future__ import annotations

from .base import CloudProvider, OperationRecord, VmOperation


class MockCloudProvider(CloudProvider):
    name = "mock"
    can_provision_real_resources = False

    def __init__(self, initial_vms: int = 1, min_vms: int = 1):
        if min_vms < 1:
            raise ValueError("min_vms must be >= 1 (the last VM is never removed)")
        if initial_vms < min_vms:
            raise ValueError("initial_vms must be >= min_vms")
        self.min_vms = min_vms
        self._next = 0
        self.vms: list[str] = [self._new_id() for _ in range(initial_vms)]
        self.records: list[OperationRecord] = []

    def _new_id(self) -> str:
        self._next += 1
        return f"mock-vm-{self._next:04d}"

    def scale_out(self, op: VmOperation) -> OperationRecord:
        added = [self._new_id() for _ in range(op.count)]
        self.vms.extend(added)
        return self._record(op, "recorded", {"would_create": added, "fleet_after": len(self.vms)})

    def scale_in(self, op: VmOperation) -> OperationRecord:
        removable = max(0, len(self.vms) - self.min_vms)
        n = min(op.count, removable)
        removed = [self.vms.pop() for _ in range(n)]       # newest first
        outcome = "recorded" if n == op.count else ("partially refused" if n else "refused")
        detail = {"would_destroy": removed, "fleet_after": len(self.vms)}
        if n < op.count:
            detail["refused"] = f"{op.count - n} VM(s): would go below min_vms={self.min_vms}"
        return self._record(op, outcome, detail)

    def _record(self, op: VmOperation, outcome: str, detail: dict) -> OperationRecord:
        rec = OperationRecord(op, self.name, executed=False, outcome=outcome, detail=detail)
        self.records.append(rec)
        return rec
