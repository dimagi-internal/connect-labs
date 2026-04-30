import pytest

from commcare_connect.labs.analysis.backends.sql.query_builder import _aggregation_to_sql

pytestmark = pytest.mark.django_db(databases=[])


class TestAggregationToSQL:
    def test_count_distinct(self):
        result = _aggregation_to_sql("count_distinct", "beneficiary_case_id", "total_cases")
        assert "COUNT(DISTINCT" in result
        assert "beneficiary_case_id" in result

    def test_count_unique_alias(self):
        """count_unique should behave like count_distinct."""
        result = _aggregation_to_sql("count_unique", "case_id", "cases")
        assert "COUNT(DISTINCT" in result

    def test_last_uses_desc_array_agg(self):
        # As of the entity-stage refactor, first/last use ARRAY_AGG with explicit
        # ORDER BY (visit_date, visit_id) instead of a correlated subquery, because
        # the subquery's outer-column references were rejected by Postgres when the
        # GROUP BY was a JSONB-extracted entity-stage linking_field expression.
        result = _aggregation_to_sql("last", "weight", "last_weight")
        assert "ARRAY_AGG" in result
        assert "ORDER BY visit_date DESC" in result

    def test_count(self):
        result = _aggregation_to_sql("count", "visit_id", "total_visits")
        assert result == "COUNT(visit_id)"

    def test_first_uses_asc_array_agg(self):
        result = _aggregation_to_sql("first", "weight", "first_weight")
        assert "ARRAY_AGG" in result
        assert "ORDER BY visit_date ASC" in result

    def test_unknown_raises(self):
        """Previously fell through to MIN() silently. A typo like `counts`
        or `averge` would produce wrong numbers with no warning. Now it
        raises so the caller sees the error at pipeline-save or preview
        time with the list of valid aggregations."""
        import pytest

        with pytest.raises(ValueError, match="Unknown aggregation"):
            _aggregation_to_sql("bogus", "val", "field")


class TestFilteredAggregation:
    def test_count_distinct_with_filter(self):
        """COUNT(DISTINCT case_id) FILTER (WHERE child_alive = 'no')"""
        result = _aggregation_to_sql(
            "count_distinct",
            "COALESCE(form_json->'form'->>'kmc_beneficiary_case_id', '')",
            "deaths",
            filter_path="form.child_alive",
            filter_value="no",
        )
        assert "COUNT(DISTINCT" in result
        assert "FILTER" in result
        assert "child_alive" in result

    def test_count_with_filter(self):
        result = _aggregation_to_sql(
            "count",
            "form_json->'form'->>'danger_sign_positive'",
            "danger_positive",
            filter_path="form.danger_signs_checklist.danger_sign_positive",
            filter_value="yes",
        )
        assert "COUNT(" in result
        assert "FILTER" in result
        assert "danger_sign_positive" in result
        assert "'yes'" in result

    def test_no_filter_when_empty(self):
        result = _aggregation_to_sql("count", "val", "field")
        assert "FILTER" not in result

    def test_no_filter_when_only_path(self):
        result = _aggregation_to_sql("count", "val", "field", filter_path="form.x")
        assert "FILTER" not in result  # Both path and value required
