"""Unit tests for the three scaling strategies and their shared types.

All tests are deterministic: fixed inputs, fixed expected outputs, no clock and no randomness
(the property test at the end uses a fixed seed). Nothing here assumes SCALIX is the best
strategy; each test checks one rule of one strategy.
"""

from __future__ import annotations

import math
import random
from dataclasses import replace

import pytest

from scalix.algorithms import (
    Action,
    CapacityLimits,
    POLICY_NAMES,
    ScalingContext,
    ScalingDecision,
    ScalixPolicy,
    StaticPolicy,
    ThresholdPolicy,
    Trend,
    create_policy,
    estimate_trend,
    spike_ratio,
)
from scalix.algorithms.base import fmt

STEADY = (5.0,) * 6
RISING = (4.0, 5.0, 6.0, 7.0, 8.0, 9.0)
FALLING = (9.0, 8.0, 7.0, 6.0, 5.0, 4.0)
REBOUND = (8.0, 7.0, 6.0, 5.0, 4.0, 7.0)  # falling trend, then a 1.75x jump


def ctx(util=0.5, active=4, booting=0, rates=STEADY, history=None, time=300.0,
        last_up=-math.inf, last_down=-math.inf, provisioned=None) -> ScalingContext:
    return ScalingContext(
        time=time,
        provisioned=active + booting if provisioned is None else provisioned,
        active=active, booting=booting, utilization=util,
        utilization_history=(util,) if history is None else history,
        arrival_rate_history=rates,
        last_scale_up_time=last_up, last_scale_down_time=last_down)


def low(util=0.15, **kw) -> ScalingContext:
    """Utilization low for two intervals in a row (enough for SCALIX's default caution)."""
    return ctx(util=util, history=(util, util), **kw)


# --------------------------------------------------------------------------- shared types

class TestSharedTypes:
    def test_capacity_limits_validation(self):
        with pytest.raises(ValueError, match="minimum"):
            CapacityLimits(0, 1, 2)
        with pytest.raises(ValueError, match="maximum"):
            CapacityLimits(3, 3, 2)
        with pytest.raises(ValueError, match="initial"):
            CapacityLimits(2, 5, 4)
        with pytest.raises(ValueError):
            CapacityLimits(True, 1, 2)
        assert CapacityLimits(2, 3, 5).clamp(9) == 5
        assert CapacityLimits(2, 3, 5).clamp(0) == 2

    @pytest.mark.parametrize("kwargs", [
        dict(util=1.5), dict(util=-0.1), dict(util=math.nan), dict(time=-1.0), dict(time=math.inf),
        dict(active=-1), dict(provisioned=2.0), dict(last_up=math.nan),
    ])
    def test_context_rejects_invalid_measurements(self, kwargs):
        with pytest.raises(ValueError):
            ctx(**kwargs)

    def test_context_freezes_histories(self):
        c = ctx(rates=[1.0, 2.0], history=[0.1])
        assert c.arrival_rate_history == (1.0, 2.0)
        assert c.utilization_history == (0.1,)

    @pytest.mark.parametrize("action, delta", [
        (Action.SCALE_UP, 0), (Action.SCALE_DOWN, 1), (Action.NONE, 1), (Action.SCALE_UP, 1.5),
    ])
    def test_decision_invariants(self, action, delta):
        with pytest.raises(ValueError):
            ScalingDecision(action, delta, "reason")

    def test_decision_needs_reason(self):
        with pytest.raises(ValueError, match="reason"):
            ScalingDecision.none("")

    @pytest.mark.parametrize("value, places, sign, text", [
        (0.5625, 3, False, "0.563"),      # exact half rounds up (Python's default would give 0.562)
        (0.1125, 3, False, "0.113"),
        (0.7, 2, False, "0.70"),
        (0.0, 3, True, "+0.000"),
        (-0.0, 3, True, "-0.000"),
        (-0.0694, 3, True, "-0.069"),
        (999.0, 3, True, "+999.000"),
        (math.inf, 2, False, "inf"),
    ])
    def test_reason_number_formatting(self, value, places, sign, text):
        assert fmt(value, places, sign) == text


# --------------------------------------------------------------------------- trend + spike

class TestTrendAndSpike:
    def test_directions(self):
        assert estimate_trend(RISING, 6, 0.03).trend is Trend.RISING
        assert estimate_trend(FALLING, 6, 0.03).trend is Trend.FALLING
        assert estimate_trend(STEADY, 6, 0.03).trend is Trend.STABLE

    @pytest.mark.parametrize("series", [(), (5.0,)])
    def test_too_little_history_is_unknown(self, series):
        assert estimate_trend(series, 6, 0.03).trend is Trend.UNKNOWN

    def test_uses_only_the_last_window_samples(self):
        est = estimate_trend((100.0, 0.0, 5.0, 5.0, 5.0), 3, 0.03)
        assert est.trend is Trend.STABLE and est.samples == 3

    def test_slope_is_relative_to_the_mean(self):
        small = estimate_trend((1.0, 2.0, 3.0), 3, 0.0).relative_slope
        large = estimate_trend((100.0, 200.0, 300.0), 3, 0.0).relative_slope
        assert small == pytest.approx(large) == pytest.approx(0.5)

    def test_slope_exactly_at_threshold_is_stable(self):
        rel = estimate_trend(RISING, 6, 0.0).relative_slope
        assert estimate_trend(RISING, 6, rel).trend is Trend.STABLE

    def test_zero_demand(self):
        assert estimate_trend((0.0, 0.0, 0.0), 6, 0.03).relative_slope == 0.0
        rising_from_zero = estimate_trend((0.0, 0.0, 0.0, 0.0), 6, 0.03)
        assert rising_from_zero.trend is Trend.STABLE
        est = estimate_trend((-1.0, 0.0, 1.0), 6, 0.03)  # zero mean, non-zero slope
        assert est.relative_slope == math.inf and est.trend is Trend.RISING

    @pytest.mark.parametrize("args", [((1.0, 2.0), 1, 0.03), ((1.0, 2.0), 6, -0.1),
                                      ((1.0, math.nan), 6, 0.03), ((1.0, math.inf), 6, 0.03)])
    def test_invalid_trend_input(self, args):
        with pytest.raises(ValueError):
            estimate_trend(*args)

    def test_spike_ratio(self):
        assert spike_ratio(()) is None
        assert spike_ratio((5.0,)) is None
        assert spike_ratio(REBOUND) == pytest.approx(1.75)
        assert spike_ratio(RISING) == pytest.approx(9 / 8)  # a steady ramp is not a spike
        assert spike_ratio((0.0, 3.0)) == math.inf
        assert spike_ratio((0.0, 0.0)) == 1.0


# --------------------------------------------------------------------------- Strategy A

class TestStatic:
    @pytest.mark.parametrize("c", [ctx(util=1.0), ctx(util=0.0), ctx(util=0.5, rates=RISING),
                                   ctx(util=0.0, active=1)])
    def test_never_scales(self, c):
        d = StaticPolicy(worker_count=4).decide(c)
        assert d.action is Action.NONE and d.delta == 0
        assert "fixed at 4" in d.reason

    def test_limits_are_fixed(self):
        assert StaticPolicy(worker_count=3).limits == CapacityLimits(3, 3, 3)

    @pytest.mark.parametrize("bad", [0, -1, 2.5])
    def test_invalid_count(self, bad):
        with pytest.raises(ValueError):
            StaticPolicy(worker_count=bad)


# --------------------------------------------------------------------------- Strategy B

class TestThreshold:
    policy = ThresholdPolicy()  # upper 0.70, lower 0.30, cooldown 60 s, capacity 1..20

    def test_defaults(self):
        p = self.policy
        assert (p.upper, p.lower, p.cooldown_seconds) == (0.70, 0.30, 60.0)

    @pytest.mark.parametrize("util, action", [
        (0.70, Action.NONE), (0.7000001, Action.SCALE_UP), (1.0, Action.SCALE_UP),
        (0.30, Action.NONE), (0.2999999, Action.SCALE_DOWN), (0.0, Action.SCALE_DOWN),
        (0.50, Action.NONE),
    ])
    def test_threshold_boundaries(self, util, action):
        d = self.policy.decide(ctx(util=util))
        assert d.action is action
        assert d.delta in (-1, 0, 1)

    def test_custom_thresholds(self):
        p = ThresholdPolicy(upper=0.9, lower=0.1)
        assert p.decide(ctx(util=0.85)).action is Action.NONE
        assert p.decide(ctx(util=0.95)).action is Action.SCALE_UP

    def test_maximum_capacity(self):
        p = ThresholdPolicy(capacity=CapacityLimits(1, 4, 4))
        d = p.decide(ctx(util=0.95, active=4))
        assert d.action is Action.NONE and "max 4" in d.reason

    def test_minimum_capacity(self):
        p = ThresholdPolicy(capacity=CapacityLimits(2, 2, 10))
        d = p.decide(ctx(util=0.05, active=2))
        assert d.action is Action.NONE and "min 2" in d.reason

    def test_cooldown_blocks_and_expires(self):
        blocked = self.policy.decide(ctx(util=0.9, time=300, last_up=270))
        assert blocked.action is Action.NONE and blocked.cooldown_blocked
        exactly_over = self.policy.decide(ctx(util=0.9, time=300, last_up=240))
        assert exactly_over.action is Action.SCALE_UP and not exactly_over.cooldown_blocked

    def test_cooldown_is_shared_by_both_directions(self):
        d = self.policy.decide(ctx(util=0.1, time=300, last_up=280))
        assert d.action is Action.NONE and d.cooldown_blocked
        d = self.policy.decide(ctx(util=0.9, time=300, last_down=280))
        assert d.action is Action.NONE and d.cooldown_blocked

    def test_zero_cooldown_never_blocks(self):
        p = ThresholdPolicy(cooldown_seconds=0)
        assert p.decide(ctx(util=0.9, time=300, last_up=300)).action is Action.SCALE_UP

    def test_cooldown_prevents_repeated_scaling_while_overloaded(self):
        """Overloaded for 3 minutes with 15 s decisions: 60 s cooldown -> one step per minute."""
        p = self.policy
        workers, last_up, ups = 4, -math.inf, 0
        for t in range(0, 180, 15):
            d = p.decide(ctx(util=0.9, active=workers, time=float(t), last_up=last_up))
            if d.action is Action.SCALE_UP:
                workers += d.delta
                last_up = float(t)
                ups += 1
        assert ups == 3  # at t = 0, 60 and 120

    @pytest.mark.parametrize("kwargs", [dict(upper=0.3, lower=0.7), dict(upper=0.5, lower=0.5),
                                        dict(upper=1.2), dict(lower=-0.1), dict(cooldown_seconds=-1),
                                        dict(cooldown_seconds=math.inf)])
    def test_invalid_parameters(self, kwargs):
        with pytest.raises(ValueError):
            ThresholdPolicy(**kwargs)


# --------------------------------------------------------------------------- Strategy C

class TestScalixScaleUp:
    policy = ScalixPolicy()

    def test_overloaded_steady_demand_sizes_the_step(self):
        # 4 workers at 85%: busy = 3.4 workers -> 3.4 / 0.60 target = 5.67 -> 6 workers -> +2
        d = self.policy.decide(ctx(util=0.85))
        assert (d.action, d.delta, d.trend) == (Action.SCALE_UP, 2, Trend.STABLE)
        assert d.reason.startswith("utilization 0.850 >= upper 0.70")

    def test_rising_demand_scales_up_before_the_upper_threshold(self):
        # 58% is below 70%, but demand rises ~15% per interval -> projected 2 intervals ahead.
        d = self.policy.decide(ctx(util=0.58, rates=RISING))
        assert (d.action, d.delta, d.trend) == (Action.SCALE_UP, 2, Trend.RISING)
        assert "demand rising" in d.reason
        assert ThresholdPolicy().decide(ctx(util=0.58, rates=RISING)).action is Action.NONE

    @pytest.mark.parametrize("rates", [STEADY, FALLING])
    def test_no_early_scale_up_without_rising_demand(self, rates):
        assert self.policy.decide(ctx(util=0.60, rates=rates)).action is Action.NONE

    def test_rising_demand_below_preemptive_threshold_waits(self):
        assert self.policy.decide(ctx(util=0.50, rates=RISING)).action is Action.NONE

    def test_sudden_spike_triggers_early_scale_up_even_when_trend_is_falling(self):
        d = self.policy.decide(ctx(util=0.65, rates=REBOUND))
        assert d.trend is Trend.FALLING and d.spike
        assert (d.action, d.delta) == (Action.SCALE_UP, 1)
        assert "demand spiked" in d.reason and "1.75x" in d.reason

    def test_spike_detection_can_be_switched_off(self):
        d = ScalixPolicy(spike_factor=None).decide(ctx(util=0.65, rates=REBOUND))
        assert d.action is Action.NONE and not d.spike

    def test_small_jump_is_not_a_spike(self):
        d = self.policy.decide(ctx(util=0.65, rates=(5.0, 5.0, 5.0, 5.0, 5.0, 6.0)))
        assert not d.spike

    def test_spike_from_zero(self):
        d = self.policy.decide(ctx(util=0.6, rates=(0.0, 0.0, 0.0, 0.0, 0.0, 3.0)))
        assert d.spike and "jumped from zero" in d.reason
        assert d.action is Action.SCALE_UP

    def test_step_is_capped(self):
        assert self.policy.decide(ctx(util=1.0)).delta == 3  # wants 7 - 4 = 3, cap 3
        assert ScalixPolicy(max_scale_up_step=1).decide(ctx(util=1.0)).delta == 1
        near_max = ScalixPolicy(capacity=CapacityLimits(1, 8, 10)).decide(ctx(util=1.0, active=9))
        assert near_max.delta == 1

    def test_never_exceeds_maximum(self):
        p = ScalixPolicy(capacity=CapacityLimits(1, 4, 4))
        d = p.decide(ctx(util=1.0, active=4, rates=RISING))
        assert d.action is Action.NONE and "max 4" in d.reason

    def test_booting_workers_count_towards_capacity(self):
        d = self.policy.decide(ctx(util=0.58, active=4, booting=2, rates=RISING))
        assert d.action is Action.NONE and "booting" in d.reason

    def test_overload_always_adds_at_least_one(self):
        # Booting workers seem to cover demand, but saturated CPU hides the true demand.
        d = self.policy.decide(ctx(util=0.72, active=4, booting=3))
        assert (d.action, d.delta) == (Action.SCALE_UP, 1)

    def test_scale_up_cooldown(self):
        blocked = self.policy.decide(ctx(util=0.9, time=300, last_up=280))
        assert blocked.action is Action.NONE and blocked.cooldown_blocked
        assert "suppressed by cooldown" in blocked.reason
        allowed = self.policy.decide(ctx(util=0.9, time=300, last_up=270))  # exactly 30 s
        assert allowed.action is Action.SCALE_UP

    def test_recent_scale_down_does_not_block_scale_up(self):
        assert self.policy.decide(ctx(util=0.9, time=300, last_down=295)).action is Action.SCALE_UP


class TestScalixScaleDown:
    policy = ScalixPolicy()

    @pytest.mark.parametrize("rates", [FALLING, STEADY])
    def test_scales_down_one_when_low_and_demand_not_rising(self, rates):
        d = self.policy.decide(low(rates=rates, active=6))
        assert (d.action, d.delta) == (Action.SCALE_DOWN, -1)

    @pytest.mark.parametrize("rates, word", [(RISING, "RISING"), ((3.0,), "UNKNOWN")])
    def test_holds_when_demand_rising_or_unknown(self, rates, word):
        d = self.policy.decide(low(rates=rates, active=6))
        assert d.action is Action.NONE and word in d.reason

    def test_holds_when_demand_just_spiked(self):
        d = self.policy.decide(low(util=0.2, rates=REBOUND, active=6))
        assert d.action is Action.NONE and "just spiked" in d.reason
        assert ThresholdPolicy().decide(ctx(util=0.2, rates=REBOUND)).action is Action.SCALE_DOWN

    def test_waits_for_utilization_to_stay_low(self):
        once = self.policy.decide(ctx(util=0.15, rates=FALLING, history=(0.5, 0.15)))
        assert once.action is Action.NONE and "(1 so far)" in once.reason
        no_history = self.policy.decide(ctx(util=0.15, rates=FALLING, history=()))
        assert no_history.action is Action.NONE and "(0 so far)" in no_history.reason
        twice = self.policy.decide(ctx(util=0.15, rates=FALLING, history=(0.5, 0.2, 0.15)))
        assert twice.action is Action.SCALE_DOWN

    def test_low_intervals_required_is_configurable(self):
        assert ScalixPolicy(low_intervals_required=1).decide(
            ctx(util=0.15, rates=FALLING, history=(0.5, 0.15))).action is Action.SCALE_DOWN
        three = ScalixPolicy(low_intervals_required=3)
        assert three.decide(ctx(util=0.15, rates=FALLING, history=(0.5, 0.2, 0.15))).action is Action.NONE
        assert three.decide(ctx(util=0.15, rates=FALLING, history=(0.2, 0.2, 0.15))).action is Action.SCALE_DOWN

    def test_scale_down_cooldown_counts_from_any_action(self):
        after_up = self.policy.decide(low(time=300, last_up=250))
        assert after_up.action is Action.NONE and after_up.cooldown_blocked
        after_down = self.policy.decide(low(time=300, last_down=250))
        assert after_down.action is Action.NONE and after_down.cooldown_blocked
        ok = self.policy.decide(low(time=300, last_up=210, last_down=210))  # exactly 90 s
        assert ok.action is Action.SCALE_DOWN

    def test_refuses_scale_down_that_would_overload_the_rest(self):
        # 2 workers at 30%: one worker would run at 60% = target -> allowed
        assert self.policy.decide(low(util=0.30, active=2)).action is Action.SCALE_DOWN
        # same load with a lower target: 60% > 55% -> refused (prevents add/remove flapping)
        p = ScalixPolicy(target_utilization=0.55)
        d = p.decide(low(util=0.30, active=2))
        assert d.action is Action.NONE and "raise utilization to 0.600" in d.reason

    def test_never_removes_the_last_worker(self):
        d = self.policy.decide(low(util=0.0, active=1))
        assert d.action is Action.NONE and "min 1" in d.reason

    def test_only_one_serving_worker(self):
        d = self.policy.decide(low(util=0.1, active=1, provisioned=2))
        assert d.action is Action.NONE and "only one serving VM" in d.reason

    def test_respects_configured_minimum(self):
        p = ScalixPolicy(capacity=CapacityLimits(3, 4, 10))
        assert p.decide(low(util=0.0, active=3, rates=FALLING)).action is Action.NONE
        assert p.decide(low(util=0.0, active=4, rates=FALLING)).action is Action.SCALE_DOWN

    def test_holds_inside_the_band(self):
        d = self.policy.decide(ctx(util=0.45))
        assert d.action is Action.NONE and "within band" in d.reason


class TestScalixParameters:
    @pytest.mark.parametrize("kwargs", [
        dict(upper=0.3, lower=0.7),
        dict(preemptive_upper=0.80), dict(preemptive_upper=0.25),
        dict(target_utilization=0.9), dict(target_utilization=0.3),
        dict(observation_window=1), dict(trend_threshold=-0.1), dict(projection_intervals=-1),
        dict(scale_up_cooldown_seconds=-5), dict(scale_down_cooldown_seconds=math.inf),
        dict(max_scale_up_step=0), dict(spike_factor=1.0), dict(spike_factor=math.inf),
        dict(low_intervals_required=0), dict(capacity=(1, 4, 8)),
    ])
    def test_invalid_parameters(self, kwargs):
        with pytest.raises(ValueError):
            ScalixPolicy(**kwargs)

    def test_defaults_match_the_documented_starting_values(self):
        p = ScalixPolicy()
        assert (p.upper, p.lower, p.preemptive_upper, p.target_utilization) == (0.70, 0.30, 0.55, 0.60)
        assert (p.scale_up_cooldown_seconds, p.scale_down_cooldown_seconds) == (30.0, 90.0)
        assert (p.spike_factor, p.low_intervals_required) == (1.5, 2)


# --------------------------------------------------------------------------- factory

class TestFactory:
    def test_names(self):
        assert POLICY_NAMES == ("STATIC", "THRESHOLD", "SCALIX")
        assert isinstance(create_policy(" scalix "), ScalixPolicy)

    def test_flat_capacity_keys(self):
        p = create_policy("THRESHOLD", min_capacity=2, max_capacity=6, initial_capacity=3)
        assert p.limits == CapacityLimits(2, 3, 6)

    @pytest.mark.parametrize("name", POLICY_NAMES)
    def test_recorded_parameters_rebuild_the_same_policy(self, name):
        p = create_policy(name)
        assert create_policy(name, **p.parameters()) == p

    @pytest.mark.parametrize("call", [
        lambda: create_policy("MAGIC"),
        lambda: create_policy("THRESHOLD", uper=0.8),
        lambda: create_policy("STATIC", min_capacity=2),
        lambda: create_policy("SCALIX", capacity=CapacityLimits(), max_capacity=5),
    ])
    def test_bad_requests_raise(self, call):
        with pytest.raises(ValueError):
            call()


# --------------------------------------------------------------------------- properties

def test_every_decision_is_explained_bounded_and_deterministic():
    """5,000 random situations per strategy: every decision has a reason, stays within the
    capacity limits, and is identical when repeated."""
    rng = random.Random(20261020)
    policies = [StaticPolicy(worker_count=5), ThresholdPolicy(capacity=CapacityLimits(2, 4, 10)),
                ScalixPolicy(capacity=CapacityLimits(2, 4, 10))]
    for policy in policies:
        lim = policy.limits
        for _ in range(5000):
            active = rng.randint(lim.minimum, lim.maximum)
            booting = rng.randint(0, lim.maximum - active)
            u = rng.choice([0.0, 0.3, 0.55, 0.7, 1.0, rng.random()])
            t = rng.uniform(0, 1000)
            c = ctx(util=u, active=active, booting=booting, time=t,
                    history=tuple(rng.random() for _ in range(rng.randint(0, 4))) + (u,),
                    rates=tuple(rng.uniform(0, 20) for _ in range(rng.randint(0, 8))),
                    last_up=rng.choice([-math.inf, t - rng.uniform(0, 200)]),
                    last_down=rng.choice([-math.inf, t - rng.uniform(0, 200)]))
            d = policy.decide(c)
            assert d.reason
            assert lim.minimum <= c.provisioned + d.delta <= lim.maximum, (policy.name, c, d)
            assert policy.decide(c) == d
            if d.cooldown_blocked:
                assert d.action is Action.NONE


def test_examples_script_runs(capsys):
    from scalix.algorithms import examples

    examples.main()
    out = capsys.readouterr().out
    rows = {line.split("  ")[0]: line for line in out.splitlines() if line[:2] in {f"{i}." for i in range(1, 9)}}
    assert len(rows) == 8
    assert "scale up +2" in rows["3. Demand rising steadily: 58% busy"]
    assert rows["3. Demand rising steadily: 58% busy"].count("hold") == 2  # STATIC and THRESHOLD hold
