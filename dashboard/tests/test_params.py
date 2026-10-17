import math

import pytest

from scalix_dash.params import ExperimentParams, properties_text


def test_defaults_are_valid():
    assert ExperimentParams().validate() == []


@pytest.mark.parametrize("changes, fragment", [
    ({"workloads": []}, "at least one workload"),
    ({"workloads": ["SPIKY"]}, "Unknown workload"),
    ({"workloads": ["LOW", "LOW"]}, "must not repeat"),
    ({"policies": []}, "at least one policy"),
    ({"policies": ["MAGIC"]}, "Unknown policy"),
    ({"duration_s": 10}, "Simulation duration"),
    ({"duration_s": math.nan}, "Simulation duration"),
    ({"initial_vms": 0}, "Initial VMs"),
    ({"initial_vms": 2.5}, "whole number"),
    ({"max_vms": 41}, "Maximum VMs"),
    ({"min_vms": 5, "max_vms": 4, "initial_vms": 4}, "Minimum VMs must not exceed"),
    ({"initial_vms": 25}, "Initial VMs must be between"),
    ({"lower_threshold": 0.7, "upper_threshold": 0.7}, "Lower threshold must be below"),
    ({"upper_threshold": 1.5}, "Upper threshold"),
    ({"lower_threshold": -0.1}, "Lower threshold"),
    ({"upper_threshold": 0.5}, "SCALIX preemptive threshold"),
    ({"observation_window": 1}, "Observation window"),
    ({"scale_up_cooldown_s": -1}, "Scale-up cooldown"),
    ({"scale_down_cooldown_s": "ten"}, "Scale-down cooldown"),
    ({"repetitions": 0}, "Repetitions"),
    ({"repetitions": 31}, "Repetitions"),
    ({"decision_interval_s": 1000, "duration_s": 600}, "Decision interval must not exceed"),
    ({"price_per_vm_hour": -1}, "Price per VM-hour"),
    ({"trend_threshold": True}, "Trend threshold"),
])
def test_invalid_parameters_are_reported(changes, fragment):
    errs = ExperimentParams(**changes).validate()
    assert any(fragment in e for e in errs), errs


def test_engine_properties_mapping():
    p = ExperimentParams(workloads=["BURST", "LOW"], policies=["STATIC", "SCALIX"], duration_s=600, initial_vms=5,
                         min_vms=2, max_vms=12, upper_threshold=0.8, lower_threshold=0.2, observation_window=4,
                         scale_up_cooldown_s=20, scale_down_cooldown_s=60, threshold_cooldown_s=15)
    props = p.to_engine_properties(seed=7)
    assert props["simulation.seed"] == "7"
    assert props["simulation.durationSeconds"] == "600"
    assert props["policy.static.vmCount"] == "5" and props["policy.scaling.initialVms"] == "5"
    assert props["policy.scaling.minVms"] == "2" and props["policy.scaling.maxVms"] == "12"
    assert props["policy.threshold.upper"] == props["policy.scalix.upper"] == "0.8"
    assert props["policy.threshold.lower"] == props["policy.scalix.lower"] == "0.2"
    assert props["policy.threshold.cooldownSeconds"] == "15"
    assert props["policy.scalix.observationWindow"] == "4"
    assert props["policy.scalix.scaleUpCooldownSeconds"] == "20"
    assert props["policy.scalix.scaleDownCooldownSeconds"] == "60"
    assert props["experiment.workloads"] == "BURST,LOW"
    assert props["experiment.policies"] == "STATIC,SCALIX"
    # Workload shape scales with duration (600 s -> burst at 200 s for 80 s, period 160 s).
    assert props["workload.burst.startSeconds"] == "200"
    assert props["workload.burst.durationSeconds"] == "80"
    assert props["workload.fluctuating.periodSeconds"] == "160"


def test_shape_at_900s_equals_part1_defaults():
    shape = ExperimentParams(duration_s=900).workload_shape()
    assert shape == {"workload.burst.startSeconds": 300, "workload.burst.durationSeconds": 120,
                     "workload.fluctuating.periodSeconds": 240}


def test_invalid_params_cannot_be_translated():
    with pytest.raises(ValueError):
        ExperimentParams(min_vms=0).to_engine_properties(1)


def test_seeds_one_per_repetition():
    assert ExperimentParams(base_seed=10, repetitions=3).seeds() == [10, 11, 12]


def test_dict_roundtrip_and_tolerant_parsing():
    p = ExperimentParams(workloads=["HIGH"], repetitions=4)
    assert ExperimentParams.from_dict(p.to_dict()) == p
    q = ExperimentParams.from_dict({"workloads": "low, burst", "policies": ["scalix"], "unknown_key": 1})
    assert q.workloads == ["LOW", "BURST"] and q.policies == ["SCALIX"]
    assert ExperimentParams.from_dict(None) == ExperimentParams()


def test_properties_text_is_sorted_with_header():
    text = properties_text({"b": "2", "a": "1"}, header="line one")
    assert text == "# line one\na=1\nb=2\n"
