"""Static checks on infra/labs-monitoring.yml that CloudFormation only enforces at APPLY time.

Both rules here cost a real deploy to learn.

**Description length.** `aws cloudformation validate-template` passes a template
whose AlarmDescription is over AWS's 1024-character limit — the constraint is
per-resource, checked by the alarm handler, so the first sign of trouble is a
CREATE_FAILED mid-update and an automatic stack rollback. That happened on
2026-09-07 with a 1058-character description. The rollback was clean, but the
whole update was lost and the change set had to be rebuilt. Four descriptions in
this file are already within ~90 characters of the ceiling, and they are
deliberately long: each one is the ONLY briefing a responder gets, pasted into
mail that pages an agent mailbox and read cold. So they will keep growing, and
this is the check that keeps them shippable.

**N-of-N.** `EvaluationPeriods > 1` with no `DatapointsToAlarm` means N-of-N: N
breaching datapoints to fire, but a SINGLE sub-threshold datapoint to clear. The
noisy half (one incident paging many times) is the obvious one; the dangerous
half is the false all-clear — the alarm goes OK while the condition is still
happening, and returning to ALARM then needs N *consecutive* breaches, so a
condition that settles just under the threshold is invisible indefinitely and
nothing pages about it. Fixed once per alarm in #1419, then again in #1429 for
three more that had been written by copying a neighbour. This encodes it so the
next copied alarm cannot reintroduce it silently.
"""

from pathlib import Path

import pytest
import yaml

TEMPLATE_PATH = Path(__file__).resolve().parents[3] / "infra" / "labs-monitoring.yml"

# AWS's hard limit on AlarmDescription, for both AWS::CloudWatch::Alarm and
# AWS::CloudWatch::CompositeAlarm.
MAX_ALARM_DESCRIPTION = 1024

ALARM_TYPES = {"AWS::CloudWatch::Alarm", "AWS::CloudWatch::CompositeAlarm"}

# labs-jj-web-no-healthy-targets is N-of-N on purpose. #1429 fixed the class and
# deliberately held this one back rather than changing it as a ride-along: it has
# a 60-second period and is availability-critical, so the hysteresis trade is a
# different decision from the saturation alarms'. Named here rather than silently
# skipped, so removing it from this set is a conscious act.
DELIBERATE_N_OF_N = {"labs-jj-web-no-healthy-targets"}


class _CfnLoader(yaml.SafeLoader):
    """SafeLoader that tolerates CloudFormation's short-form intrinsics (!Ref, !Sub, ...)."""


_CfnLoader.add_multi_constructor("!", lambda loader, suffix, node: None)


def _alarms():
    """Yield (logical_id, alarm_name, properties) for every alarm in the template."""
    doc = yaml.load(TEMPLATE_PATH.read_text(), Loader=_CfnLoader)
    for logical_id, resource in doc["Resources"].items():
        if resource.get("Type") not in ALARM_TYPES:
            continue
        props = resource.get("Properties", {})
        yield logical_id, props.get("AlarmName", logical_id), props


def test_the_template_actually_parses_and_has_alarms():
    """Guards the two tests below: a loader change that silently yielded nothing
    would make both of them vacuously pass."""
    found = list(_alarms())
    assert len(found) >= 10, f"expected the full alarm set, parsed only {len(found)}"


@pytest.mark.parametrize("logical_id,alarm_name,props", list(_alarms()), ids=lambda v: v if isinstance(v, str) else "")
def test_alarm_description_fits_aws_limit(logical_id, alarm_name, props):
    description = props.get("AlarmDescription")
    if description is None:
        return
    assert len(description) <= MAX_ALARM_DESCRIPTION, (
        f"{alarm_name} AlarmDescription is {len(description)} chars, over AWS's "
        f"{MAX_ALARM_DESCRIPTION}. validate-template will NOT catch this — it fails "
        f"at apply time as CREATE_FAILED and rolls the whole stack update back."
    )


@pytest.mark.parametrize("logical_id,alarm_name,props", list(_alarms()), ids=lambda v: v if isinstance(v, str) else "")
def test_multi_period_alarms_set_datapoints_to_alarm(logical_id, alarm_name, props):
    """EvaluationPeriods > 1 without DatapointsToAlarm is N-of-N — fires on N,
    clears on ONE. See this module's docstring for why that is the dangerous
    default rather than merely a noisy one."""
    evaluation_periods = props.get("EvaluationPeriods")
    if evaluation_periods is None or evaluation_periods <= 1:
        return  # 1-of-1: DatapointsToAlarm is meaningless, fire-immediately is deliberate.
    if alarm_name in DELIBERATE_N_OF_N:
        return
    assert props.get("DatapointsToAlarm") is not None, (
        f"{alarm_name} has EvaluationPeriods={evaluation_periods} and no DatapointsToAlarm, "
        f"so a single sub-threshold datapoint clears it while the condition continues. "
        f"Set DatapointsToAlarm (M-of-N), or add it to DELIBERATE_N_OF_N with the reason."
    )
