"""Incremental top-up of RawVisitCache instead of repaginating the whole export (#1361).

Cache validity is ``count >= expected AND not expired``, so a miss has two causes and
they want different answers:

- **expired** — the periodic refresh, and the only thing that re-reads EXISTING
  visits. It must stay a full rebuild: a visit's ``status`` changes when it is
  reviewed and that never moves the count, so nothing else would pick it up.
- **count short, still unexpired** — new visits arrived, which are strictly new rows
  with higher ids.

Only the second is topped up. That is why this costs nothing in freshness, and it is
the property most worth protecting here — several tests below exist purely to pin it.

Everything the delta is unsure about must degrade to a full rebuild, so most of these
assert ``False`` (meaning "caller, do it the old way") rather than an exception.
"""

from datetime import timedelta
from unittest.mock import MagicMock, patch

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import RAW_CACHE_DELTA_MAX_ROWS, SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

pytestmark = pytest.mark.django_db

OPP = 2155
PIPELINE = 42


def _seed(count, *, first_id=1000, expires_in_hours=6, visit_count=None):
    """Write `count` visible cached rows with sequential numeric visit ids."""
    expires_at = timezone.now() + timedelta(hours=expires_in_hours)
    stamped = visit_count if visit_count is not None else count
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=OPP,
                pipeline_id=PIPELINE,
                visit_count=stamped,
                expires_at=expires_at,
                visit_id=str(first_id + i),
                username=f"flw{i % 3}",
                visit_date=timezone.now().date(),
            )
            for i in range(count)
        ]
    )
    return expires_at


def _manager():
    return SQLCacheManager(OPP, pipeline_id=PIPELINE)


def _backend():
    return SQLBackend()


def _client_yielding(pages):
    """A get_export_client stand-in whose paginate() yields the given pages."""
    client = MagicMock()
    client.paginate.return_value = iter(pages)
    client.__enter__ = lambda s: client
    client.__exit__ = lambda s, *a: False
    return client


def _record(visit_id):
    return {"id": visit_id, "username": "flw9", "visit_date": "2026-09-07", "status": "pending"}


# --- The anchor -------------------------------------------------------------------


def test_anchor_reports_the_numeric_high_water_mark():
    """Not a SQL Max: visit_id is a CharField, so the database would order it
    lexicographically and rank "9" above "10", handing back a mark that silently
    skips every id in between."""
    _seed(12, first_id=5)  # ids 5..16 — lexicographic max is "9", numeric is 16
    count, max_id, _ = _manager().get_raw_delta_anchor()
    assert count == 12
    assert max_id == 16


def test_anchor_declines_on_a_non_numeric_id():
    """ONE unparseable id is enough. A numeric max over a set we cannot fully parse
    is a mark that may sit below rows we already hold, and the delta would then
    re-fetch them into a duplicate-key violation."""
    _seed(3, first_id=100)
    stray = RawVisitCache.objects.filter(opportunity_id=OPP).first()
    stray.visit_id = "abc"
    stray.save(update_fields=["visit_id"])
    assert _manager().get_raw_delta_anchor() is None


def test_anchor_declines_when_a_writer_is_mid_finalize():
    """Mixed visit_count means the slot is being rewritten; a top-up could not
    re-stamp it coherently."""
    _seed(4, first_id=200)
    row = RawVisitCache.objects.filter(opportunity_id=OPP).first()
    row.visit_count = 999
    row.save(update_fields=["visit_count"])
    assert _manager().get_raw_delta_anchor() is None


def test_anchor_is_none_on_an_empty_slot():
    assert _manager().get_raw_delta_anchor() is None


# --- The freshness contract, which is the point of the whole design ----------------


def test_the_appended_rows_inherit_the_existing_expiry():
    """The TTL floor is the ONLY thing that re-reads existing visits, so a top-up
    must never push it out. has_valid_raw_cache is an .exists(), so a single row
    with a later expiry would make a busy opportunity look fresh forever and its
    reviewed visits would never refresh."""
    expires_at = _seed(5, first_id=300)
    mgr = _manager()
    mgr.store_raw_visits_append_start(expires_at)
    mgr.store_raw_visits_batch([_record(400), _record(401)])
    mgr.store_raw_visits_append_finalize(5, 7)

    expiries = set(RawVisitCache.objects.filter(opportunity_id=OPP).values_list("expires_at", flat=True))
    assert expiries == {expires_at}, "a top-up extended the cache's life"


def test_an_expired_cache_is_not_topped_up():
    """Expiry is the status-refresh path and must stay a full rebuild."""
    _seed(10, first_id=500, expires_in_hours=-1)
    assert (
        _backend()._try_delta_refresh(_manager(), OPP, "tok", expected_visit_count=12, pipeline_id=PIPELINE, user=None)
        is False
    )


def test_finalize_leaves_one_coherent_count():
    _seed(5, first_id=600)
    mgr = _manager()
    mgr.store_raw_visits_append_start(timezone.now() + timedelta(hours=6))
    mgr.store_raw_visits_batch([_record(700)])
    mgr.store_raw_visits_append_finalize(5, 6)

    counts = set(RawVisitCache.objects.filter(opportunity_id=OPP).values_list("visit_count", flat=True))
    assert counts == {6}
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 6


def test_finalize_abandons_the_append_if_the_base_moved():
    """Another writer finalized underneath us. Its result is at least as fresh, so
    the half-written top-up is dropped rather than merged into it."""
    _seed(5, first_id=800)
    mgr = _manager()
    mgr.store_raw_visits_append_start(timezone.now() + timedelta(hours=6))
    mgr.store_raw_visits_batch([_record(900)])
    RawVisitCache.objects.filter(opportunity_id=OPP, visit_count=5).update(visit_count=77)

    assert mgr.store_raw_visits_append_finalize(5, 6) == 0
    assert not RawVisitCache.objects.filter(opportunity_id=OPP, visit_count__lt=0).exists()
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 5


# --- The fetch guards, all of which mean "caller, do a full rebuild" ---------------


def _run_delta(expected, pages):
    with patch(
        "connect_labs.labs.integrations.connect.factory.get_export_client",
        return_value=_client_yielding(pages),
    ):
        return _backend()._try_delta_refresh(
            _manager(), OPP, "tok", expected_visit_count=expected, pipeline_id=PIPELINE, user=None
        )


def test_a_clean_delta_tops_up_and_reports_success():
    _seed(10, first_id=1000)  # ids 1000..1009
    assert _run_delta(13, [[_record(1010), _record(1011), _record(1012)]]) is True
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 13
    assert set(RawVisitCache.objects.filter(opportunity_id=OPP).values_list("visit_count", flat=True)) == {13}


def test_a_cursor_returning_an_id_at_or_below_the_anchor_falls_back():
    """The cursor assumption is the one thing that would corrupt the cache — an id
    already held would be appended a second time and the count would be wrong."""
    _seed(10, first_id=1000)
    assert _run_delta(11, [[_record(1005)]]) is False
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 10


def test_a_gap_larger_than_the_ceiling_falls_back():
    _seed(10, first_id=1000)
    assert _run_delta(10 + RAW_CACHE_DELTA_MAX_ROWS + 1, [[]]) is False


def test_no_expected_count_falls_back():
    _seed(10, first_id=1000)
    assert (
        _backend()._try_delta_refresh(
            _manager(), OPP, "tok", expected_visit_count=None, pipeline_id=PIPELINE, user=None
        )
        is False
    )


def test_a_cache_that_is_not_actually_short_falls_back():
    _seed(10, first_id=1000)
    assert _run_delta(10, [[]]) is False


def test_an_export_failure_falls_back_rather_than_raising():
    from connect_labs.labs.integrations.connect.export_client import ExportAPIError

    _seed(10, first_id=1000)
    client = MagicMock()
    client.paginate.side_effect = ExportAPIError("upstream is unhappy")
    client.__enter__ = lambda s: client
    client.__exit__ = lambda s, *a: False
    with patch("connect_labs.labs.integrations.connect.factory.get_export_client", return_value=client):
        assert (
            _backend()._try_delta_refresh(
                _manager(), OPP, "tok", expected_visit_count=12, pipeline_id=PIPELINE, user=None
            )
            is False
        )
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 10


def test_a_duplicate_id_across_pages_does_not_abort_the_append():
    """UNIQUE(opportunity_id, pipeline_id, visit_count, visit_id) means one repeated
    id would fail the whole insert. Overlap with rows already held is impossible by
    the anchor guard; overlap within the delta is not."""
    _seed(10, first_id=1000)
    assert _run_delta(12, [[_record(1010)], [_record(1010), _record(1011)]]) is True
    assert RawVisitCache.objects.filter(opportunity_id=OPP).count() == 12
