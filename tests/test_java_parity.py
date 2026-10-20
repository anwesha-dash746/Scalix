"""Parity: the Python policies make the same decisions as the original Java policies.

Thousands of random and boundary contexts are written to a file, decided by the Java classes
inside the engine jar (through tests/java/PolicyParity.java, compiled with plain ``javac``; no
Maven needed), and decided again by the Python port. Action, step size, trend, slope,
cooldown flag and the reason text must all match exactly.

The Python SCALIX additions (spike detection, sustained-low scale-down) are switched off here,
because the Java policy has neither. They are covered by the unit tests in test_policies.py.

Skipped, with the reason shown, if ``javac``/``java`` or the jar is missing. Build the jar with
``./mvnw -B package``.
"""

from __future__ import annotations

import math
import random
import shutil
import subprocess
from pathlib import Path

import pytest

from scalix.algorithms import (
    CapacityLimits,
    ScalingContext,
    ScalixPolicy,
    StaticPolicy,
    ThresholdPolicy,
)

ROOT = Path(__file__).resolve().parent.parent
JAR = ROOT / "target" / "scalix-1.0.0-SNAPSHOT-all.jar"
HARNESS = ROOT / "tests" / "java" / "PolicyParity.java"

pytestmark = [
    pytest.mark.java,
    pytest.mark.skipif(shutil.which("javac") is None or shutil.which("java") is None,
                       reason="javac/java not on PATH (JDK 17+ needed for the Java parity tests)"),
    pytest.mark.skipif(not JAR.is_file(), reason=f"engine jar not found at {JAR}; build it with ./mvnw -B package"),
]

CONTEXTS_PER_POLICY = 3000


@pytest.fixture(scope="module")
def harness_classes(tmp_path_factory) -> Path:
    out = tmp_path_factory.mktemp("parity-classes")
    result = subprocess.run(["javac", "-cp", str(JAR), "-d", str(out), str(HARNESS)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 0, result.stderr
    return out


def _num(x: float) -> str:
    """Format a float so Java's Double.parseDouble reads back exactly the same value."""
    if math.isinf(x):
        return "Infinity" if x > 0 else "-Infinity"
    return repr(float(x))


def _random_rates(rng: random.Random) -> list[float]:
    n = rng.choice([0, 1, 2, 3, 4, 5, 6, 6, 6, 7, 8, 10])
    kind = rng.choice(["random", "rising", "falling", "flat", "zeros", "from_zero", "spike"])
    base = rng.uniform(0.5, 15)
    if kind == "random":
        return [rng.uniform(0, 20) for _ in range(n)]
    if kind == "rising":
        return [base + i * rng.uniform(0, 2) for i in range(n)]
    if kind == "falling":
        return [max(0.0, base + 10 - i * rng.uniform(0, 2)) for i in range(n)]
    if kind == "flat":
        return [base] * n
    if kind == "zeros":
        return [0.0] * n
    if kind == "from_zero":
        return [0.0] * max(0, n - 1) + ([base] if n else [])
    return [base] * max(0, n - 1) + ([base * rng.uniform(1, 3)] if n else [])


def _random_context(rng: random.Random, limits: CapacityLimits, target: float = 0.6) -> ScalingContext:
    t = rng.choice([0.0, rng.uniform(0, 1000), 300.0])
    active = rng.randint(0, limits.maximum + 2)
    booting = rng.choice([0, 0, 0, 1, 2, 3])
    boundary = [0.0, 0.3, 0.29999999, 0.30000001, 0.5, 0.55, 0.6, 0.65, 0.7, 0.69999999, 0.70000001, 1.0]
    roll = rng.random()
    if roll < 0.05:
        # Scale-down boundary: removing one of 2 workers would put the other exactly at the target.
        active, booting, u = 2, 0, target / 2
    elif roll < 0.35:
        u = rng.choice(boundary)
    elif roll < 0.5 and active > 0:
        # Load that needs exactly a whole number of workers at the target utilization, where
        # floating-point rounding decides between n and n + 1 workers.
        u = min(1.0, target * rng.randint(1, active + 3) / active)
    else:
        u = rng.random()

    def last() -> float:
        if rng.random() < 0.4:
            return -math.inf
        return t - rng.choice([0.0, 10.0, 15.0, 29.999, 30.0, 45.0, 60.0, 89.9, 90.0, 120.0, rng.uniform(0, 300)])

    return ScalingContext(time=t, provisioned=active + booting, active=active, booting=booting,
                          utilization=u, utilization_history=(u,), arrival_rate_history=_random_rates(rng),
                          last_scale_up_time=last(), last_scale_down_time=last())


def _spec(policy) -> str:
    if isinstance(policy, StaticPolicy):
        return f"STATIC\t{policy.worker_count}"
    c = policy.capacity
    head = f"{c.minimum}\t{c.initial}\t{c.maximum}"
    if isinstance(policy, ThresholdPolicy):
        return "\t".join(["THRESHOLD", head, _num(policy.upper), _num(policy.lower), _num(policy.cooldown_seconds)])
    p = policy
    return "\t".join(["SCALIX", head] + [_num(v) for v in (p.upper, p.lower, p.preemptive_upper, p.target_utilization)]
                     + [str(p.observation_window), _num(p.trend_threshold), str(p.projection_intervals),
                        _num(p.scale_up_cooldown_seconds), _num(p.scale_down_cooldown_seconds),
                        str(p.max_scale_up_step)])


JAVA_COMPATIBLE = dict(spike_factor=None, low_intervals_required=1)

POLICIES = {
    "static": StaticPolicy(worker_count=8),
    "threshold-java-defaults": ThresholdPolicy(cooldown_seconds=0.0),
    "threshold-cooldown": ThresholdPolicy(capacity=CapacityLimits(2, 4, 12), upper=0.8, lower=0.25,
                                          cooldown_seconds=45.0),
    "scalix-java-defaults": ScalixPolicy(**JAVA_COMPATIBLE),
    "scalix-variant": ScalixPolicy(capacity=CapacityLimits(2, 4, 12), upper=0.75, lower=0.25,
                                   preemptive_upper=0.5, target_utilization=0.65, observation_window=4,
                                   trend_threshold=0.05, projection_intervals=3,
                                   scale_up_cooldown_seconds=20.0, scale_down_cooldown_seconds=60.0,
                                   max_scale_up_step=2, **JAVA_COMPATIBLE),
}


@pytest.mark.parametrize("key", list(POLICIES))
def test_python_matches_java(key, harness_classes, tmp_path):
    policy = POLICIES[key]
    rng = random.Random(f"scalix-parity-{key}")
    limits = policy.limits
    target = getattr(policy, "target_utilization", 0.6)
    contexts = [_random_context(rng, limits, target) for _ in range(CONTEXTS_PER_POLICY)]

    lines = [_spec(policy)]
    for c in contexts:
        rates = ",".join(_num(r) for r in c.arrival_rate_history) or "-"
        lines.append("\t".join([_num(c.time), str(c.provisioned), str(c.active), str(c.booting),
                                _num(c.utilization), rates, _num(c.last_scale_up_time),
                                _num(c.last_scale_down_time)]))
    infile = tmp_path / "contexts.tsv"
    infile.write_text("\n".join(lines) + "\n", encoding="utf-8")

    sep = ";" if subprocess.os.name == "nt" else ":"
    result = subprocess.run(["java", "-cp", f"{JAR}{sep}{harness_classes}", "PolicyParity", str(infile)],
                            capture_output=True, text=True, timeout=300)
    assert result.returncode == 0, result.stderr
    java_rows = result.stdout.splitlines()
    assert len(java_rows) == len(contexts)

    mismatches = []
    actions = set()
    for i, (ctx, row) in enumerate(zip(contexts, java_rows)):
        action, delta, trend, slope, blocked, reason = row.split("\t", 5)
        py = policy.decide(ctx)
        actions.add(py.action.value)
        java = (action, int(delta), trend, float(slope), blocked == "true", reason)
        mine = (py.action.value, py.delta, py.trend.value, py.relative_slope, py.cooldown_blocked, py.reason)
        if java != mine:
            mismatches.append((i, ctx, java, mine))
    assert not mismatches, f"{len(mismatches)} mismatches; first: {mismatches[0]}"
    if not isinstance(policy, StaticPolicy):
        # The random contexts must exercise every kind of decision, or the comparison proves little.
        assert actions == {"SCALE_UP", "SCALE_DOWN", "NONE"}
