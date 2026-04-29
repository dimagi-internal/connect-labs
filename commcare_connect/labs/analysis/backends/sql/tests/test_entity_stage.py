"""Tests for the entity-stage pipeline.

Two layers:
- SQL-string tests verify build_entity_aggregation_query and the parameterized
  _aggregation_to_sql produce expected query shapes without touching a DB.
- DB-backed tests load raw visits into ComputedVisitCache's sibling RawVisitCache,
  run entity aggregation through the SQLBackend, and assert on the cached output
  shape — including parity with FLW stage when linking_field=username, the
  visit_id tiebreaker, NULL entity_id handling, histograms, and rebuild idempotence.
"""

from datetime import date

import pytest
from django.utils import timezone

from commcare_connect.labs.analysis.backends.sql.backend import SQLBackend
from commcare_connect.labs.analysis.backends.sql.cache import SQLCacheManager
from commcare_connect.labs.analysis.backends.sql.models import ComputedEntityCache, RawVisitCache
from commcare_connect.labs.analysis.backends.sql.query_builder import (
    _aggregation_to_sql,
    _resolve_linking_field_outer_expr,
    build_entity_aggregation_query,
)
from commcare_connect.labs.analysis.config import (
    AnalysisPipelineConfig,
    CacheStage,
    FieldComputation,
    HistogramComputation,
)

# ---------------------------------------------------------------------------
# Schema-level validation
# ---------------------------------------------------------------------------


class TestConfigValidation:
    def test_entity_stage_requires_linking_field(self):
        # linking_field has a default of "entity_id", so this should be valid
        # — explicit empty string should fail.
        with pytest.raises(ValueError, match="linking_field is required"):
            AnalysisPipelineConfig(
                grouping_key="username",
                terminal_stage=CacheStage.ENTITY,
                linking_field="",
            )

    def test_entity_stage_default_linking_field_is_entity_id(self):
        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
        )
        assert config.linking_field == "entity_id"


# ---------------------------------------------------------------------------
# Pure SQL-string tests (no DB)
# ---------------------------------------------------------------------------


class TestResolveLinkingField:
    def test_base_column_resolves_directly(self):
        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="entity_id",
        )
        expr = _resolve_linking_field_outer_expr(config)
        assert expr == "labs_raw_visit_cache.entity_id"

    def test_field_computation_resolves_to_jsonb_path(self):
        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="beneficiary_case_id",
            fields=[
                FieldComputation(
                    name="beneficiary_case_id",
                    paths=["form.case.@case_id", "form.kmc_beneficiary_case_id"],
                    aggregation="first",
                ),
            ],
        )
        expr = _resolve_linking_field_outer_expr(config)
        assert "labs_raw_visit_cache.form_json" in expr
        assert "case_id" in expr
        # Must use COALESCE since multiple paths
        assert "COALESCE" in expr

    def test_unknown_linking_field_raises(self):
        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="not_a_real_field",
        )
        with pytest.raises(ValueError, match="not a base column"):
            _resolve_linking_field_outer_expr(config)


class TestBuildEntityAggregationQuery:
    def _config(self, linking_field="entity_id", fields=None, histograms=None):
        return AnalysisPipelineConfig(
            grouping_key="username",
            fields=fields or [],
            histograms=histograms or [],
            terminal_stage=CacheStage.ENTITY,
            linking_field=linking_field,
        )

    def test_groups_by_linking_field_and_opportunity_id(self):
        """opportunity_id must appear in GROUP BY for the same reason as FLW —
        correlated subqueries reference it from the outer table.
        """
        query = build_entity_aggregation_query(self._config(), opportunity_id=42)
        assert "GROUP BY (labs_raw_visit_cache.entity_id), opportunity_id" in query

    def test_where_clause_restricts_to_opportunity(self):
        query = build_entity_aggregation_query(self._config(), opportunity_id=999)
        assert "WHERE opportunity_id = 999" in query

    def test_drops_status_counters(self):
        """Entity stage doesn't get approved_visits/etc — those are visit-level facts."""
        query = build_entity_aggregation_query(self._config(), opportunity_id=1)
        assert "approved_visits" not in query
        assert "pending_visits" not in query
        assert "rejected_visits" not in query
        assert "flagged_visits" not in query

    def test_includes_total_visits_and_date_range(self):
        query = build_entity_aggregation_query(self._config(), opportunity_id=1)
        assert "COUNT(*) as total_visits" in query
        assert "MIN(visit_date) as _base_first_visit_date" in query
        assert "MAX(visit_date) as _base_last_visit_date" in query

    def test_username_is_first_per_entity(self):
        """Representative FLW per entity is first(username), not GROUP BY username."""
        query = build_entity_aggregation_query(self._config(), opportunity_id=1)
        # The first(username) subquery should appear as the username column
        assert "as username" in query


class TestAggregationToSqlGroupColumn:
    """Verify that _aggregation_to_sql honors group_column_outer_expr.

    The default keeps FLW behavior unchanged; non-default callers (entity stage)
    should see the inner-qualified group column propagated into the correlated
    subquery's WHERE clause.
    """

    def test_first_default_group_column_is_username(self):
        result = _aggregation_to_sql("first", "v", "f")
        assert "labs_raw_visit_cache.username" in result
        assert "sub.username" in result

    def test_first_visit_id_tiebreaker_present(self):
        """Tiebreaker: visit_id ASC for first, DESC for last, at both stages."""
        first_sql = _aggregation_to_sql("first", "v", "f")
        last_sql = _aggregation_to_sql("last", "v", "f")
        assert "ORDER BY visit_date ASC, visit_id ASC" in first_sql
        assert "ORDER BY visit_date DESC, visit_id DESC" in last_sql

    def test_first_with_entity_group_column(self):
        outer = "labs_raw_visit_cache.entity_id"
        result = _aggregation_to_sql("first", "v", "f", group_column_outer_expr=outer)
        # Outer side uses the passed-in expression; inner side substitutes prefix.
        assert "(labs_raw_visit_cache.entity_id)" in result
        assert "(sub.entity_id)" in result
        # Old hardcoded username clause must be gone
        assert "sub.username = labs_raw_visit_cache.username" not in result

    def test_first_with_jsonb_group_expression(self):
        """Passing a JSONB-path linking_field must produce sub-qualified inner side."""
        outer = "COALESCE(NULLIF(labs_raw_visit_cache.form_json->'form'->>'case_id', ''))"
        result = _aggregation_to_sql("first", "v", "f", group_column_outer_expr=outer)
        # Inner version: every labs_raw_visit_cache.X becomes sub.X
        assert "sub.form_json->'form'->>'case_id'" in result
        assert "labs_raw_visit_cache.form_json->'form'->>'case_id'" in result


# ---------------------------------------------------------------------------
# DB-backed integration tests
# ---------------------------------------------------------------------------


@pytest.fixture
def raw_visits_factory(db):
    """Insert raw visits into RawVisitCache and return a config that can use them."""

    def _factory(opp_id: int, visits: list[dict]):
        expires_at = timezone.now() + timezone.timedelta(hours=1)
        rows = [
            RawVisitCache(
                opportunity_id=opp_id,
                visit_count=len(visits),
                expires_at=expires_at,
                visit_id=str(v["visit_id"]),
                username=v.get("username", ""),
                entity_id=v.get("entity_id", ""),
                entity_name=v.get("entity_name", ""),
                visit_date=v.get("visit_date"),
                status=v.get("status", "approved"),
                form_json=v.get("form_json", {}),
            )
            for v in visits
        ]
        RawVisitCache.objects.bulk_create(rows)
        return rows

    return _factory


class TestEntityStageIntegration:
    """End-to-end: raw visits → entity-stage aggregation → cached EntityRow."""

    def test_groups_visits_by_entity_id(self, raw_visits_factory):
        opp_id = 9001
        raw_visits_factory(
            opp_id,
            [
                {
                    "visit_id": 1,
                    "username": "alice",
                    "entity_id": "child-A",
                    "entity_name": "Alice's Child",
                    "visit_date": date(2026, 4, 1),
                },
                {
                    "visit_id": 2,
                    "username": "alice",
                    "entity_id": "child-A",
                    "entity_name": "Alice's Child",
                    "visit_date": date(2026, 4, 5),
                },
                {
                    "visit_id": 3,
                    "username": "bob",
                    "entity_id": "child-B",
                    "entity_name": "Bob's Child",
                    "visit_date": date(2026, 4, 2),
                },
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="entity_id",
        )
        backend = SQLBackend()
        result = backend._process_entity_level(
            config, opp_id, visit_count=3, cache_manager=SQLCacheManager(opp_id, config)
        )

        rows = sorted(result.rows, key=lambda r: r.entity_id)
        assert [r.entity_id for r in rows] == ["child-A", "child-B"]
        assert rows[0].total_visits == 2
        assert rows[1].total_visits == 1
        # Representative FLW is first by visit_date+visit_id
        assert rows[0].username == "alice"
        assert rows[1].username == "bob"
        # entity_name is denormalized from base column
        assert rows[0].entity_name == "Alice's Child"
        # Date range
        assert rows[0].first_visit_date == date(2026, 4, 1)
        assert rows[0].last_visit_date == date(2026, 4, 5)

    def test_first_last_uses_visit_id_tiebreaker(self, raw_visits_factory):
        """When two visits share visit_date, ties must resolve by visit_id (ASC for first, DESC for last).

        Note: visit_id is a CharField (migration 0008), so ORDER BY does lex ordering.
        We use single-digit IDs so lex == numeric and the test is unambiguous.
        """
        opp_id = 9002
        raw_visits_factory(
            opp_id,
            [
                # Same visit_date, different visit_id — ties must break by visit_id.
                {
                    "visit_id": "2",
                    "username": "u1",
                    "entity_id": "ent-1",
                    "visit_date": date(2026, 4, 1),
                    "form_json": {"form": {"weight": "5.0"}},
                },
                {
                    "visit_id": "1",
                    "username": "u1",
                    "entity_id": "ent-1",
                    "visit_date": date(2026, 4, 1),
                    "form_json": {"form": {"weight": "3.0"}},
                },
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="entity_id",
            fields=[
                FieldComputation(name="first_weight", path="form.weight", aggregation="first"),
                FieldComputation(name="last_weight", path="form.weight", aggregation="last"),
            ],
        )
        backend = SQLBackend()
        result = backend._process_entity_level(
            config, opp_id, visit_count=2, cache_manager=SQLCacheManager(opp_id, config)
        )

        assert len(result.rows) == 1
        row = result.rows[0]
        # first: smaller visit_id ("1") wins → weight 3.0
        # last: larger visit_id ("2") wins → weight 5.0
        assert row.custom_fields["first_weight"] == "3.0"
        assert row.custom_fields["last_weight"] == "5.0"

    def test_null_entity_id_collapses_into_one_row(self, raw_visits_factory):
        """Visits whose linking_field path doesn't extract collapse into a single GROUP BY row.

        The behavior should be stable across cache rebuilds — that's what we lock in.
        """
        opp_id = 9003
        raw_visits_factory(
            opp_id,
            [
                {"visit_id": 1, "username": "u1", "entity_id": "", "visit_date": date(2026, 4, 1)},
                {"visit_id": 2, "username": "u1", "entity_id": "", "visit_date": date(2026, 4, 2)},
                {"visit_id": 3, "username": "u1", "entity_id": "ent-1", "visit_date": date(2026, 4, 3)},
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="entity_id",
        )
        backend = SQLBackend()
        result = backend._process_entity_level(
            config, opp_id, visit_count=3, cache_manager=SQLCacheManager(opp_id, config)
        )

        # Two rows: one for empty entity_id (with 2 visits), one for ent-1
        rows_by_entity = {r.entity_id: r for r in result.rows}
        assert len(rows_by_entity) == 2
        assert rows_by_entity[""].total_visits == 2
        assert rows_by_entity["ent-1"].total_visits == 1

    def test_histogram_works_at_entity_stage(self, raw_visits_factory):
        """Histogram bins use the standard FILTER aggregations and don't reference username."""
        opp_id = 9004
        raw_visits_factory(
            opp_id,
            [
                {
                    "visit_id": i,
                    "username": "u1",
                    "entity_id": "ent-1",
                    "visit_date": date(2026, 4, i + 1),
                    "form_json": {"form": {"muac": str(value)}},
                }
                for i, value in enumerate([10.0, 11.0, 12.0, 13.0, 14.0])
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="entity_id",
            histograms=[
                HistogramComputation(
                    name="muac_dist",
                    path="form.muac",
                    lower_bound=10.0,
                    upper_bound=15.0,
                    num_bins=5,
                    bin_name_prefix="muac",
                    transform=lambda x: float(x) if x else None,
                )
            ],
        )
        backend = SQLBackend()
        result = backend._process_entity_level(
            config, opp_id, visit_count=5, cache_manager=SQLCacheManager(opp_id, config)
        )

        assert len(result.rows) == 1
        custom = result.rows[0].custom_fields
        # Each bin should have exactly one value
        assert custom["muac_10_0_11_0_visits"] == 1
        assert custom["muac_11_0_12_0_visits"] == 1
        assert custom["muac_12_0_13_0_visits"] == 1
        # Summary stats
        assert custom["muac_dist_count"] == 5

    def test_rebuild_is_idempotent(self, raw_visits_factory):
        """Running the entity pipeline twice produces the same rows; no orphans."""
        opp_id = 9005
        raw_visits_factory(
            opp_id,
            [
                {"visit_id": 1, "username": "u1", "entity_id": "ent-1", "visit_date": date(2026, 4, 1)},
                {"visit_id": 2, "username": "u1", "entity_id": "ent-2", "visit_date": date(2026, 4, 2)},
            ],
        )

        config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="entity_id",
        )
        backend = SQLBackend()
        cache_mgr = SQLCacheManager(opp_id, config)

        backend._process_entity_level(config, opp_id, visit_count=2, cache_manager=cache_mgr)
        first_count = ComputedEntityCache.objects.filter(opportunity_id=opp_id).count()

        backend._process_entity_level(config, opp_id, visit_count=2, cache_manager=cache_mgr)
        second_count = ComputedEntityCache.objects.filter(opportunity_id=opp_id).count()

        assert first_count == 2
        assert first_count == second_count

    def test_parity_with_flw_when_grouping_by_username(self, raw_visits_factory):
        """When linking_field=username, entity-stage output should match FLW-stage
        output in the fields they share (entity_id<->username, total_visits, dates).

        Locks in the refactor: same GROUP BY column, same aggregation vocabulary,
        same row count.
        """
        opp_id = 9006
        raw_visits_factory(
            opp_id,
            [
                {"visit_id": 1, "username": "alice", "entity_id": "x", "visit_date": date(2026, 4, 1)},
                {"visit_id": 2, "username": "alice", "entity_id": "y", "visit_date": date(2026, 4, 2)},
                {"visit_id": 3, "username": "bob", "entity_id": "z", "visit_date": date(2026, 4, 3)},
            ],
        )

        flw_config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.AGGREGATED,
        )
        entity_config = AnalysisPipelineConfig(
            grouping_key="username",
            terminal_stage=CacheStage.ENTITY,
            linking_field="username",
        )
        backend = SQLBackend()

        flw_result = backend._process_flw_level(
            flw_config, opp_id, visit_count=3, cache_manager=SQLCacheManager(opp_id, flw_config)
        )
        entity_result = backend._process_entity_level(
            entity_config, opp_id, visit_count=3, cache_manager=SQLCacheManager(opp_id, entity_config)
        )

        assert len(flw_result.rows) == len(entity_result.rows) == 2
        flw_by_user = {r.username: r for r in flw_result.rows}
        entity_by_id = {r.entity_id: r for r in entity_result.rows}
        assert set(flw_by_user.keys()) == set(entity_by_id.keys())

        for username in flw_by_user:
            flw = flw_by_user[username]
            entity = entity_by_id[username]
            assert flw.total_visits == entity.total_visits
            assert flw.first_visit_date == entity.first_visit_date
            assert flw.last_visit_date == entity.last_visit_date
