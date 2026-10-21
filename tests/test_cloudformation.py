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
    assert data["Monitoring"]["Enabled"] is False
    assert data["CreditSpecification"]["CpuCredits"] == "unlimited"
    assert data["MetadataOptions"]["HttpTokens"] == "required"
    (disk,) = data["BlockDeviceMappings"]
    assert disk["Ebs"]["VolumeSize"] <= 8 and disk["Ebs"]["DeleteOnTermination"] is True
    assert "KeyName" not in data  # no SSH key: nobody logs in


def test_plan_and_config_example_match_the_template():
    plan = (ROOT / "docs" / "PHASE8_AWS_PLAN.md").read_text()
    assert "scalix-workers" in plan and "USD 20" in plan and "DELETE_COMPLETE" in plan
    assert "--apply --confirm scalix-workers" in plan and "touch config/LIVE_DISABLED" in plan
