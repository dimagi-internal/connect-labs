"""A multi-opp workflow must not validate one opportunity's cache against another's size."""

from types import SimpleNamespace

import pytest

from connect_labs.labs.analysis.config import DATA_SOURCE_TYPES, USER_VISITS_SOURCE
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


@pytest.mark.parametrize("source", [t for t in DATA_SOURCE_TYPES if t != USER_VISITS_SOURCE])
def test_a_source_whose_rows_are_not_visits_is_not_validated_against_the_visit_count(source):
    """A `work_areas` export holds 12,937 rows on an opportunity of 84,867 visits, so it
    could never count as warm, and every open of its report refilled it (2026-11-04)."""
    config = SimpleNamespace(data_source=SimpleNamespace(type=source), filters={})
    assert _pipeline()._expected_count_for_cache(config, 10042) == 0


def test_the_visits_export_is_validated_against_it():
    config = SimpleNamespace(data_source=SimpleNamespace(type=USER_VISITS_SOURCE), filters={})
    assert _pipeline()._expected_count_for_cache(config, 10042) == 2491


def test_a_filtered_visits_read_is_not():
    config = SimpleNamespace(data_source=SimpleNamespace(type=USER_VISITS_SOURCE), filters={"status": ["approved"]})
    assert _pipeline()._expected_count_for_cache(config, 10042) == 0
