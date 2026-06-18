"""End-to-end Postgres execution test for new aggregations.

Bounds the correctness of the in-memory aggregation runner in
reference_aggregation.py. We seed a minimal visit set into labs_raw_visit_cache,
run the SQL aggregation query, and assert the result matches what an
in-memory mirror produces for the same input.

This test requires a real Postgres database (the test runner uses
config.settings.test which points at PostGIS). It runs once per
aggregation type rather than per-fixture — its job is "does our SQL
fragment actually execute and give the right answer," not full coverage.
"""

import pytest
from django.db import connection
from django.utils import timezone

from commcare_connect.labs.analysis.backends.sql.models import RawVisitCache
from commcare_connect.labs.analysis.backends.sql.query_builder import _aggregation_to_sql


@pytest.mark.django_db
class TestAggregationSqlExecution:
    """Run each new aggregation through Postgres and verify the result."""

    def _seed_numeric(self, opp_id: int, rows: list[tuple[str, int | float]]) -> None:
        """Insert raw visits with `form_json.form.x = <value>` for each (username, value)."""
        future = timezone.now() + timezone.timedelta(days=1)
        for i, (username, value) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=opp_id,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(10000 + i),
                username=username,
                form_json={"form": {"x": value}},
                visit_date="2024-01-15",
                status="approved",
            )

    def _seed_categorical(self, opp_id: int, rows: list[tuple[str, str]]) -> None:
        """Insert raw visits with `form_json.form.parity = <value>`."""
        future = timezone.now() + timezone.timedelta(days=1)
        for i, (username, parity) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=opp_id,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(20000 + i),
                username=username,
                form_json={
                    "form": {
                        "confirm_visit_information": {
                            "parity__of_live_births_or_stillbirths_after_24_weeks": parity,
                        }
                    }
                },
                visit_date="2024-01-15",
                status="approved",
            )

    def _run_sql(self, sql_fragment: str, opp_id: int) -> list[tuple]:
        """Run `SELECT username, <agg> ... GROUP BY username, opportunity_id`.

        The double-key grouping mirrors `build_flw_aggregation_query` — required
        because the first/last/mode_share correlated subqueries reference the
        outer table's `opportunity_id`, and Postgres demands every correlated
        column either be grouped or aggregated.
        """
        sql = (
            f"SELECT username, {sql_fragment} "
            f"FROM labs_raw_visit_cache "
            f"WHERE opportunity_id = {opp_id} "
            f"GROUP BY username, opportunity_id"
        )
        with connection.cursor() as cur:
            cur.execute(sql)
            return cur.fetchall()

    def test_median_with_ints(self, db):
        self._seed_numeric(9991, [("a", 1), ("a", 2), ("a", 3), ("b", 10), ("b", 20)])
        value_expr = "(form_json #>> '{form,x}')::float"
        agg_sql = _aggregation_to_sql("median", value_expr, "med_x")
        results = dict(self._run_sql(agg_sql, 9991))
        assert results["a"] == pytest.approx(2.0)
        assert results["b"] == pytest.approx(15.0)

    def test_mode_returns_most_frequent(self, db):
        self._seed_categorical(9992, [("a", "G2P1"), ("a", "G2P1"), ("a", "G3P2"), ("b", "G1P0")])
        value_expr = (
            "form_json #>> '{form,confirm_visit_information," "parity__of_live_births_or_stillbirths_after_24_weeks}'"
        )
        agg_sql = _aggregation_to_sql("mode", value_expr, "mode_p")
        results = dict(self._run_sql(agg_sql, 9992))
        assert results["a"] == "G2P1"
        assert results["b"] == "G1P0"

    def test_mode_share_concentration(self, db):
        # All-same parity → 1.0; diverse → 0.333...
        self._seed_categorical(
            9993,
            [
                ("fraud", "G2P1"),
                ("fraud", "G2P1"),
                ("fraud", "G2P1"),
                ("diverse", "G1P0"),
                ("diverse", "G2P1"),
                ("diverse", "G3P2"),
            ],
        )
        value_expr = (
            "form_json #>> '{form,confirm_visit_information," "parity__of_live_births_or_stillbirths_after_24_weeks}'"
        )
        agg_sql = _aggregation_to_sql("mode_share", value_expr, "ms")
        results = dict(self._run_sql(agg_sql, 9993))
        assert results["fraud"] == pytest.approx(1.0)
        assert results["diverse"] == pytest.approx(1.0 / 3.0)

    def test_dup_share_executes(self, db):
        """`dup_share` SQL executes and matches in-memory mirror.

        For [A, A, B, C] the dup_share is 2/4 = 0.5 (A appears twice, B and C
        once each — only A's two rows are "in a duplicate group"). For
        [A, A, B, B] it's 4/4 = 1.0 (both pairs are duplicates).
        """
        future = timezone.now() + timezone.timedelta(days=1)
        rows = [
            ("a_only_dup", "A"),
            ("a_only_dup", "A"),
            ("a_only_dup", "B"),
            ("a_only_dup", "C"),
            ("all_dup", "A"),
            ("all_dup", "A"),
            ("all_dup", "B"),
            ("all_dup", "B"),
            ("all_distinct", "A"),
            ("all_distinct", "B"),
            ("all_distinct", "C"),
        ]
        for i, (u, p) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=9997,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(50000 + i),
                username=u,
                form_json={"form": {"x": p}},
                visit_date="2024-01-15",
                status="approved",
            )
        value_expr = "form_json #>> '{form,x}'"
        agg_sql = _aggregation_to_sql("dup_share", value_expr, "ds")
        results = dict(self._run_sql(agg_sql, 9997))
        assert results["a_only_dup"] == pytest.approx(0.5)
        assert results["all_dup"] == pytest.approx(1.0)
        assert results["all_distinct"] == pytest.approx(0.0)

    def test_contains_word_filter_executes(self, db):
        """`contains_word` filter_op produces tokenized membership SQL that
        Postgres accepts. V1 logic: `if "ebf" in bf_status.split()`.
        """
        future = timezone.now() + timezone.timedelta(days=1)
        rows = [
            ("flw_ebf", "ebf"),  # token match
            ("flw_ebf", "ebf bottle"),  # token match
            ("flw_ebf", "non-ebf"),  # substring, NOT a token → excluded
            ("flw_ebf", ""),  # empty → excluded
            ("flw_other", "exclusive_breastfeeding"),  # different token → excluded
        ]
        for i, (u, bf) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=9995,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(30000 + i),
                username=u,
                form_json={"form": {"feeding_history": {"pnc_current_bf_status": bf}}},
                visit_date="2024-01-15",
                status="approved",
            )
        value_expr = "form_json #>> '{form,feeding_history,pnc_current_bf_status}'"
        # COUNT(value) FILTER (WHERE 'ebf' = ANY(string_to_array(value, ' ')))
        agg_sql = _aggregation_to_sql(
            "count",
            value_expr,
            "ebf_count",
            filter_path="form.feeding_history.pnc_current_bf_status",
            filter_value="ebf",
            filter_op="contains_word",
        )
        results = dict(self._run_sql(agg_sql, 9995))
        assert results["flw_ebf"] == 2  # "ebf" and "ebf bottle" only
        assert results["flw_other"] == 0  # no token == "ebf"

    def test_pre_aggregated_field_executes_as_two_pass(self, db):
        """A FieldComputation with pre_aggregate_by produces SQL that
        executes correctly: per-mother first-parity, then per-FLW mode_share.

        Regression-tests the v1 quality-metric pattern where an FLW visiting
        ONE mother three times (same parity) must produce a different
        mode_share than an FLW visiting THREE mothers (one each, same parity).
        Without two-pass aggregation both would yield 1.0; with two-pass,
        the single-mother FLW yields 1.0 (1 unique value, 1 visit) and the
        three-mother FLW also yields 1.0 (3 unique mothers, all same parity)
        — so the discriminating case is when the three mothers have different
        parities, which is the test below.
        """
        from commcare_connect.labs.analysis.backends.sql.query_builder import _pre_aggregated_field_sql
        from commcare_connect.labs.analysis.config import FieldComputation

        future = timezone.now() + timezone.timedelta(days=1)

        # Fraud FLW: 3 visits to 1 mother, all same parity → 1 per-mother value.
        # Diverse FLW: 3 visits, 3 different mothers, 3 distinct parities → 3 per-mother values.
        # Without pre-aggregation both would yield mode_share = 1.0 (or 1/3) over raw rows.
        # With pre-aggregation: fraud yields 1.0, diverse yields 1/3.
        rows = [
            # fraud
            ("fraud", "mother_1", "G2P1"),
            ("fraud", "mother_1", "G2P1"),
            ("fraud", "mother_1", "G2P1"),
            # diverse
            ("diverse", "mother_a", "G1P0"),
            ("diverse", "mother_b", "G2P1"),
            ("diverse", "mother_c", "G3P2"),
        ]
        for i, (u, m, p) in enumerate(rows):
            RawVisitCache.objects.create(
                opportunity_id=9996,
                visit_count=len(rows),
                expires_at=future,
                visit_id=str(40000 + i),
                username=u,
                form_json={
                    "form": {
                        "parents": {"parent": {"case": {"@case_id": m}}},
                        "confirm_visit_information": {
                            "parity__of_live_births_or_stillbirths_after_24_weeks": p,
                        },
                    }
                },
                visit_date="2024-01-15",
                status="approved",
            )

        field = FieldComputation(
            name="parity_conc",
            path="form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
            aggregation="mode_share",
            pre_aggregate_by="form.parents.parent.case.@case_id",
            pre_aggregation="first",
        )
        sql_fragment = _pre_aggregated_field_sql(field)
        results = dict(self._run_sql(sql_fragment, 9996))
        assert results["fraud"] == pytest.approx(1.0)
        assert results["diverse"] == pytest.approx(1.0 / 3.0)

    def test_sql_agrees_with_inmemory_mirror_on_mixed_fixture(self, db):
        """Bound the in-memory mirror's correctness: SQL and Python must agree
        on the same input for median, mode, and mode_share.

        If this test ever fails, either the SQL builder or the in-memory
        reference_aggregation.aggregate has drifted. Both must be fixed in lockstep.
        """
        from commcare_connect.labs.analysis.backends.sql.tests.reference_aggregation import aggregate

        # Mixed numeric + categorical for one composite check.
        rows_num = [
            ("a", 1.0),
            ("a", 2.0),
            ("a", 3.0),
            ("a", 4.0),
            ("a", 5.0),
            ("b", 10.0),
            ("b", 20.0),
            ("b", 100.0),
        ]
        self._seed_numeric(9994, rows_num)
        value_expr = "(form_json #>> '{form,x}')::float"

        # median
        sql_median = dict(self._run_sql(_aggregation_to_sql("median", value_expr, "med"), 9994))
        py_median = aggregate(
            [{"u": u, "v": v} for u, v in rows_num],
            grouping_key="u",
            field_name="med",
            source_path="v",
            aggregation="median",
        )
        assert sql_median["a"] == pytest.approx(py_median["a"])
        assert sql_median["b"] == pytest.approx(py_median["b"])

        # mode_share — for [1,2,3,4,5] all distinct so mode_share = 1/5 = 0.2;
        # for [10,20,100] all distinct so mode_share = 1/3
        sql_share = dict(self._run_sql(_aggregation_to_sql("mode_share", value_expr, "ms"), 9994))
        py_share = aggregate(
            [{"u": u, "v": v} for u, v in rows_num],
            grouping_key="u",
            field_name="ms",
            source_path="v",
            aggregation="mode_share",
        )
        assert sql_share["a"] == pytest.approx(py_share["a"])
        assert sql_share["b"] == pytest.approx(py_share["b"])
