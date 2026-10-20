"""Show each strategy deciding on the same hand-made situations.

Run from the project root:

    python -m scalix.algorithms.examples

Every scenario starts with 4 workers serving traffic and no recent scaling (unless the scenario
says otherwise). Demand is in requests/second, oldest interval first. These are illustrative
inputs only; they are not experiment results.
"""

from __future__ import annotations

from dataclasses import dataclass

from scalix.algorithms import ScalingContext, ScalingDecision, create_policy

STEADY = (5.0, 5.0, 5.0, 5.0, 5.0, 5.0)
RISING = (4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
REBOUND = (8.0, 7.0, 6.0, 5.0, 4.0, 7.0)   # falling, then a sudden 1.75x jump
FALLING = (9.0, 8.0, 7.0, 6.0, 5.0, 4.0)


@dataclass(frozen=True)
class Scenario:
    title: str
    ctx: ScalingContext


def _ctx(util: float, rates, util_history=None, active: int = 4, last_up: float = float("-inf"),
         time: float = 300.0) -> ScalingContext:
    history = util_history if util_history is not None else (util,)
    return ScalingContext(time=time, provisioned=active, active=active, booting=0,
                          utilization=util, utilization_history=history,
                          arrival_rate_history=rates, last_scale_up_time=last_up)


SCENARIOS = [
    Scenario("1. Calm: 45% busy, steady demand", _ctx(0.45, STEADY)),
    Scenario("2. Overloaded: 85% busy, steady demand", _ctx(0.85, STEADY)),
    Scenario("3. Demand rising steadily: 58% busy", _ctx(0.58, RISING)),
    Scenario("4. Sudden jump after falling demand: 65% busy", _ctx(0.65, REBOUND)),
    Scenario("5. Overloaded, but scaled up 10 s ago", _ctx(0.85, STEADY, last_up=290.0)),
    Scenario("6. Quiet for one interval only: 15% busy, falling demand",
             _ctx(0.15, FALLING, util_history=(0.50, 0.15))),
    Scenario("7. Quiet for two intervals: 15% busy, falling demand",
             _ctx(0.15, FALLING, util_history=(0.18, 0.15))),
    Scenario("8. Quiet (20% busy) but demand just jumped",
             _ctx(0.20, REBOUND, util_history=(0.25, 0.20))),
]

POLICIES = [create_policy("STATIC", worker_count=4), create_policy("THRESHOLD"), create_policy("SCALIX")]


def _short(d: ScalingDecision) -> str:
    if d.action.value == "NONE":
        return "hold" + (" (cooldown)" if d.cooldown_blocked else "")
    return f"{d.action.value.replace('SCALE_', 'scale ').lower()} {d.delta:+d}"


def main() -> None:
    names = [p.name for p in POLICIES]
    width = max(len(s.title) for s in SCENARIOS)
    print(f"{'Scenario':<{width}}  " + "  ".join(f"{n:<16}" for n in names))
    print("-" * (width + 2 + 18 * len(names)))
    for s in SCENARIOS:
        decisions = [p.decide(s.ctx) for p in POLICIES]
        print(f"{s.title:<{width}}  " + "  ".join(f"{_short(d):<16}" for d in decisions))
    print()
    for p in POLICIES[1:]:
        print(f"{p.name} reasons:")
        for s in SCENARIOS:
            print(f"  {s.title.split(':')[0].split('.')[0]}. {p.decide(s.ctx).reason}")
        print()


if __name__ == "__main__":
    main()
