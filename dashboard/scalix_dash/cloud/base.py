"""Provider-neutral interface for scaling actions."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import pandas as pd

SCALE_OUT = "SCALE_OUT"
SCALE_IN = "SCALE_IN"


class ProviderSafetyError(RuntimeError):
    """Raised when a real cloud operation is attempted without the required opt-ins."""


@dataclass(frozen=True)
class VmOperation:
    """A provider-neutral request derived from one SCALIX scaling decision."""

    kind: str            # SCALE_OUT or SCALE_IN
    count: int
    reason: str = ""
    sim_time_s: float | None = None
    run_id: str = ""

    def __post_init__(self) -> None:
        if self.kind not in (SCALE_OUT, SCALE_IN):
            raise ValueError(f"Unknown operation kind {self.kind!r}")
        if not isinstance(self.count, int) or self.count < 1:
            raise ValueError("count must be a positive integer")


@dataclass
class OperationRecord:
    """What a provider did (or would have done) for one operation."""

    operation: VmOperation
    provider: str
    executed: bool                      # True only if a real API call was made
    outcome: str                        # e.g. "recorded", "planned", "refused", "ok", "error"
    detail: dict[str, Any] = field(default_factory=dict)
    at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))

    def as_row(self) -> dict[str, Any]:
        return {"at": self.at, "provider": self.provider, "sim_time_s": self.operation.sim_time_s,
                "run_id": self.operation.run_id, "kind": self.operation.kind, "count": self.operation.count,
                "executed": self.executed, "outcome": self.outcome, "detail": str(self.detail),
                "reason": self.operation.reason}


class CloudProvider(ABC):
    """Adapters implement these two operations; everything SCALIX decides maps onto them."""

    name: str = "abstract"
    #: True only for adapters that can create or delete real resources.
    can_provision_real_resources: bool = False

    @abstractmethod
    def scale_out(self, op: VmOperation) -> OperationRecord:
        """Add ``op.count`` VMs."""

    @abstractmethod
    def scale_in(self, op: VmOperation) -> OperationRecord:
        """Remove ``op.count`` VMs (the adapter chooses which, never the last one)."""

    def apply(self, op: VmOperation) -> OperationRecord:
        return self.scale_out(op) if op.kind == SCALE_OUT else self.scale_in(op)


def operations_from_events(events: pd.DataFrame) -> list[VmOperation]:
    """Converts engine scaling events (scaling_events.csv rows) that changed the fleet into operations."""
    ops: list[VmOperation] = []
    if events is None or events.empty:
        return ops
    for _, r in events.sort_values("time_s", kind="stable").iterrows():
        delta = int(r["applied_delta"])
        if delta == 0:
            continue
        ops.append(VmOperation(SCALE_OUT if delta > 0 else SCALE_IN, abs(delta), str(r.get("reason", "")),
                               float(r["time_s"]), str(r.get("run_id", ""))))
    return ops


def replay(events: pd.DataFrame, provider: CloudProvider) -> list[OperationRecord]:
    """Feeds every fleet-changing decision of a run to ``provider`` in time order."""
    return [provider.apply(op) for op in operations_from_events(events)]
