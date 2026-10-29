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

import re
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


class _SubKeepingLoader(yaml.SafeLoader):
    """Like _CfnLoader, but keeps a short-form intrinsic's scalar (the !Sub string)
    so a composite's AlarmRule can be read for its children."""


_SubKeepingLoader.add_multi_constructor(
    "!", lambda loader, suffix, node: node.value if isinstance(node, yaml.ScalarNode) else None
)


def _composite_children():
    """Yield (composite_name, description, child_name, child_props) for each
    multi-period metric alarm a composite's AlarmRule references as ALARM("${LogicalId}")."""
    resources = yaml.load(TEMPLATE_PATH.read_text(), Loader=_SubKeepingLoader)["Resources"]
    for resource in resources.values():
        if resource.get("Type") != "AWS::CloudWatch::CompositeAlarm":
            continue
        props = resource["Properties"]
        for child_id in re.findall(r'ALARM\("\$\{(\w+)\}"\)', props.get("AlarmRule") or ""):
            child = resources.get(child_id, {}).get("Properties", {})
            # Multi-period children only: a 1-of-1 suppressor (deploy-in-progress)
            # has no "M of the last N" for the description to state.
            if child.get("Period") and (child.get("EvaluationPeriods") or 1) > 1:
                yield props["AlarmName"], props.get("AlarmDescription") or "", child["AlarmName"], child


def test_composite_descriptions_found_their_children():
    """Guards the test below against passing vacuously on a loader change."""
    assert any(name == "labs-jj-web-cpu-high-actionable" for name, *_ in _composite_children())


@pytest.mark.parametrize("composite,description,child,child_props", list(_composite_children()))
def test_composite_description_states_its_childs_live_rule(composite, description, child, child_props):
    """The COMPOSITE pages, so its description is the responder's only briefing — but
    the rule lives on the child. #1647 moved labs-jj-web-cpu-high to 4-of-5 @ 60s and
    the paging composite went on saying "3 of the last 5 five-minute periods ...
    Maximum over 300s" for four weeks (#2256). Any "M of the last N" or "over Ns"
    the description states must match the child it names."""
    m, n = child_props.get("DatapointsToAlarm"), child_props.get("EvaluationPeriods")
    for stated_m, stated_n in re.findall(r"(\d+) of the last (\d+)", description):
        assert (int(stated_m), int(stated_n)) == (
            m,
            n,
        ), f"{composite} says '{stated_m} of the last {stated_n}' but {child} is {m}-of-{n}"
    for stated_period in re.findall(r"over (\d+)s\b", description):
        assert (
            int(stated_period) == child_props["Period"]
        ), f"{composite} says 'over {stated_period}s' but {child} has Period {child_props['Period']}"


# ── Size, first steps, latency gate ───────────────────────────────────────────

INFRA = TEMPLATE_PATH.parent
RUNBOOK_PATH = INFRA.parent / "docs" / "PERFORMANCE_RUNBOOK.md"
ALARM_TEMPLATES = ["labs-monitoring.yml", "labs-tenant-monitoring.yml", "labs-synthetic-jobs.yml"]

# CloudFormation's limit on an inline --template-body, which infra/README.md's
# change-set path uses. Over it, the deploy fails with "Member must have length
# less than or equal to 51200", which reads like a malformed template.
MAX_INLINE_TEMPLATE_BYTES = 51_200


def test_template_fits_inline_limit():
    size = len(TEMPLATE_PATH.read_bytes())
    assert size <= MAX_INLINE_TEMPLATE_BYTES, (
        f"infra/labs-monitoring.yml is {size} bytes, over CloudFormation's {MAX_INLINE_TEMPLATE_BYTES}-byte "
        f"inline limit. Trim comments (history lives in the PRs), or move new alarms to their own "
        f"template as labs-tenant-monitoring.yml did. See infra/README.md § Deploy."
    )


def _paging_alarms():
    """Yield (template, alarm_name, description) for every alarm that notifies the topic."""
    for name in ALARM_TEMPLATES:
        doc = yaml.load((INFRA / name).read_text(), Loader=_CfnLoader)
        for resource in doc["Resources"].values():
            props = resource.get("Properties", {})
            if resource.get("Type") in ALARM_TYPES and "AlarmActions" in props:
                yield name, props["AlarmName"], props.get("AlarmDescription") or ""


def test_paging_alarms_found():
    """Guards the test below against passing vacuously."""
    assert len(list(_paging_alarms())) >= 15


@pytest.mark.parametrize("template,alarm_name,description", list(_paging_alarms()))
def test_paging_alarm_description_names_its_first_step(template, alarm_name, description):
    """A paging alarm's description is the responder's only briefing, read cold by an agent
    session. It must name the first read the responder can run without an SSO approval
    (`--profile labs-observe`) and the runbook section to continue in, and that section
    must exist, so the email starts the turn on the right query (#2404 did this once)."""
    assert "--profile labs-observe" in description, f"{alarm_name} ({template}) names no labs-observe first step"
    sections = re.findall(r"PERFORMANCE_RUNBOOK\.md §(\d+)", description)
    assert sections, f"{alarm_name} ({template}) names no docs/PERFORMANCE_RUNBOOK.md §N"
    headings = set(re.findall(r"^## (\d+)\.", RUNBOOK_PATH.read_text(), flags=re.M))
    missing = [s for s in sections if s not in headings]
    assert not missing, f"{alarm_name} points at runbook §{missing}, which has no '## N.' heading"


def test_latency_alarm_is_labs_scoped_and_gated_on_request_count():
    """#2768. The ALB-wide p95 counted canopy-web, whose ~14s night-time p95 flapped this
    alarm 10 times in 38 minutes while labs served 0-10 requests at ~0.1s; and on labs' own
    sparse traffic p95 is just the slowest request. So both inputs carry the labs target
    group, and the alarm judges p95 only when the period had LatencyMinRequests requests."""
    doc = yaml.load(TEMPLATE_PATH.read_text(), Loader=_SubKeepingLoader)
    props = next(
        r["Properties"]
        for r in doc["Resources"].values()
        if r.get("Properties", {}).get("AlarmName") == "labs-jj-alb-latency-high"
    )
    metrics = {m["Id"]: m for m in props["Metrics"]}
    for metric_id, name in (("p95", "TargetResponseTime"), ("n", "RequestCount")):
        stat = metrics[metric_id]["MetricStat"]
        assert stat["Metric"]["MetricName"] == name
        assert [d["Name"] for d in stat["Metric"]["Dimensions"]] == ["LoadBalancer", "TargetGroup"]
    assert metrics["gated"]["Expression"] == "IF(n >= ${LatencyMinRequests}, p95, 0)"
    assert [m["Id"] for m in props["Metrics"] if m.get("ReturnData")] == ["gated"]
    assert doc["Parameters"]["LatencyMinRequests"]["Default"] > 0
