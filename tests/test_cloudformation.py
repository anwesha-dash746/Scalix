"""Phase 8 CloudFormation template: checked offline for the budget and safety promises in
docs/PHASE8_AWS_PLAN.md. Nothing here contacts AWS."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "aws" / "cloudformation" / "scalix-workers.yaml"


class _CfnLoader(yaml.SafeLoader):
    """Reads CloudFormation short-form tags (!Ref, !Sub, ...) as plain values."""


def _tag(loader, suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return {suffix: loader.construct_scalar(node)}
    if isinstance(node, yaml.SequenceNode):
        return {suffix: loader.construct_sequence(node, deep=True)}
    return {suffix: loader.construct_mapping(node, deep=True)}


_CfnLoader.add_multi_constructor("!", _tag)


@pytest.fixture(scope="module")
def template():
    return yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)


def resources(template, kind=None):
    return {k: v for k, v in template["Resources"].items() if kind is None or v["Type"] == kind}


def test_group_is_capped_at_two_t3_workers_and_named_as_the_controller_expects(template):
    (group,) = resources(template, "AWS::AutoScaling::AutoScalingGroup").values()
    p = group["Properties"]
    assert (p["MinSize"], p["MaxSize"], p["DesiredCapacity"]) == ("1", "2", "1")
    assert p["AutoScalingGroupName"] == "scalix-workers"
    assert "ScalingPolicy" not in str(template)  # only SCALIX's controller changes the group
    params = template["Parameters"]
    assert params["InstanceType"]["Default"] == "t3.micro"
    assert set(params["InstanceType"]["AllowedValues"]) <= {"t3.nano", "t3.micro"}
    assert params["LoadMinutes"]["MaxValue"] <= 40


def test_no_billable_extras_no_iam_no_inbound_access(template):
    kinds = {r["Type"] for r in resources(template).values()}
    forbidden = {"AWS::ElasticLoadBalancingV2::LoadBalancer", "AWS::ElasticLoadBalancing::LoadBalancer",
                 "AWS::EC2::NatGateway", "AWS::EC2::EIP", "AWS::IAM::Role", "AWS::IAM::InstanceProfile",
                 "AWS::IAM::Policy", "AWS::RDS::DBInstance", "AWS::CloudWatch::Alarm"}
    assert not kinds & forbidden
    assert all(not t.startswith("AWS::IAM::") for t in kinds)
    (sg,) = resources(template, "AWS::EC2::SecurityGroup").values()
    assert "SecurityGroupIngress" not in sg["Properties"]
    assert not resources(template, "AWS::EC2::SecurityGroupIngress")


def test_launch_template_uses_free_monitoring_and_a_small_deleted_on_termination_disk(template):
    (lt,) = resources(template, "AWS::EC2::LaunchTemplate").values()
    data = lt["Properties"]["LaunchTemplateData"]
    # 1-minute (billed) monitoring only when DetailedMonitoring=true; the default is the free 5-minute data
    assert data["Monitoring"]["Enabled"] == {"If": ["UseDetailedMonitoring", True, False]}
    assert template["Parameters"]["DetailedMonitoring"]["Default"] == "false"
    assert template["Conditions"]["UseDetailedMonitoring"] == {"Equals": [{"Ref": "DetailedMonitoring"}, "true"]}
    assert data["CreditSpecification"]["CpuCredits"] == "unlimited"
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    (disk,) = data["BlockDeviceMappings"]
    assert disk["Ebs"]["VolumeSize"] <= 8 and disk["Ebs"]["DeleteOnTermination"] is True
    # the AWS Academy sandbox allows only gp2 volumes of at most 35 GB
    assert disk["Ebs"]["VolumeType"] == "gp2" and disk["Ebs"]["VolumeSize"] <= 35
    assert "KeyName" not in data  # no SSH key: nobody logs in


def test_plan_and_config_example_match_the_template():
    plan = (ROOT / "docs" / "PHASE8_AWS_PLAN.md").read_text()
    assert "scalix-workers" in plan and "USD 20" in plan and "DELETE_COMPLETE" in plan
    assert "--apply --confirm scalix-workers" in plan and "touch config/LIVE_DISABLED" in plan


def test_demo_load_pattern_is_optional_and_minute_aligned(template):
    """LoadPattern repeats one CPU percentage per clock minute; empty keeps the constant LoadPercent."""
    import re

    p = template["Parameters"]["LoadPattern"]
    assert p["Default"] == ""
    ok = re.compile(p["AllowedPattern"])
    assert ok.match("") and ok.match("40,90,80") and ok.match("85")
    assert not ok.match("40, 90") and not ok.match("100") and not ok.match("40;90") and not ok.match("1,2,3,4,5,6,7,8,9,1,2,3,4")
    user_data = str(template)
    assert '"${LoadPattern}"' in user_data and "time.time() // 60" in user_data and "min(95.0" in user_data


def test_demo_load_script_follows_the_pattern_per_minute(template, monkeypatch):
    """Run the worker's own load-target function (extracted from UserData) against a fake clock."""
    import re
    import textwrap

    (lt,) = resources(template, "AWS::EC2::LaunchTemplate").values()
    sub = lt["Properties"]["LaunchTemplateData"]["UserData"]["Fn::Base64"]["Sub"]
    script = textwrap.dedent(sub.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0])
    namespace: dict = {}
    exec(compile(re.split(r"\ndef burn", script)[0], "scalix-load.py", "exec"), namespace)
    target = namespace["target"]
    fake = {"t": 0.0}
    monkeypatch.setattr(namespace["time"], "time", lambda: fake["t"])
    seen = []
    for minute in range(7):
        fake["t"] = 1_800_000_000 - 1_800_000_000 % 180 + minute * 60 + 30   # minute index divisible by 3 at 0
        seen.append(target(85.0, [40.0, 90.0, 80.0]))
    assert seen == [40.0, 90.0, 80.0, 40.0, 90.0, 80.0, 40.0]
    assert target(85.0, []) == 85.0
