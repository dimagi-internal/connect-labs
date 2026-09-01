"""Tests for the raw-visit-cache shrink guard.

Background: a transient short-read from Connect's export endpoint once
returned far fewer rows than expected, and the cache layer blindly
overwrote a good ~33k-row cache with a ~1.5k-row one for a full TTL
period, corrupting every metric derived from it (see backend.py module
docstring / RAW_CACHE_SHRINK_THRESHOLD_PCT).

The realistic trigger for the guard is NOT force_refresh -- it's the
*normal* TTL-expiry cache-miss path. These tests always seed a prior
cache and expire it via expires_at, rather than relying on force_refresh,
because that's the scenario `get_raw_visit_count_ignoring_ttl()` exists
to protect: the ordinary get_raw_visit_count() would read 0 for an
expired cache and silently defeat the whole guard.
"""

from datetime import timedelta

import pytest
from django.test import override_settings
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.backend import (
    RAW_CACHE_MAX_ATTEMPTS,
    RAW_CACHE_SHRINK_THRESHOLD_PCT,
    SQLBackend,
)
from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

OPP_ID = 42
PIPELINE_ID = 1001


def _seed_expired_cache(count: int, pipeline_id: int = PIPELINE_ID) -> SQLCacheManager:
    """Store `count` valid rows, then force them into the past so the cache
    reads as a "miss" (mirrors natural TTL expiry, the common real trigger)."""
    manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=pipeline_id)
    manager.store_raw_visits(
        visit_dicts=[{"id": i, "username": f"user{i}"} for i in range(count)],
        visit_count=count,
    )
    RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=pipeline_id).update(
        expires_at=timezone.now() - timedelta(hours=1)
    )
    return manager


def _single_page_response(n: int, url_suffix: str = "?page_size=2500"):
    return {
        "url": f"https://connect.example.com/export/opportunity/{OPP_ID}/user_visits/{url_suffix}",
        "json": {"next": None, "results": [{"id": i} for i in range(n)]},
    }


@pytest.mark.django_db
class TestGetRawVisitCountIgnoringTtl:
    def test_counts_expired_rows(self):
        """The whole point of this method: unlike get_raw_visit_count, it must
        still see rows whose TTL has already lapsed."""
        manager = _seed_expired_cache(10)
        assert manager.get_raw_visit_count() == 0  # sanity: TTL-filtered count sees nothing
        assert manager.get_raw_visit_count_ignoring_ttl() == 10

    def test_excludes_sentinel_rows(self):
        manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        manager.store_raw_visits_start(visit_count=5)
        manager.store_raw_visits_batch([{"id": 1, "username": "alice"}])
        # Not finalized -- still a negative-sentinel row, should not count.
        assert manager.get_raw_visit_count_ignoring_ttl() == 0

    def test_scoped_to_opportunity_and_pipeline(self):
        _seed_expired_cache(10, pipeline_id=PIPELINE_ID)
        other = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=9999)
        assert other.get_raw_visit_count_ignoring_ttl() == 0
        other_opp = SQLCacheManager(opportunity_id=43, pipeline_id=PIPELINE_ID)
        assert other_opp.get_raw_visit_count_ignoring_ttl() == 0


@pytest.mark.django_db
class TestPendingRawFetchAnomaly:
    def test_round_trips(self):
        manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        assert manager.get_pending_raw_fetch_anomaly() is None
        anomaly = {"previous_count": 10, "attempted_count": 2, "threshold_pct": 80}
        manager.set_pending_raw_fetch_anomaly(anomaly, minutes=10)
        assert manager.get_pending_raw_fetch_anomaly() == anomaly
        manager.clear_pending_raw_fetch_anomaly()
        assert manager.get_pending_raw_fetch_anomaly() is None

    def test_scoped_to_opportunity_and_pipeline(self):
        manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        manager.set_pending_raw_fetch_anomaly({"previous_count": 10, "attempted_count": 2, "threshold_pct": 80}, 10)
        try:
            other = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=9999)
            assert other.get_pending_raw_fetch_anomaly() is None
        finally:
            manager.clear_pending_raw_fetch_anomaly()


@pytest.mark.django_db
class TestExtendRawCacheTtl:
    def test_makes_expired_rows_readable_again(self):
        manager = _seed_expired_cache(10)
        assert manager.get_raw_visits_queryset().count() == 0  # expired -- invisible to readers
        manager.extend_raw_cache_ttl(minutes=10)
        assert manager.get_raw_visits_queryset().count() == 10

    def test_never_shortens_a_longer_lived_entry(self):
        manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        manager.store_raw_visits(visit_dicts=[{"id": 1, "username": "alice"}], visit_count=1)
        far_future = timezone.now() + timedelta(days=1)
        RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID).update(expires_at=far_future)
        manager.extend_raw_cache_ttl(minutes=10)
        row = RawVisitCache.objects.get(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        assert row.expires_at == far_future

    def test_does_not_touch_sentinel_rows(self):
        manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        manager.store_raw_visits_start(visit_count=5)
        manager.store_raw_visits_batch([{"id": 1, "username": "alice"}])
        manager.extend_raw_cache_ttl(minutes=10)
        # Sentinel row (negative visit_count) must remain invisible to readers.
        assert manager.get_raw_visits_queryset().count() == 0


@pytest.mark.django_db
@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
class TestFetchRawVisitsShrinkGuard:
    def test_first_ever_fetch_skips_guard_even_if_small(self, httpx_mock):
        """prior_count == 0 (nothing cached yet) must never be treated as a shrink."""
        httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        visits = backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert len(visits) == 2
        assert backend.last_raw_fetch_anomaly is None
        assert RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID).count() == 2

    def test_fetch_at_or_above_threshold_is_accepted_without_retry(self, httpx_mock):
        _seed_expired_cache(10)
        threshold = 10 * RAW_CACHE_SHRINK_THRESHOLD_PCT / 100
        # Exactly at threshold, single response registered -- a second HTTP
        # call would fail matching and prove no retry happened.
        httpx_mock.add_response(**_single_page_response(int(threshold)))
        backend = SQLBackend()
        visits = backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert len(visits) == int(threshold)
        assert backend.last_raw_fetch_anomaly is None
        assert RawVisitCache.objects.filter(
            opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=int(threshold)
        ).count() == int(threshold)

    def test_retries_and_succeeds_on_a_later_attempt(self, httpx_mock):
        _seed_expired_cache(10)
        httpx_mock.add_response(**_single_page_response(2))  # attempt 1: low
        httpx_mock.add_response(**_single_page_response(9))  # attempt 2: passes (>= 8)
        backend = SQLBackend()
        visits = backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert len(visits) == 9
        assert backend.last_raw_fetch_anomaly is None
        # New count replaced the old cache entirely.
        assert RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=9).count() == 9
        assert (
            RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=10).count() == 0
        )

    def test_falls_back_to_old_cache_after_exhausting_retries(self, httpx_mock):
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))  # stays low every attempt

        backend = SQLBackend()
        visits = backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)

        # Old (good) data served, not the bad short-read.
        assert len(visits) == 10
        assert backend.last_raw_fetch_anomaly == {
            "previous_count": 10,
            "attempted_count": 2,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }
        # Old cache rows were never overwritten by the bad fetch.
        assert (
            RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=10).count() == 10
        )
        # TTL was pushed out -- rows are readable again despite having
        # originally been expired to trigger this "miss" in the first place.
        manager = SQLCacheManager(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID)
        assert manager.get_raw_visits_queryset().count() == 10

    def test_accept_low_count_bypasses_the_guard(self, httpx_mock):
        _seed_expired_cache(10)
        httpx_mock.add_response(**_single_page_response(2))  # only one attempt should fire
        backend = SQLBackend()
        visits = backend.fetch_raw_visits(
            opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID, accept_low_count=True
        )
        assert len(visits) == 2
        assert backend.last_raw_fetch_anomaly is None
        assert RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=2).count() == 2

    def test_anomaly_persists_on_a_later_cache_hit(self, httpx_mock):
        """The guard's extend_raw_cache_ttl() makes the old rows look like an
        ordinary valid cache again -- without get_pending_raw_fetch_anomaly,
        this second call would silently see a plain cache HIT and drop the
        flag, so the banner would only ever have appeared on the one request
        that happened to exhaust the retries."""
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert backend.last_raw_fetch_anomaly is not None

        # Simulate a page reload: fresh backend instance, cache is now valid
        # again (TTL was extended), no HTTP mock registered -- a plain
        # cache-HIT must not require another fetch.
        reload_backend = SQLBackend()
        visits = reload_backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert len(visits) == 10
        assert reload_backend.last_raw_fetch_anomaly == {
            "previous_count": 10,
            "attempted_count": 2,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }

    def test_anomaly_clears_once_a_refresh_succeeds(self, httpx_mock):
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert backend.last_raw_fetch_anomaly is not None

        # A later force_refresh succeeds with a good count -- anomaly clears...
        httpx_mock.add_response(**_single_page_response(9))
        refreshed = SQLBackend()
        refreshed.fetch_raw_visits(
            opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID, force_refresh=True
        )
        assert refreshed.last_raw_fetch_anomaly is None

        # ...and stays cleared on a subsequent plain cache-HIT read.
        reload_backend = SQLBackend()
        reload_backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)
        assert reload_backend.last_raw_fetch_anomaly is None

    def test_falls_back_to_low_fetch_when_old_cache_vanishes(self, httpx_mock, monkeypatch):
        """Narrow race: if the old cache the guard is protecting gets
        invalidated by something else between reading prior_count and
        finishing retries, _load_from_cache would come back empty. Serving
        nothing would be exactly the failure mode this whole feature exists
        to prevent -- confirm the low-but-real fetch is served instead."""
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))
        monkeypatch.setattr(SQLBackend, "_load_from_cache", lambda self, *a, **k: [])

        backend = SQLBackend()
        visits = backend.fetch_raw_visits(opportunity_id=OPP_ID, access_token="t", pipeline_id=PIPELINE_ID)

        assert len(visits) == 2  # the low fetch, not an empty list
        assert backend.last_raw_fetch_anomaly == {
            "previous_count": 10,
            "attempted_count": 2,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }


@pytest.mark.django_db
@override_settings(CONNECT_PRODUCTION_URL="https://connect.example.com")
class TestStreamRawVisitsShrinkGuard:
    def test_first_ever_stream_skips_guard_even_if_small(self, httpx_mock):
        httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=2, pipeline_id=PIPELINE_ID
            )
        )
        assert events[-1][0] == "complete"
        assert len(events[-1][1]) == 2
        assert backend.last_raw_fetch_anomaly is None

    def test_stream_at_or_above_threshold_is_accepted_without_retry(self, httpx_mock):
        _seed_expired_cache(10)
        threshold = 10 * RAW_CACHE_SHRINK_THRESHOLD_PCT / 100
        httpx_mock.add_response(**_single_page_response(int(threshold)))  # one response only
        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )
        assert events[-1][0] == "complete"
        assert len(events[-1][1]) == int(threshold)
        assert backend.last_raw_fetch_anomaly is None

    def test_retries_and_succeeds_on_a_later_attempt(self, httpx_mock):
        _seed_expired_cache(10)
        httpx_mock.add_response(**_single_page_response(2))
        httpx_mock.add_response(**_single_page_response(9))
        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )
        assert events[-1][0] == "complete"
        assert len(events[-1][1]) == 9
        assert backend.last_raw_fetch_anomaly is None
        assert RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=9).count() == 9
        assert (
            RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=10).count() == 0
        )

    def test_falls_back_to_old_cache_after_exhausting_retries(self, httpx_mock):
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))

        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )

        assert events[-1][0] == "cached"
        assert len(events[-1][1]) == 10
        assert backend.last_raw_fetch_anomaly == {
            "previous_count": 10,
            "attempted_count": 2,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }
        # Old cache preserved -- no leftover sentinel/orphan rows from the
        # three failed+aborted attempts, and nothing was overwritten.
        assert RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID).count() == 10
        assert (
            RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=10).count() == 10
        )

    def test_accept_low_count_bypasses_the_guard(self, httpx_mock):
        _seed_expired_cache(10)
        httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID,
                access_token="t",
                expected_visit_count=10,
                pipeline_id=PIPELINE_ID,
                accept_low_count=True,
            )
        )
        assert events[-1][0] == "complete"
        assert len(events[-1][1]) == 2
        assert backend.last_raw_fetch_anomaly is None
        assert RawVisitCache.objects.filter(opportunity_id=OPP_ID, pipeline_id=PIPELINE_ID, visit_count=2).count() == 2

    def test_anomaly_persists_on_a_later_cache_hit(self, httpx_mock):
        """Mirrors the fetch_raw_visits regression test: extend_raw_cache_ttl()
        makes the old rows look like an ordinary valid cache again, so without
        get_pending_raw_fetch_anomaly a later reload would silently drop the
        flag."""
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )
        assert backend.last_raw_fetch_anomaly is not None

        reload_backend = SQLBackend()
        events = list(
            reload_backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )
        assert events[-1][0] == "cached"
        assert len(events[-1][1]) == 10
        assert reload_backend.last_raw_fetch_anomaly == {
            "previous_count": 10,
            "attempted_count": 2,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }

    def test_anomaly_clears_once_a_refresh_succeeds(self, httpx_mock):
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))
        backend = SQLBackend()
        list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )
        assert backend.last_raw_fetch_anomaly is not None

        httpx_mock.add_response(**_single_page_response(9))
        refreshed = SQLBackend()
        list(
            refreshed.stream_raw_visits(
                opportunity_id=OPP_ID,
                access_token="t",
                expected_visit_count=10,
                pipeline_id=PIPELINE_ID,
                force_refresh=True,
            )
        )
        assert refreshed.last_raw_fetch_anomaly is None

        reload_backend = SQLBackend()
        list(
            reload_backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=9, pipeline_id=PIPELINE_ID
            )
        )
        assert reload_backend.last_raw_fetch_anomaly is None

    def test_falls_back_to_low_fetch_when_old_cache_vanishes(self, httpx_mock, monkeypatch):
        """Mirrors the fetch_raw_visits regression test for the same narrow
        concurrent-invalidation race."""
        _seed_expired_cache(10)
        for _ in range(RAW_CACHE_MAX_ATTEMPTS):
            httpx_mock.add_response(**_single_page_response(2))
        monkeypatch.setattr(SQLBackend, "_load_from_cache", lambda self, *a, **k: [])

        backend = SQLBackend()
        events = list(
            backend.stream_raw_visits(
                opportunity_id=OPP_ID, access_token="t", expected_visit_count=10, pipeline_id=PIPELINE_ID
            )
        )

        assert events[-1][0] == "cached"
        assert len(events[-1][1]) == 2  # the low-but-real stream, not an empty list
        assert backend.last_raw_fetch_anomaly == {
            "previous_count": 10,
            "attempted_count": 2,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }
