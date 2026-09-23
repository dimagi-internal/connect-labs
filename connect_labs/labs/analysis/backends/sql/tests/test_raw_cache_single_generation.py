"""A raw-visit slot holding TWO finalized generations must still read as one.

Found in production on 2026-09-23: one opportunity's shared user_visits slot held
118,309 rows for ~59.7k real visits -- every visit twice, under two different
``visit_count`` labels. The unique key is (opportunity, slot, visit_count,
visit_id), so two generations with different labels do not collide. Two things
then broke, and kept each other broken:

1. Readers got every visit twice, so the visit-level computed write collided
   with itself and every load failed with "Concurrent write to
   ComputedVisitCache ... Another pipeline run is in flight" -- with no other
   run in flight.
2. The shrink guard's baseline counted ROWS (118,309), so a correct fresh
   download (59,759) read as a 50% shrink. It was discarded three times, the
   doubled rows were kept and their TTL extended. Every refresh, scheduled or
   manual, re-armed the problem instead of clearing it.

A healthy slot always holds exactly one generation (finalize and top-up both
leave a single label), so serving one generation changes nothing there.
"""

from collections import Counter
from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import RAW_CACHE_MAX_ATTEMPTS, SQLBackend
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

OPP_ID = 42
PIPELINE_ID = 1001
NEWER = 12  # the later walk; a live opportunity keeps gaining visits
OLDER = 10  # an earlier walk of the same visits that ended a little lower


@pytest.fixture(autouse=True)
def _connect_production_url(settings):
    settings.CONNECT_PRODUCTION_URL = "https://connect.example.com"


def _seed_doubled_slot(expired: bool = False) -> SQLCacheManager:
    """Two finalized generations of the SAME visit ids, as found in production."""
    manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
    expires_at = timezone.now() + (timedelta(hours=-1) if expired else timedelta(minutes=30))

    def generation(label):
        return [
            RawVisitCache(
                opportunity_id=OPP_ID,
                pipeline_id=manager.raw_slot_id,
                visit_count=label,
                expires_at=expires_at,
                visit_id=i,
                username=f"user{i}",
            )
            for i in range(label)
        ]

    RawVisitCache.objects.bulk_create(generation(OLDER) + generation(NEWER))
    # Fetched hours ago, as in production -- otherwise a forced refresh would
    # reuse the slot as "just fetched" (FORCED_REFRESH_REUSE_MINUTES) and never walk.
    RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=manager.raw_slot_id).update(
        created_at=timezone.now() - timedelta(hours=2)
    )
    return manager


def _export(n: int):
    return {
        "url": f"https://connect.example.com/export/opportunity/{OPP_ID}/user_visits/?page_size=2500",
        "json": {"next": None, "results": [{"id": i} for i in range(n)]},
    }


def _slot_rows():
    slot = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID).raw_slot_id
    return RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=slot)


def _labels():
    return set(_slot_rows().values_list("visit_count", flat=True))


@pytest.mark.django_db
class TestDoubledSlotReadsAsOneGeneration:
    def test_counts_one_generation(self):
        manager = _seed_doubled_slot()
        assert manager.get_raw_visit_count() == NEWER
        assert manager.get_raw_visit_count_ignoring_ttl() == NEWER

    def test_readers_see_each_visit_once(self):
        manager = _seed_doubled_slot()
        ids = Counter(row.visit_id for row in manager.get_raw_visits_queryset())
        assert len(ids) == NEWER
        assert set(ids.values()) == {1}

    def test_serves_the_newer_generation(self):
        manager = _seed_doubled_slot()
        assert set(manager.get_raw_visits_queryset().values_list("visit_count", flat=True)) == {NEWER}

    def test_ignoring_ttl_still_sees_an_expired_doubled_slot_as_one(self):
        manager = _seed_doubled_slot(expired=True)
        assert manager.get_raw_visit_count() == 0
        assert manager.get_raw_visit_count_ignoring_ttl() == NEWER

    def test_computed_write_from_the_readers_succeeds(self):
        manager = _seed_doubled_slot()
        manager.config_hash = "abc123def456"
        visits = [
            {"visit_id": row.visit_id, "username": row.username, "computed_fields": {}}
            for row in manager.get_raw_visits_queryset()
        ]
        manager.store_computed_visits(visits, visit_count=NEWER)  # raised CacheConcurrencyError before
        assert manager.get_computed_visits_queryset().count() == NEWER


@pytest.mark.django_db
class TestShrinkGuardOnADoubledSlot:
    def test_a_correct_refresh_replaces_the_doubled_slot(self, httpx_mock):
        _seed_doubled_slot()
        httpx_mock.add_response(**_export(NEWER))
        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID,
                access_token="t",
                expected_visit_count=NEWER,
                pipeline_id=PIPELINE_ID,
                force_refresh=True,
            )
        )
        assert events[-1] == ("complete", NEWER)
        assert backend.last_raw_fetch_anomaly is None
        assert _labels() == {NEWER}
        assert _slot_rows().count() == NEWER

    def test_the_ordinary_expiry_rebuild_also_clears_it(self, httpx_mock):
        _seed_doubled_slot(expired=True)
        httpx_mock.add_response(**_export(NEWER))
        events = list(
            SQLBackend().stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=NEWER, pipeline_id=PIPELINE_ID
            )
        )
        assert events[-1] == ("complete", NEWER)
        assert _labels() == {NEWER}

    def test_a_genuine_shrink_is_still_refused(self, httpx_mock):
        """The guard keeps its job: a short read against a doubled slot is still
        judged against one generation (12), and 2 rows is still too few."""
        manager = _seed_doubled_slot()
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_export(2))
        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID,
                access_token="t",
                expected_visit_count=NEWER,
                pipeline_id=PIPELINE_ID,
                force_refresh=True,
            )
        )
        assert events[-1] == ("cached", NEWER)
        assert backend.last_raw_fetch_anomaly["previous_count"] == NEWER
        assert manager.get_raw_visit_count() == NEWER
