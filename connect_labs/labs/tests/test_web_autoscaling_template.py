"""Static checks on infra/labs-web-autoscaling.yml.

The stack adopts the web tier's hand-registered scaling by IMPORT. Two properties
keep that safe and are only enforced at apply time, so they are pinned here:

- every resource is Retain: CloudFormation refuses an import without a
  DeletionPolicy, and deleting the stack must never remove the tier's scaling
  (desiredCount would freeze wherever it was);
- the floor stays at 2: canopy-web and ace-web share the cluster and ALB, and a
  single-task labs tier has zero healthy targets during any replacement or OOM.
"""

from pathlib import Path

import yaml

TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "infra" / "labs-web-autoscaling.yml"


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation's short-form intrinsics (!Ref, !Sub, ...)."""


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _doc():
    return yaml.load(TEMPLATE_PATH.read_text(), Loader=_CfnLoader)


def test_every_resource_is_retained():
    resources = _doc()["Resources"]
    assert resources
    for logical_id, resource in resources.items():
        assert resource.get("DeletionPolicy") == "Retain", logical_id
        assert resource.get("UpdateReplacePolicy") == "Retain", logical_id


def test_min_capacity_floor_is_two():
    params = _doc()["Parameters"]["MinCapacity"]
    assert params["Default"] >= 2
    assert params["MinValue"] == 2


def test_owns_one_target_and_both_live_policies():
    resources = _doc()["Resources"]
    types = sorted(r["Type"] for r in resources.values())
    assert types == [
        "AWS::ApplicationAutoScaling::ScalableTarget",
        "AWS::ApplicationAutoScaling::ScalingPolicy",
        "AWS::ApplicationAutoScaling::ScalingPolicy",
    ]
    metrics = sorted(
        r["Properties"]["TargetTrackingScalingPolicyConfiguration"]["PredefinedMetricSpecification"][
            "PredefinedMetricType"
        ]
        for r in resources.values()
        if r["Type"] == "AWS::ApplicationAutoScaling::ScalingPolicy"
    )
    assert metrics == ["ECSServiceAverageCPUUtilization", "ECSServiceAverageMemoryUtilization"]
