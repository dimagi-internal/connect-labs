"""Unit tests for the SQL query builder.

Focuses on regressions we've actually hit — starting with the
GROUP BY / correlated-subquery bug that broke `first`/`last` aggregations
for every pipeline preview.
"""

from commcare_connect.labs.analysis.backends.sql.query_builder import _aggregation_to_sql, build_flw_aggregation_query
from commcare_connect.labs.analysis.config import AnalysisPipelineConfig, FieldComputation


def _config(fields):
    return AnalysisPipelineConfig(
        grouping_key="username",
        fields=fields,
        histograms=[],
        filters={},
        experiment="test",
    )


class TestBuildFlwAggregationQuery:
    def test_group_by_includes_opportunity_id_for_first_last_safety(self):
        """`first` and `last` aggregations emit a correlated subquery that
        references labs_raw_visit_cache.opportunity_id. Postgres requires
        every correlated column to be either grouped or aggregated in the
        outer query; without opportunity_id in the GROUP BY it fails with
        `subquery uses ungrouped column "labs_raw_visit_cache.opportunity_id"
        from outer query`.
        """
        config = _config(
            [
                FieldComputation(name="visit_count", path="form.meta.instanceID", aggregation="count"),
                FieldComputation(name="last_visit", path="form.meta.timeEnd", aggregation="last"),
            ]
        )
        query = build_flw_aggregation_query(config, opportunity_id=1237)
        assert "GROUP BY username, opportunity_id" in query

    def test_where_clause_still_restricts_to_single_opp(self):
        """Adding opportunity_id to GROUP BY is safe precisely because the
        WHERE clause restricts to one opp already — confirm that's still there.
        """
        config = _config([FieldComputation(name="n", path="form.x", aggregation="count")])
        query = build_flw_aggregation_query(config, opportunity_id=999)
        assert "WHERE opportunity_id = 999" in query


class TestAggregationToSql:
    def test_raises_on_unknown_aggregation_instead_of_silent_min(self):
        """Prior behaviour was to fall through to MIN() for any unknown
        aggregation — a typo like `aggregation: "counts"` would silently
        produce wrong data. Now we fail loudly."""
        import pytest

        with pytest.raises(ValueError, match="Unknown aggregation"):
            _aggregation_to_sql("counts", "value_expr", "my_field")

    def test_min_and_max_are_explicit(self):
        """min/max are now explicit branches, not the else fallback."""
        assert _aggregation_to_sql("min", "v", "f") == "MIN(v)"
        assert _aggregation_to_sql("max", "v", "f") == "MAX(v)"

    def test_median_uses_percentile_cont(self):
        """`median` is the standard interpolated 50th percentile in Postgres."""
        sql = _aggregation_to_sql("median", "v", "f")
        assert sql == "PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY v)"

    def test_mode_uses_mode_within_group(self):
        """`mode` returns the most frequent non-null value (ties broken by Postgres)."""
        sql = _aggregation_to_sql("mode", "v", "f")
        assert sql == "MODE() WITHIN GROUP (ORDER BY v)"

    def test_dup_share_uses_correlated_subquery(self):
        """`dup_share` returns share (0..1) of values that appear in groups
        of >1. Same correlated-subquery shape as mode_share — pairs with it
        for fraud detection. v1's pct_duplicate.
        """
        sql = _aggregation_to_sql("dup_share", "v", "f")
        assert "FROM labs_raw_visit_cache sub" in sql
        assert "GROUP BY v" in sql
        assert "FILTER (WHERE c > 1)" in sql
        assert "::float" in sql

    def test_mode_share_uses_correlated_subquery(self):
        """`mode_share` returns share (0..1) of rows whose value equals the mode.

        Implementation note: Postgres rejects aggregate functions inside FILTER
        (WHERE ...) clauses, so the obvious shape `COUNT FILTER (= MODE())` is
        illegal. Instead this aggregation emits a correlated subquery that
        groups by value and takes max-count / sum-count. Mirrors the first/last
        subquery pattern.

        Used for fraud-concentration metrics: 1.0 means every visit by an FLW
        has the same parity; 0.1 means parity is well-distributed across visits.
        """
        sql = _aggregation_to_sql("mode_share", "v", "f")
        # Subquery shape over labs_raw_visit_cache aliased as sub
        assert "SELECT MAX(c)::float / NULLIF(SUM(c), 0)" in sql
        assert "FROM labs_raw_visit_cache sub" in sql
        # Correlation against the outer row's username/opportunity
        assert "sub.opportunity_id = labs_raw_visit_cache.opportunity_id" in sql
        assert "sub.username = labs_raw_visit_cache.username" in sql
        # Inner GROUP BY value, COUNT(*) — that's what produces per-value frequencies.
        assert "GROUP BY v" in sql
        assert "COUNT(*) AS c" in sql
        # MUST NOT use the illegal FILTER (= MODE()) shape.
        assert "= MODE()" not in sql


class TestAggregationTypeLiteral:
    """The `AggregationType` Literal in config.py must list every aggregation
    `_aggregation_to_sql` accepts; otherwise template authors get a typing
    error at edit time but a runtime ValueError at execution time.
    """

    def test_new_aggregations_in_literal(self):
        from typing import get_args

        from commcare_connect.labs.analysis.config import AggregationType

        members = set(get_args(AggregationType))
        assert "median" in members
        assert "mode" in members
        assert "mode_share" in members
