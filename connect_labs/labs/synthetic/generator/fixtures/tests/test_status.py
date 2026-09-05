import random

from connect_labs.labs.synthetic.generator.fixtures.manifest import FlwPersona, MeanStddev
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
