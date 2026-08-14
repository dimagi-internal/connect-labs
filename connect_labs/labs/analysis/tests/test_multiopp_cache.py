"""A multi-opp workflow must not validate one opportunity's cache against another's size."""

from connect_labs.labs.analysis.pipeline import AnalysisPipeline


class _Req:
    def __init__(self, ctx):
        self.labs_context = ctx
        self.user = None
        self.session = {"labs_oauth": {"access_token": "tok"}}


def _pipeline(primary_id=10042, primary_visits=2491):
    return AnalysisPipeline(_Req({"opportunity_id": primary_id, "opportunity": {"visit_count": primary_visits}}))


def test_expected_visits_only_applies_to_the_primary_opportunity():
    """`visit_count` describes the REQUEST's opportunity. Applying it to any other
    opportunity in a multi-opp set is what made the cache unusable: it is compared as
    `cached_visit_count >= expected`, so every opp SMALLER than the primary failed
    forever and re-downloaded its whole dataset on every page view.

    Measured on the 11-opp KMC workflow (primary = 2,491 visits): the 4 opps at or
    above that count hit cache 8/8, the 7 below it missed 14/14 — and the same value
    produced the "Fetching visits: 11,604 / 2,491 rows (465%)" progress line.
    """
    p = _pipeline()

    # The primary keeps its real expected count — that check is meaningful.
    assert p.expected_visits_for(10042) == 2491

    # A smaller sibling must NOT inherit it (this is the opp that never hit cache).
    assert p.expected_visits_for(10020) == 0, "a foreign opp must fall back to the TTL, not the primary's count"

    # A larger sibling must not inherit it either — it would spuriously PASS, which is
    # how the bug hid: big opps looked fine while small ones re-downloaded forever.
    assert p.expected_visits_for(10019) == 0


def test_unknown_primary_is_not_treated_as_a_match():
    """With no opportunity in context, nothing should claim a known expected count."""
    p = AnalysisPipeline(_Req({}))
    assert p.expected_visits_for(10042) == 0
