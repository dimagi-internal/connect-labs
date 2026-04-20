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
