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

from connect_labs.labs.analysis.backends.sql.cache import CacheConcurrencyError, SQLCacheManager
from connect_labs.labs.analysis.backends.sql.models import (
    ComputedEntityCache,
    ComputedFLWCache,
    ComputedVisitCache,
    RawVisitCache,
)


def _make_manager(opportunity_id=42, pipeline_id=1001):
    """Cache manager with config_hash + pipeline_id set so writes are scoped properly.

    Real production callers always have a pipeline_id (the workflow definition id).
    Tests pass an explicit one so unique constraints fire — Postgres treats NULLs
    as distinct, so the constraint doesn't catch in-batch dups when pipeline_id is None.
    """
    from connect_labs.labs.analysis.config import AnalysisPipelineConfig

    config = AnalysisPipelineConfig(grouping_key="username", pipeline_id=pipeline_id)
    m = SQLCacheManager(opportunity_id=opportunity_id, config=config)
    m.config_hash = "deadbeefcafe1234deadbeefcafe1234"
    return m


@pytest.fixture
def manager():
    return _make_manager()


@pytest.mark.django_db
class TestRawVisitCacheConcurrency:
    """RawVisitCache: UNIQUE(opportunity_id, pipeline_id, visit_count, visit_id)."""

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
        # Same pipeline_id — both writers are racing within the same pipeline's slot.
        writer_a = _make_manager(pipeline_id=1001)
        writer_b = _make_manager(pipeline_id=1001)

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

    def test_different_pipelines_dont_clobber_each_other(self):
        """Two pipelines for the same opp keep separate raw caches (#116).

        This is the core regression that produced empty per-mother metrics
        on V2: each pipeline used to wholesale DELETE+INSERT for the opp,
        so the last pipeline to run was the only one with raw rows visible.
        """
        visits_pipeline = _make_manager(pipeline_id=2718)
        regs_pipeline = _make_manager(pipeline_id=2719)

        visits_pipeline.store_raw_visits(
            visit_dicts=[{"id": "v1", "username": "alice"}, {"id": "v2", "username": "bob"}],
            visit_count=2,
        )
        # Registrations writes after — must NOT delete the visits pipeline's rows.
        regs_pipeline.store_raw_visits(
            visit_dicts=[{"id": "m1", "username": "alice"}],
            visit_count=1,
        )

        # Each pipeline reads only its own slot
        assert RawVisitCache.objects.filter(opportunity_id=42, pipeline_id=2718).count() == 2
        assert RawVisitCache.objects.filter(opportunity_id=42, pipeline_id=2719).count() == 1
        # Total across pipelines
        assert RawVisitCache.objects.filter(opportunity_id=42).count() == 3

        # Same visit_id can exist in both pipelines without conflict
        # (separate slots, separate unique keys)
        regs_pipeline2 = _make_manager(pipeline_id=2719)
        regs_pipeline2.store_raw_visits(
            visit_dicts=[{"id": "v1", "username": "alice"}],  # same visit_id as visits pipeline
            visit_count=1,
        )
        assert RawVisitCache.objects.filter(opportunity_id=42, visit_id="v1").count() == 2


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
