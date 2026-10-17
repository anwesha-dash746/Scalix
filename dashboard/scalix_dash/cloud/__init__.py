"""Optional, provider-neutral mapping of SCALIX scaling decisions to cloud VM operations.

The simulation and dashboard never need this package. The default provider is a mock that only
records what it *would* do; the CloudStack adapter plans API calls and refuses to execute them
unless explicitly enabled, configured through environment variables, and confirmed by an operator.
"""

from .base import CloudProvider, OperationRecord, ProviderSafetyError, VmOperation, operations_from_events, replay
from .mock import MockCloudProvider

__all__ = ["CloudProvider", "OperationRecord", "ProviderSafetyError", "VmOperation", "operations_from_events",
           "replay", "MockCloudProvider"]
