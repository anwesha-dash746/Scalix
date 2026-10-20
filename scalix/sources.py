"""Where a number came from. Every value SCALIX shows carries one of these labels."""

from __future__ import annotations

from enum import Enum


class DataSource(str, Enum):
    SIMULATED = "SIMULATED"  # produced by the local simulator's model
    MOCKED = "MOCKED"        # scripted test data shaped like AWS data, never from AWS
    LIVE_AWS = "LIVE_AWS"    # read from a real AWS account through the read-only adapter

    @property
    def label(self) -> str:
        return {
            DataSource.SIMULATED: "Simulated (local model, not measured)",
            DataSource.MOCKED: "Mocked (test data, not from AWS)",
            DataSource.LIVE_AWS: "Live AWS data",
        }[self]
