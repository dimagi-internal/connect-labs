"""A declared field or filter naming a JSONB base column must read its logical
value — not its JSON serialization (dimagi-internal/ace#2431).

`flag_reason` is JSONB on `labs_raw_visit_cache`. Two paths were wrong, and
both were silent:

* `{aggregation: count, path: flag_reason}` compiled to
  `COUNT(NULLIF(flag_reason::text, ''))`. An unflagged visit stores `{}`, whose
  `::text` is the two-character string `'{}'` — not NULL — so the count came
  back as the row count. Measured on labs opp 10065 / pipeline 5694: 235 / 315 /
  260 per worker (each worker's `total_visits`) where the truth was 17 / 9 / 11
  out of 137 flagged in 2,207.
* `{filter_path: flag_reason, filter_value: "response_pattern_outlier"}`
  compiled to `TRIM(form_json->>'flag_reason') = '...'` — the singular
  `filter_path` branch never resolved base columns at all, so the comparison was
  against a form path that does not exist and matched zero rows. Routing it
  through the base-column resolver is not enough on its own either: a JSON
  string serializes as `"response_pattern_outlier"` *with* the quotes, so the
  value a schema author writes could never have matched.

Neither showed up in `fields_all_null` — that signal is NULL-only, and these
fields were not null, just wrong. A count returning the row count is worse than
an error, because it is plausible and nobody re-derives it.
"""

import pytest
from django.db import connection
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.backends.sql.query_builder import (
    _jsonb_base_column_text_sql,
    _paths_to_coalesce_sql,
    build_flw_aggregation_query,
)

# The measured shape from the report, reproduced exactly: 2,207 visits, 137 of
# them flagged, 17 of those carrying `response_pattern_outlier`.
TOTAL_VISITS = 2207
FLAGGED_VISITS = 137
OUTLIER_VISITS = 17

#: `flag_reason` is `JSONField(default=dict)` and NOT NULL, so a JSON `null` has
#: to go in through a raw UPDATE. A SQL NULL cannot be written to this column at
#: all — its NULL-safety is asserted against the expression directly, in
#: TestTheEmptinessRule, rather than through a row.
JSON_NULL = object()


def _config(opp_id: int, fields: list[dict]):
    from connect_labs.workflow.data_access import PipelineDataAccess

    schema = {
        "terminal_stage": "aggregated",
        "data_source": {"type": "connect_export", "endpoint": "user_visits"},
        "fields": fields,
    }
    access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
    return access._schema_to_config(schema, definition_id=opp_id)


def _run(opp_id: int, fields: list[dict]) -> list[dict]:
    sql = build_flw_aggregation_query(_config(opp_id, fields), opportunity_id=opp_id)
    with connection.cursor() as cur:
        cur.execute(sql)
        cols = [c[0] for c in cur.description]
        return [dict(zip(cols, r)) for r in cur.fetchall()]


def _seed(opp_id: int, flag_reasons: list) -> None:
    """One visit per entry, `flag_reasons[i]` stored verbatim in the JSONB
    column — `{}`, `[]`, a scalar, an object, or `JSON_NULL`."""
    future = timezone.now() + timezone.timedelta(days=1)
    RawVisitCache.objects.bulk_create(
        [
            RawVisitCache(
                opportunity_id=opp_id,
                pipeline_id=opp_id,
                visit_count=len(flag_reasons),
                expires_at=future,
                visit_id=f"{opp_id}-{i}",
                username="flw_a",
                visit_date="2026-09-01",
                status="approved",
                flagged=reason not in (JSON_NULL, {}),
                flag_reason={} if reason is JSON_NULL else reason,
                form_json={"form": {"x": "1"}},
            )
            for i, reason in enumerate(flag_reasons)
        ],
        batch_size=500,
    )
    with connection.cursor() as cur:
        for i, reason in enumerate(flag_reasons):
            if reason is JSON_NULL:
                cur.execute(
                    "UPDATE labs_raw_visit_cache SET flag_reason = 'null'::jsonb "
                    "WHERE opportunity_id = %s AND visit_id = %s",
                    [opp_id, f"{opp_id}-{i}"],
                )


def _measured_population() -> list:
    """2,207 visits: 137 flagged, of which 17 are response_pattern_outlier."""
    held = ["response_pattern_outlier"] * OUTLIER_VISITS + ["duplicate_identifiers"] * (
        FLAGGED_VISITS - OUTLIER_VISITS
    )
    return held + [{}] * (TOTAL_VISITS - FLAGGED_VISITS)


@pytest.mark.django_db
class TestTheEmptinessRule:
    """What "present" means for a JSONB base column, one literal at a time.

    Runs the emitted expression over a VALUES list rather than over rows, so it
    can cover the two shapes the ORM cannot write (SQL NULL, JSON null) and
    states the whole rule in one table.
    """

    CASES = [
        # (json literal or None for SQL NULL, expected text, expected presence)
        (None, None, False),  # SQL NULL
        ("null", None, False),  # JSON null
        ("{}", None, False),  # the empty default every unflagged visit carries
        ("[]", None, False),
        ('""', None, False),  # an empty string is an absence, as on a form path
        ('"response_pattern_outlier"', "response_pattern_outlier", True),
        ("0", "0", True),  # a value, not an absence
        ("false", "false", True),
        ('{"flags": [["dup", "m"]]}', '{"flags": [["dup", "m"]]}', True),
        ('["a"]', '["a"]', True),
    ]

    def test_each_literal_reads_as_declared(self, db):
        expr = _jsonb_base_column_text_sql("v")
        for literal, expected_text, expected_present in self.CASES:
            operand = "NULL::jsonb" if literal is None else f"'{literal}'::jsonb"
            with connection.cursor() as cur:
                cur.execute(f"SELECT {expr} FROM (SELECT {operand} AS v) t")
                (got,) = cur.fetchone()
            assert got == expected_text, literal
            assert (got is not None) is expected_present, literal


@pytest.mark.django_db
class TestCountOverAJsonbBaseColumn:
    def test_the_positive_control_counts_the_populated_rows_not_every_row(self, db):
        """The headline defect: 2,207 returned where 137 was the truth."""
        _seed(41001, _measured_population())
        (row,) = _run(41001, [{"name": "held_for_review", "path": "flag_reason", "aggregation": "count"}])

        assert row["total_visits"] == TOTAL_VISITS
        assert row["held_for_review"] == FLAGGED_VISITS
        assert row["held_for_review"] != row["total_visits"]

    def test_an_empty_object_and_a_json_null_both_count_as_absent(self, db):
        _seed(41002, [{}, JSON_NULL, {}, JSON_NULL, "held"])
        (row,) = _run(41002, [{"name": "held", "path": "flag_reason", "aggregation": "count"}])

        assert row["total_visits"] == 5
        assert row["held"] == 1

    def test_an_empty_array_and_an_empty_string_count_as_absent(self, db):
        _seed(41003, [[], "", {}, ["a_flag"]])
        (row,) = _run(41003, [{"name": "held", "path": "flag_reason", "aggregation": "count"}])

        assert row["total_visits"] == 4
        assert row["held"] == 1

    def test_a_falsy_json_scalar_is_present_because_it_is_a_value(self, db):
        """Where emptiness stops. A zero-valued scalar vanishing from a count
        would be the same silent wrongness pointing the other way."""
        _seed(41004, [0, False, {}, {}])
        (row,) = _run(41004, [{"name": "held", "path": "flag_reason", "aggregation": "count"}])

        assert row["total_visits"] == 4
        assert row["held"] == 2

    def test_a_populated_object_is_present(self, db):
        """The production shape of `flag_reason`: `{"flags": [[code, msg], …]}`."""
        _seed(41005, [{"flags": [["duplicate_identifiers", "dup"]]}, {}, {}])
        (row,) = _run(41005, [{"name": "held", "path": "flag_reason", "aggregation": "count"}])

        assert row["held"] == 1

    def test_the_value_read_back_is_the_logical_one_not_the_quoted_one(self, db):
        """`list`/`first`/`mode` over the column hand render code the value a
        reader expects, without the JSON quotes a `::text` cast left on."""
        _seed(41006, ["response_pattern_outlier", {}])
        (row,) = _run(41006, [{"name": "held", "path": "flag_reason", "aggregation": "first"}])

        assert row["held"] == "response_pattern_outlier"


@pytest.mark.django_db
class TestFilterOverAJsonbBaseColumn:
    def test_the_filter_control_matches_the_value_the_author_wrote(self, db):
        """`response_pattern_outlier`, spelled as a schema author writes it —
        not the JSON-quoted serialization."""
        _seed(41010, _measured_population())
        (row,) = _run(
            41010,
            [
                {
                    "name": "outlier_holds",
                    "path": "flag_reason",
                    "aggregation": "count",
                    "filter_path": "flag_reason",
                    "filter_value": "response_pattern_outlier",
                }
            ],
        )

        assert row["outlier_holds"] == OUTLIER_VISITS

    def test_the_json_quoted_spelling_does_not_match(self, db):
        """The workaround people reached for when the plain value returned 0. It
        never worked, and now that the plain value does work it must stay not
        working — two live spellings would make the column's comparison value
        ambiguous."""
        _seed(41011, _measured_population())
        (row,) = _run(
            41011,
            [
                {
                    "name": "outlier_holds",
                    "path": "flag_reason",
                    "aggregation": "count",
                    "filter_path": "flag_reason",
                    "filter_value": '"response_pattern_outlier"',
                }
            ],
        )

        assert row["outlier_holds"] == 0

    def test_the_plural_filter_paths_form_agrees_with_the_singular_one(self, db):
        _seed(41013, _measured_population())
        (row,) = _run(
            41013,
            [
                {
                    "name": "outlier_holds",
                    "path": "flag_reason",
                    "aggregation": "count",
                    "filter_paths": ["flag_reason"],
                    "filter_value": "response_pattern_outlier",
                }
            ],
        )

        assert row["outlier_holds"] == OUTLIER_VISITS

    def test_a_filter_on_a_non_jsonb_base_column_restricts_rows(self, db):
        """The sibling defect the `_entity_stage_filters_where` docstring
        already described: the singular `filter_path` branch resolved every name
        against `form_json`, so `filter_path: "status"` zeroed every row instead
        of filtering them."""
        future = timezone.now() + timezone.timedelta(days=1)
        RawVisitCache.objects.bulk_create(
            [
                RawVisitCache(
                    opportunity_id=41012,
                    pipeline_id=41012,
                    visit_count=4,
                    expires_at=future,
                    visit_id=f"41012-{i}",
                    username="flw_a",
                    visit_date="2026-09-01",
                    status=status,
                    flagged=False,
                    flag_reason={},
                    form_json={"form": {"x": "1"}},
                )
                for i, status in enumerate(["approved", "approved", "pending", "rejected"])
            ]
        )
        (row,) = _run(
            41012,
            [
                {
                    "name": "approved_x",
                    "path": "form.x",
                    "aggregation": "count",
                    "filter_path": "status",
                    "filter_value": "approved",
                }
            ],
        )

        assert row["total_visits"] == 4
        assert row["approved_x"] == 2


class TestTheSqlForEverythingElseIsUnchanged:
    """The regression control, and the one that protects every existing
    dashboard.

    `_paths_to_coalesce_sql` is shared by the value path and the filter path and
    is reached by every field in every pipeline, not just JSONB ones — so any
    change to what it emits for an ordinary form path would move numbers
    everywhere at once. These are pinned literals rather than round-trips: they
    fail if the emitted string changes at all.
    """

    def test_a_form_path_is_byte_identical(self):
        assert _paths_to_coalesce_sql(["form.case.update.muac_cm"]) == (
            "COALESCE(NULLIF(form_json->'form'->'case'->'update'->>'muac_cm', ''))"
        )

    def test_a_multi_path_coalesce_is_byte_identical(self):
        assert _paths_to_coalesce_sql(["form.a", "form.b"]) == (
            "COALESCE(NULLIF(form_json->'form'->>'a', ''), NULLIF(form_json->'form'->>'b', ''))"
        )

    def test_a_non_jsonb_base_column_is_byte_identical(self):
        assert _paths_to_coalesce_sql(["status"]) == "COALESCE(NULLIF(status::text, ''))"
        assert _paths_to_coalesce_sql(["visit_date"]) == "COALESCE(NULLIF(visit_date::text, ''))"
        assert _paths_to_coalesce_sql(["flagged"]) == "COALESCE(NULLIF(flagged::text, ''))"

    def test_a_join_scoped_lookup_is_byte_identical(self):
        """Base-column resolution only ever applied to the default form_json
        column; a `sub.form_json` or joined lookup stays a plain path even when
        the leaf is spelled like a base column."""
        assert _paths_to_coalesce_sql(["flag_reason"], column="sub.form_json") == (
            "COALESCE(NULLIF(sub.form_json->>'flag_reason', ''))"
        )

    def test_a_mixed_coalesce_changes_only_the_jsonb_leg(self):
        sql = _paths_to_coalesce_sql(["form.a", "status", "flag_reason"])
        assert sql.startswith("COALESCE(NULLIF(form_json->'form'->>'a', ''), NULLIF(status::text, ''), ")
        assert "jsonb_typeof(flag_reason)" in sql

    def test_the_whole_aggregation_query_for_a_form_field_is_unchanged(self):
        """Belt and braces: the full emitted query for an ordinary dashboard
        field must not reach any of the new JSONB machinery."""
        sql = build_flw_aggregation_query(
            _config(41020, [{"name": "muac_cm", "path": "form.muac", "aggregation": "avg"}]),
            opportunity_id=41020,
        )
        assert "jsonb_typeof" not in sql
        assert "#>>" not in sql
        assert "COALESCE(NULLIF(form_json->'form'->>'muac', ''))" in sql

    def test_a_form_path_filter_is_byte_identical(self):
        """The singular `filter_path` branch now routes through
        `_filter_path_to_sql`; for a non-base-column path it must still emit the
        bare `form_json->>` lookup it always did, un-wrapped."""
        sql = build_flw_aggregation_query(
            _config(
                41021,
                [
                    {
                        "name": "ebf_visits",
                        "path": "form.x",
                        "aggregation": "count",
                        "filter_path": "form.bf_status",
                        "filter_value": "ebf",
                    }
                ],
            ),
            opportunity_id=41021,
        )
        assert "TRIM(form_json->'form'->>'bf_status') = 'ebf'" in sql
