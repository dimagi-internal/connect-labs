"""An empty visit filter must return NO visits, not EVERY visit.

`filter_visit_ids` was tested for truthiness, so `set()` — "none of these
visits" — collapsed into `None`, which means "no filter at all". A caller asking
for zero visits was handed the entire opportunity.

It fails as a slow success, never as an error, which is why it survived: the two
callers that re-filter in Python afterwards (`get_visit_data`, `get_visits_batch`)
still return the correct answer, having materialised every row in the opportunity
to do it. Measured 2026-08-26 on one 5h15m audit job: 264 of these loads, ~23,272
rows and 8-10 seconds each — roughly 4.9 million rows and ~37 minutes spent
answering "give me nothing".

These tests assert the ROW COUNT the query returns, because that is the property
that broke. A test asserting only "the right visits come back" passes on the bug.
"""

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

OPP = 990001


def _seed(n: int) -> None:
    future = timezone.now() + timezone.timedelta(days=1)
    for i in range(n):
        RawVisitCache.objects.create(
            opportunity_id=OPP,
            visit_count=n,
            expires_at=future,
            visit_id=str(70000 + i),
            username=f"flw{i}",
            form_json={"form": {"x": i}},
            visit_date="2024-01-15",
            status="approved",
        )


@pytest.mark.django_db
class TestEmptyVisitFilter:
    def test_empty_set_returns_nothing_not_everything(self):
        """The bug, stated directly: set() must not mean 'all of them'."""
        _seed(25)
        rows = SQLBackend()._load_from_cache(
            SQLCacheManager(OPP, config=None), skip_form_json=True, filter_visit_ids=set()
        )
        assert len(rows) == 0, (
            f"asked for zero visits and got {len(rows)} — an empty filter is being "
            "treated as no filter, so the whole opportunity is materialised"
        )

    def test_none_still_means_no_filter(self):
        """`None` keeps its meaning — this is the distinction the fix restores."""
        _seed(25)
        rows = SQLBackend()._load_from_cache(
            SQLCacheManager(OPP, config=None), skip_form_json=True, filter_visit_ids=None
        )
        assert len(rows) == 25, "None must still mean 'every visit'"

    def test_a_populated_filter_is_unaffected(self):
        """The ordinary case keeps working — guards against over-correcting."""
        _seed(25)
        rows = SQLBackend()._load_from_cache(
            SQLCacheManager(OPP, config=None),
            skip_form_json=True,
            filter_visit_ids={"70003", "70007"},
        )
        assert len(rows) == 2
        assert {str(r["id"]) for r in rows} == {"70003", "70007"}
