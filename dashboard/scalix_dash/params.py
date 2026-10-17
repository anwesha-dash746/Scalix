"""User-selectable experiment parameters: validation and translation to Java engine properties.

The Java engine stays the source of truth: it re-validates everything and rejects bad values with
exit code 2. Validating here as well gives immediate, field-level feedback in the dashboard.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field, fields
from typing import Any

from . import POLICIES, WORKLOADS

# Datacenter capacity of the default Java configuration: 10 hosts x (8 PEs / 2 PEs per VM).
# The dashboard does not change the host or VM type, so this bound is fixed.
MAX_VMS_CAPACITY = 40

# SCALIX parameters that are not exposed as main form fields keep the Part 1 defaults.
SCALIX_PREEMPTIVE_UPPER_DEFAULT = 0.55
SCALIX_TARGET_UTILIZATION_DEFAULT = 0.60
SCALIX_PROJECTION_INTERVALS_DEFAULT = 2
SCALIX_MAX_STEP_DEFAULT = 3


@dataclass
class ExperimentParams:
    """Everything the dashboard lets a user choose. Units are seconds, fractions and VM counts."""

    workloads: list[str] = field(default_factory=lambda: ["BURST"])
    policies: list[str] = field(default_factory=lambda: list(POLICIES))
    duration_s: float = 900
    # Initial VM count, used for every policy: STATIC keeps it, THRESHOLD and SCALIX start from it.
    initial_vms: int = 8
    min_vms: int = 1
    max_vms: int = 20
    upper_threshold: float = 0.70
    lower_threshold: float = 0.30
    observation_window: int = 6
    trend_threshold: float = 0.03
    scale_up_cooldown_s: float = 30
    scale_down_cooldown_s: float = 90
    threshold_cooldown_s: float = 0
    repetitions: int = 1
    base_seed: int = 42
    # Advanced settings
    decision_interval_s: float = 15
    vm_boot_s: float = 30
    price_per_vm_hour: float = 0.10
    max_drain_s: float = 3600
    scalix_preemptive_upper: float = SCALIX_PREEMPTIVE_UPPER_DEFAULT
    scalix_target_utilization: float = SCALIX_TARGET_UTILIZATION_DEFAULT

    # ------------------------------------------------------------------ validation

    def validate(self) -> list[str]:
        """Returns a list of human-readable problems; empty means valid."""
        errs: list[str] = []

        if not self.workloads:
            errs.append("Select at least one workload.")
        bad_w = [w for w in self.workloads if w not in WORKLOADS]
        if bad_w:
            errs.append(f"Unknown workload(s): {', '.join(map(str, bad_w))}.")
        if len(set(self.workloads)) != len(self.workloads):
            errs.append("Workloads must not repeat.")
        if not self.policies:
            errs.append("Select at least one policy.")
        bad_p = [p for p in self.policies if p not in POLICIES]
        if bad_p:
            errs.append(f"Unknown policy/policies: {', '.join(map(str, bad_p))}.")
        if len(set(self.policies)) != len(self.policies):
            errs.append("Policies must not repeat.")

        _num(errs, "Simulation duration", self.duration_s, lo=60, hi=86_400)
        _int(errs, "Initial VMs", self.initial_vms, lo=1, hi=MAX_VMS_CAPACITY)
        _int(errs, "Minimum VMs", self.min_vms, lo=1, hi=MAX_VMS_CAPACITY)
        _int(errs, "Maximum VMs", self.max_vms, lo=1, hi=MAX_VMS_CAPACITY)
        if _is_int(self.min_vms) and _is_int(self.max_vms) and self.min_vms > self.max_vms:
            errs.append("Minimum VMs must not exceed maximum VMs.")
        if (_is_int(self.initial_vms) and _is_int(self.min_vms) and _is_int(self.max_vms)
                and not self.min_vms <= self.initial_vms <= self.max_vms):
            errs.append("Initial VMs must be between minimum and maximum VMs.")

        _num(errs, "Upper threshold", self.upper_threshold, lo=0, hi=1)
        _num(errs, "Lower threshold", self.lower_threshold, lo=0, hi=1)
        if _is_num(self.upper_threshold) and _is_num(self.lower_threshold) and not self.lower_threshold < self.upper_threshold:
            errs.append("Lower threshold must be below the upper threshold.")
        if _is_num(self.upper_threshold) and _is_num(self.lower_threshold):
            for label, v in (("SCALIX preemptive threshold", self.scalix_preemptive_upper),
                             ("SCALIX target utilization", self.scalix_target_utilization)):
                if not _is_num(v) or not (self.lower_threshold < v <= self.upper_threshold):
                    errs.append(f"{label} ({v}) must be above the lower threshold and at most the upper threshold.")

        _int(errs, "Observation window", self.observation_window, lo=2, hi=100)
        _num(errs, "Trend threshold", self.trend_threshold, lo=0, hi=10)
        _num(errs, "Scale-up cooldown", self.scale_up_cooldown_s, lo=0, hi=86_400)
        _num(errs, "Scale-down cooldown", self.scale_down_cooldown_s, lo=0, hi=86_400)
        _num(errs, "Threshold-policy cooldown", self.threshold_cooldown_s, lo=0, hi=86_400)
        _int(errs, "Repetitions", self.repetitions, lo=1, hi=30)
        _int(errs, "Base seed", self.base_seed, lo=-(2**63), hi=2**63 - 1 - 30)
        _num(errs, "Decision interval", self.decision_interval_s, lo=1, hi=3600)
        if _is_num(self.decision_interval_s) and _is_num(self.duration_s) and self.decision_interval_s > self.duration_s:
            errs.append("Decision interval must not exceed the simulation duration.")
        _num(errs, "VM boot time", self.vm_boot_s, lo=0, hi=3600)
        _num(errs, "Price per VM-hour", self.price_per_vm_hour, lo=0, hi=1000)
        _num(errs, "Maximum drain time", self.max_drain_s, lo=0, hi=86_400)
        return errs

    # ------------------------------------------------------------------ translation

    def seeds(self) -> list[int]:
        """One seed per repetition. Every policy in a repetition uses the same seed (same requests)."""
        return [int(self.base_seed) + i for i in range(int(self.repetitions))]

    def workload_shape(self) -> dict[str, float]:
        """Burst and fluctuation timing scaled with the duration.

        At 900 s this equals the Part 1 defaults (burst 300-420 s, period 240 s); other durations
        keep the same proportions so BURST and FLUCTUATING stay valid for any duration.
        """
        d = float(self.duration_s)
        return {
            "workload.burst.startSeconds": round(d / 3, 6),
            "workload.burst.durationSeconds": round(d * 2 / 15, 6),
            "workload.fluctuating.periodSeconds": round(d * 4 / 15, 6),
        }

    def to_engine_properties(self, seed: int) -> dict[str, str]:
        """Java property keys/values for one repetition (keys as in scalix-default.properties)."""
        errs = self.validate()
        if errs:
            raise ValueError("Invalid parameters: " + " ".join(errs))
        props: dict[str, Any] = {
            "simulation.seed": int(seed),
            "simulation.durationSeconds": self.duration_s,
            "simulation.decisionIntervalSeconds": self.decision_interval_s,
            "simulation.maxDrainSeconds": self.max_drain_s,
            "vm.bootSeconds": self.vm_boot_s,
            "pricing.vmPricePerHour": self.price_per_vm_hour,
            "policy.static.vmCount": self.initial_vms,
            "policy.scaling.initialVms": self.initial_vms,
            "policy.scaling.minVms": self.min_vms,
            "policy.scaling.maxVms": self.max_vms,
            "policy.threshold.upper": self.upper_threshold,
            "policy.threshold.lower": self.lower_threshold,
            "policy.threshold.cooldownSeconds": self.threshold_cooldown_s,
            "policy.scalix.upper": self.upper_threshold,
            "policy.scalix.lower": self.lower_threshold,
            "policy.scalix.preemptiveUpper": self.scalix_preemptive_upper,
            "policy.scalix.targetUtilization": self.scalix_target_utilization,
            "policy.scalix.observationWindow": self.observation_window,
            "policy.scalix.trendThreshold": self.trend_threshold,
            "policy.scalix.scaleUpCooldownSeconds": self.scale_up_cooldown_s,
            "policy.scalix.scaleDownCooldownSeconds": self.scale_down_cooldown_s,
            "experiment.workloads": ",".join(self.workloads),
            "experiment.policies": ",".join(self.policies),
            "export.requests": "false",
        }
        props.update(self.workload_shape())
        return {k: _fmt(v) for k, v in props.items()}

    # ------------------------------------------------------------------ (de)serialisation

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "ExperimentParams":
        """Builds params from a mapping, ignoring unknown keys; missing keys keep their defaults."""
        data = dict(data or {})
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        for key in ("workloads", "policies"):
            if key in kwargs:
                v = kwargs[key]
                if isinstance(v, str):
                    v = [s for s in v.split(",") if s.strip()]
                kwargs[key] = [str(s).strip().upper() for s in (v or [])]
        return cls(**kwargs)


def properties_text(props: dict[str, str], header: str = "") -> str:
    """Renders a Java .properties file (keys/values here never need escaping beyond ':' and '=')."""
    lines = [f"# {line}" for line in header.splitlines()] if header else []
    for k in sorted(props):
        lines.append(f"{k}={props[k]}")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------- helpers

def _is_num(v: Any) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and v == v and v not in (float("inf"), float("-inf"))


def _is_int(v: Any) -> bool:
    return isinstance(v, int) and not isinstance(v, bool)


def _num(errs: list[str], label: str, v: Any, lo: float, hi: float) -> None:
    if not _is_num(v):
        errs.append(f"{label} must be a finite number (got {v!r}).")
    elif not lo <= v <= hi:
        errs.append(f"{label} must be between {lo} and {hi} (got {v}).")


def _int(errs: list[str], label: str, v: Any, lo: int, hi: int) -> None:
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    if not _is_int(v):
        errs.append(f"{label} must be a whole number (got {v!r}).")
    elif not lo <= v <= hi:
        errs.append(f"{label} must be between {lo} and {hi} (got {v}).")


def _fmt(v: Any) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v)
