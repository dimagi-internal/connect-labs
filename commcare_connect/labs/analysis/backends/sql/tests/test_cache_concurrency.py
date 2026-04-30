"""Tests for cache concurrency guard.

Background: concurrent pipeline runs targeting the same (opportunity,
config) used to silently both succeed in writing their full row sets
because the delete-then-insert "guard" took no row locks when the table
was empty. Result: 2x rows in cache (incident on opp 765 — 172120 rows
for 86060 actual visits).

Migration 0011 adds UNIQUE constraints on each cache table. A concurrent
write now collides on insert: one writer wins, the loser raises
IntegrityError which the cache layer surfaces as
``CacheConcurrencyError``. The pipeline view catches this and reports a
clean SSE error so the user can retry.

These tests assert the new contract: single writes still work, and a
duplicate within a single batch raises the typed error.
"""

import pytest

from commcare_connect.labs.analysis.backends.sql.cache import CacheConcurrencyError, SQLCacheManager
from commcare_connect.labs.analysis.backends.sql.models import (
    ComputedEntityCache,
    ComputedFLWCache,
    ComputedVisitCache,
    RawVisitCache,
)


@pytest.fixture
def manager():
    """Cache manager with a non-empty config_hash so computed-cache writes are not no-ops."""
    m = SQLCacheManager(opportunity_id=42, config=None)
    m.config_hash = "deadbeefcafe1234deadbeefcafe1234"
    return m


@pytest.mark.django_db
class TestRawVisitCacheConcurrency:
    """RawVisitCache: UNIQUE(opportunity_id, visit_count, visit_id)."""

    def test_single_write_succeeds(self, manager):
        manager.store_raw_visits(
            visit_dicts=[
                {"id": 1, "username": "alice"},
                {"id": 2, "username": "bob"},
            ],
            visit_count=2,
        )
        assert RawVisitCache.objects.filter(opportunity_id=42).count() == 2

    def test_in_batch_dup_raises_concurrency_error(self, manager):
        """Same visit_id twice in one bulk_create -> caught by unique index -> typed error."""
        with pytest.raises(CacheConcurrencyError) as excinfo:
            manager.store_raw_visits(
                visit_dicts=[
                    {"id": 1, "username": "alice"},
                    {"id": 1, "username": "alice"},  # dup
                ],
                visit_count=2,
            )
        assert excinfo.value.table == "labs_raw_visit_cache"
        assert excinfo.value.opportunity_id == 42

    def test_streaming_writers_with_different_sentinels_coexist(self, manager):
        """The streaming protocol uses unique negative sentinels per writer.

        Two writers can insert overlapping visit_ids as long as their
        visit_count sentinels differ (UNIQUE includes visit_count). After
        finalize, one wins and the other's rows are deleted.
        """
        writer_a = SQLCacheManager(opportunity_id=42, config=None)
        writer_b = SQLCacheManager(opportunity_id=42, config=None)

        writer_a.store_raw_visits_start(visit_count=10)
        writer_b.store_raw_visits_start(visit_count=10)

        # Different sentinels by construction (random.randint)
        assert writer_a._pending_visit_count != writer_b._pending_visit_count

        writer_a.store_raw_visits_batch([{"id": 1, "username": "alice"}])
        writer_b.store_raw_visits_batch([{"id": 1, "username": "alice"}])  # same visit_id, different sentinel

        # Both rows coexist (different sentinels = different unique keys)
        assert RawVisitCache.objects.filter(opportunity_id=42).count() == 2

        # Writer A finalizes -> wipes everything except its own rows
        writer_a.store_raw_visits_finalize(actual_count=1)
        assert RawVisitCache.objects.filter(opportunity_id=42, visit_count=1).count() == 1


@pytest.mark.django_db
class TestComputedVisitCacheConcurrency:
    """ComputedVisitCache: UNIQUE(opportunity_id, config_hash, visit_id)."""

    def test_single_write_succeeds(self, manager):
        manager.store_computed_visits(
            visits_data=[
                {"visit_id": "v1", "username": "alice", "computed_fields": {}},
                {"visit_id": "v2", "username": "bob", "computed_fields": {}},
            ],
            visit_count=2,
        )
        assert ComputedVisitCache.objects.filter(opportunity_id=42).count() == 2

    def test_in_batch_dup_raises_concurrency_error(self, manager):
        with pytest.raises(CacheConcurrencyError) as excinfo:
            manager.store_computed_visits(
                visits_data=[
                    {"visit_id": "v1", "username": "alice", "computed_fields": {}},
                    {"visit_id": "v1", "username": "alice", "computed_fields": {}},
                ],
                visit_count=2,
            )
        assert excinfo.value.table == "labs_computed_visit_cache"

    def test_second_write_overwrites_first(self, manager):
        """Sequential (non-overlapping) writes still work via delete-then-insert."""
        manager.store_computed_visits(
            visits_data=[{"visit_id": "v1", "username": "alice", "computed_fields": {"x": 1}}],
            visit_count=1,
        )
        manager.store_computed_visits(
            visits_data=[
                {"visit_id": "v1", "username": "alice", "computed_fields": {"x": 2}},
                {"visit_id": "v2", "username": "bob", "computed_fields": {"x": 3}},
            ],
            visit_count=2,
        )
        rows = ComputedVisitCache.objects.filter(opportunity_id=42).order_by("visit_id")
        assert rows.count() == 2
        # Confirm second write replaced the first
        assert rows[0].computed_fields == {"x": 2}


@pytest.mark.django_db
class TestComputedFLWCacheConcurrency:
    def test_single_write_succeeds(self, manager):
        manager.store_flw_results(
            flw_data=[{"username": "alice"}, {"username": "bob"}],
            visit_count=10,
        )
        assert ComputedFLWCache.objects.filter(opportunity_id=42).count() == 2

    def test_in_batch_dup_raises_concurrency_error(self, manager):
        with pytest.raises(CacheConcurrencyError) as excinfo:
            manager.store_flw_results(
                flw_data=[{"username": "alice"}, {"username": "alice"}],
                visit_count=10,
            )
        assert excinfo.value.table == "labs_computed_flw_cache"


@pytest.mark.django_db
class TestComputedEntityCacheConcurrency:
    def test_single_write_succeeds(self, manager):
        manager.store_entity_results(
            entity_data=[
                {"entity_id": "e1", "username": "alice"},
                {"entity_id": "e2", "username": "bob"},
            ],
            visit_count=10,
        )
        assert ComputedEntityCache.objects.filter(opportunity_id=42).count() == 2

    def test_in_batch_dup_raises_concurrency_error(self, manager):
        with pytest.raises(CacheConcurrencyError) as excinfo:
            manager.store_entity_results(
                entity_data=[
                    {"entity_id": "e1", "username": "alice"},
                    {"entity_id": "e1", "username": "alice"},
                ],
                visit_count=10,
            )
        assert excinfo.value.table == "labs_computed_entity_cache"
