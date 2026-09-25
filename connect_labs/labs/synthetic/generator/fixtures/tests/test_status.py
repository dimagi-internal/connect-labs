import json
import random

import pytest

from connect_labs.labs.synthetic.generator.fixtures.manifest import FlwPersona, MeanStddev
from connect_labs.labs.synthetic.generator.fixtures.profiler import _profile_flag_reasons
from connect_labs.labs.synthetic.generator.fixtures.status import decide_visit_status


def _p(flag_rate, archetype="steady"):
    return FlwPersona(
        id="x",
        archetype=archetype,
        accuracy_distribution=MeanStddev(mean=0.9, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.95, stddev=0.03),
        flag_rate=flag_rate,
    )


def test_zero_flag_rate_never_flags():
    rng = random.Random(0)
    persona = _p(0.0)
    for _ in range(200):
        s = decide_visit_status(persona=persona, has_anomaly=False, rng=rng)
        assert s.flagged is False
        assert s.status == "approved"


def test_high_flag_rate_eventually_flags():
    rng = random.Random(0)
    persona = _p(1.0)
    s = decide_visit_status(persona=persona, has_anomaly=False, rng=rng)
    assert s.flagged is True
    assert s.flag_reason  # non-empty string
    assert s.status in {"pending", "rejected"}


def test_anomaly_forces_flag_and_review():
    rng = random.Random(0)
    persona = _p(0.0)  # would never flag without anomaly
    s = decide_visit_status(persona=persona, has_anomaly=True, rng=rng)
    assert s.flagged is True
    assert s.review_status in {"pending", "rejected"}


def test_flag_reason_sampled_from_distribution():
    rng = random.Random(0)
    persona = FlwPersona(
        id="x",
        archetype="struggling",
        accuracy_distribution=MeanStddev(mean=0.6, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.6, stddev=0.05),
        flag_rate=1.0,
    )
    seen = set()
    for _ in range(50):
        s = decide_visit_status(
            persona=persona, has_anomaly=False, rng=rng, flag_reason_distribution={"only-reason": 1.0}
        )
        if s.flag_reason:
            seen.add(s.flag_reason)
    assert seen == {"only-reason"}


# --------------------------------------------------------------------------------------
# over_limit — legitimate paid work a budget-cap accounting glitch mislabels.
#
# Connect's metrics spec defines valid data as `status IN ('approved','over_limit')` and
# notes that excluding over_limit undercounts visits, started cases and weight series by
# 40-130% depending on the programme. Before this, `Status` was
# Literal["approved","pending","rejected"] — so no generated visit could carry it and a
# synthetic dataset could not express the rule at all.
# --------------------------------------------------------------------------------------


def test_zero_rate_produces_no_over_limit_at_all():
    """The default, and a legitimate steady state: many opportunities never hit their
    budget cap, and a clone of one must contain no over_limit visits."""
    rng = random.Random(7)
    out = [decide_visit_status(persona=_p(0.0), has_anomaly=False, rng=rng) for _ in range(400)]
    assert {s.status for s in out} == {"approved"}


def test_a_rate_relabels_roughly_that_share_of_approved_visits():
    rng = random.Random(7)
    out = [decide_visit_status(persona=_p(0.0), has_anomaly=False, rng=rng, over_limit_rate=0.08) for _ in range(4000)]
    share = sum(1 for s in out if s.status == "over_limit") / len(out)
    assert 0.06 < share < 0.10
    assert {s.status for s in out} == {"approved", "over_limit"}


def test_over_limit_is_not_a_quality_signal():
    """It must not read as a flag or a review outcome. The work reviewed clean; only the
    payment ledger disagreed — so a rising over_limit rate must not shift a worker's
    archetype or show up as a flagged visit needing review."""
    rng = random.Random(3)
    over = [
        s
        for s in (
            decide_visit_status(persona=_p(0.0), has_anomaly=False, rng=rng, over_limit_rate=0.5) for _ in range(500)
        )
        if s.status == "over_limit"
    ]
    assert over, "expected some over_limit draws at rate 0.5"
    assert all(s.flagged is False for s in over)
    assert all(s.flag_reason == "" for s in over)
    assert all(s.review_status == "approved" for s in over)


def test_over_limit_never_displaces_a_flagged_or_anomalous_visit():
    """Applied to the APPROVED branch only — a genuinely flagged visit stays flagged even
    at a high rate, or the glitch would start eating real quality signal."""
    rng = random.Random(11)
    anomalous = decide_visit_status(persona=_p(0.0), has_anomaly=True, rng=rng, over_limit_rate=0.5)
    assert anomalous.status == "pending" and anomalous.flagged

    flagged = [
        decide_visit_status(persona=_p(1.0), has_anomaly=False, rng=rng, over_limit_rate=0.5) for _ in range(50)
    ]
    assert all(s.status in {"pending", "rejected"} for s in flagged)


# --------------------------------------------------------------------------------------
# flag_reason TYPE (#task-7 C2)
#
# Prod stores flag_reason as a JSONField holding {"flags": [[code, message], ...]}, and
# every labs consumer decodes it as JSON. A clone that carries a *rendering* of that
# structure instead of the structure breaks all of them at once, and does it on the one
# axis a quality demo exists to show. These pin the type, not just the content.


def _flagging_persona():
    return FlwPersona(
        id="x",
        archetype="struggling",
        accuracy_distribution=MeanStddev(mean=0.6, stddev=0.05),
        completeness_distribution=MeanStddev(mean=0.6, stddev=0.05),
        flag_rate=1.0,
    )


def _first_reason(distribution):
    rng = random.Random(0)
    for _ in range(50):
        s = decide_visit_status(
            persona=_flagging_persona(),
            has_anomaly=False,
            rng=rng,
            flag_reason_distribution=distribution,
        )
        if s.flag_reason:
            return s.flag_reason
    raise AssertionError("never flagged")


def test_flag_reason_from_json_key_is_a_dict_not_a_string():
    """A distribution keyed by canonical JSON yields prod's STRUCTURE."""
    key = json.dumps({"flags": [["pending_task", "Worker has an incomplete assigned task."]]})
    reason = _first_reason({key: 1.0})
    assert isinstance(reason, dict), f"expected dict, got {type(reason).__name__}: {reason!r}"
    # The thing every consumer does, and the thing that used to raise AttributeError.
    assert [f for f, _ in reason.get("flags", [])] == ["pending_task"]
    # And it survives the labs ingest path, which re-encodes then decodes it.
    assert json.loads(json.dumps(reason)) == reason


def test_flag_reason_from_legacy_repr_key_is_recovered():
    """Bundles profiled before the fix key by repr(dict); regenerate, don't re-profile."""
    key = "{'flags': [['pending_task', 'Worker has an incomplete assigned task.']]}"
    with pytest.raises(ValueError):
        json.loads(key)  # the defect, pinned: this key is not JSON
    reason = _first_reason({key: 1.0})
    assert isinstance(reason, dict)
    assert reason["flags"] == [["pending_task", "Worker has an incomplete assigned task."]]


@pytest.mark.parametrize("key", ["only-reason", "GPS outside service area", "123", "duration"])
def test_plain_string_reasons_are_left_alone(key):
    """A reason that is genuinely a human string stays that string, uncoerced.

    "123" is the one that matters: json.loads would turn it into an int.
    """
    assert _first_reason({key: 1.0}) == key


def test_profiler_keys_the_distribution_by_json_not_repr():
    """The root cause, at its source."""
    reason = {"flags": [["pending_task", "Worker has an incomplete assigned task."]]}
    dist = _profile_flag_reasons([{"flagged": True, "flag_reason": reason}])
    (key,) = dist
    assert json.loads(key) == reason, f"key is not JSON-decodable: {key!r}"
    assert "'" not in key, f"key is a Python repr, not JSON: {key!r}"


def test_profiler_does_not_split_one_reason_across_key_orderings():
    """Unordered dicts must collapse to one key, or a reason's rate is halved."""
    a = {"flags": [["pending_task", "m"]], "extra": 1}
    b = {"extra": 1, "flags": [["pending_task", "m"]]}
    dist = _profile_flag_reasons([{"flagged": True, "flag_reason": a}, {"flagged": True, "flag_reason": b}])
    assert len(dist) == 1
    assert next(iter(dist.values())) == 1.0
