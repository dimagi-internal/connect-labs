"""A sampled child's story holds together the way the source's do (connect-labs RUTF clone 10113).

The clone of the real RUTF opportunity followed up children its own Screening had
NOT enrolled (81 of 147 followed children), and dated 193 visits after the source's
last day. Both came from the case model, not the source: a case's answers were drawn
with no regard to which forms the case is made of, and a case's start was moved by up
to a week with no regard to where the source's window ends. Pinned here on a source
whose structure is known (rutf_like_pool), on two seeds.
"""

import copy
import datetime as dt

import pytest

from connect_labs.labs.synthetic.generator.fixtures.case_model import synthesize_case_pool
from connect_labs.labs.synthetic.generator.fixtures.tests.rutf_like_pool import (
    CARRY_OVER_SHARE,
    COUNTER,
    END,
    ENROLLED,
    FOLLOW,
    FROZEN,
    OUTCOME,
    SCREEN,
    START,
    rutf_like_pool,
)


@pytest.fixture(params=[7, 21])
def pools(request):
    real = rutf_like_pool(request.param)
    synthetic, _ = synthesize_case_pool(copy.deepcopy(real), seed=request.param + 100, frozen_paths=FROZEN)
    return real, synthetic


def _followed(pool):
    return [s for s in pool if any(v.get("form") == FOLLOW for v in s["visits"])]


def _screened_not_enrolled(series):
    answers = [(v.get("cats") or {}).get(ENROLLED) for v in series["visits"] if v.get("form") == SCREEN]
    return bool(answers) and "yes" not in answers


def _starts_with_follow_up(series):
    return series["visits"][0].get("form") == FOLLOW


def test_a_child_is_followed_up_only_after_its_screening_enrolled_it(pools):
    """(a) No orphans beyond the source's own rate: a followed child's Screening said yes."""
    real, synthetic = pools
    assert not any(_screened_not_enrolled(s) for s in _followed(real))  # the source never does it
    assert not any(_screened_not_enrolled(s) for s in _followed(synthetic))


def test_children_already_in_treatment_when_the_window_opened_stay_at_the_sources_rate(pools):
    """(a) A followed child with no Screening at all is carry-over, kept at the source's share."""
    real, synthetic = pools
    real_share = sum(map(_starts_with_follow_up, real)) / len(real)
    synthetic_share = sum(map(_starts_with_follow_up, synthetic)) / len(synthetic)
    assert real_share == pytest.approx(CARRY_OVER_SHARE, abs=0.015)
    assert synthetic_share == pytest.approx(real_share, abs=0.015)


def test_a_course_runs_as_long_as_the_sources_do(pools):
    """(b) Courses are as long as the source's: visits per followed child and the counter's reach."""
    real, synthetic = pools

    def lengths(pool):
        return [len(s["visits"]) for s in _followed(pool)]

    def counters(pool):
        return max(v["values"].get(COUNTER, 0) for s in pool for v in s["visits"])

    assert len(lengths(synthetic)) == pytest.approx(len(lengths(real)), rel=0.05)
    assert sum(lengths(synthetic)) / len(lengths(synthetic)) == pytest.approx(
        sum(lengths(real)) / len(lengths(real)), rel=0.05
    )
    assert counters(synthetic) == counters(real)
    for series in synthetic:
        n = [v["values"][COUNTER] for v in series["visits"] if COUNTER in v["values"]]
        assert n == list(range(1, len(n) + 1)) or not n


def test_children_exit_at_the_sources_rate_and_only_at_their_last_visit(pools):
    """(c) Exits (recovered, defaulted) happen as often as in the source, and end the case."""
    real, synthetic = pools

    def exit_share(pool):
        followed = _followed(pool)
        ended = [s for s in followed if (s["visits"][-1].get("cats") or {}).get(OUTCOME) in ("recovered", "defaulted")]
        return len(ended) / len(followed)

    assert exit_share(synthetic) == pytest.approx(exit_share(real), abs=0.08)
    for series in synthetic:
        outcomes = [(v.get("cats") or {}).get(OUTCOME) for v in series["visits"]]
        assert not {"recovered", "defaulted"} & set(outcomes[:-1])  # nobody is seen after exiting


def test_a_pool_sampled_before_the_window_was_learned_replays_inside_it():
    """(d) An existing bundle's pool (10113's ran five weeks past its source) is made safe on replay."""
    from connect_labs.labs.synthetic.generator.fixtures.entities import plan_mirror_visits
    from connect_labs.labs.synthetic.generator.fixtures.manifest import LongitudinalSpec

    late = {"owner": "flw_001", "start_date": "2026-09-28", "visits": [{"day": d, "values": {}} for d in (0, 7, 14)]}
    long = {"owner": "flw_002", "start_date": "2026-09-01", "visits": [{"day": d, "values": {}} for d in (0, 20, 40)]}
    spec = LongitudinalSpec(mode="modelled", transplant_pool=[late, long])
    planned = plan_mirror_visits(spec, seed=1, window=(START, END))
    assert all(START <= pv.visit_date <= END for pv in planned)
    by_case = {}
    for pv in planned:
        by_case.setdefault(pv.beneficiary_idx, []).append(pv.visit_date)
    assert by_case[1] == [END - dt.timedelta(days=14), END - dt.timedelta(days=7), END]  # moved earlier, kept whole
    assert len(by_case[2]) == 3  # fits after moving


def test_no_visit_falls_outside_the_sources_window(pools):
    """(d) Nothing is dated after the source's last day (or before its first)."""
    _, synthetic = pools
    for series in synthetic:
        start = dt.date.fromisoformat(series["start_date"])
        for v in series["visits"]:
            assert START <= start + dt.timedelta(days=v["day"]) <= END
