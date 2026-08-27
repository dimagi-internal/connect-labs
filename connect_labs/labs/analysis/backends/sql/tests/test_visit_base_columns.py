"""#1198: the visit-level SELECT, the shadow check and the window-reference
check must all describe the same set of columns — and the set must include the
ones a review dashboard actually needs.

Three lists used to disagree. `review_status` was validatable but never
selected; `visit_datetime` was validatable and is not a column at all; and
`flag_reason` and `date_created` were stored on every row and reachable from
nowhere, so a dashboard could show *that* a visit was flagged but never *why*.
"""

import pytest
from django.db import connection
from django.utils import timezone

from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
from connect_labs.labs.analysis.backends.sql.query_builder import build_visit_extraction_query
from connect_labs.labs.analysis.config import (
    _BASE_VISIT_COLUMNS,
    RAW_VISIT_BASE_COLUMNS,
    VISIT_PASSTHROUGH_COLUMNS,
    VISIT_SELECT_COLUMNS,
)


class TestTheListsCannotDriftApart:
    def test_the_window_list_is_the_base_list(self):
        """Not equal — the same object. Equality would pass the day someone
        restates it, which is precisely how the drift happened."""
        assert _BASE_VISIT_COLUMNS is RAW_VISIT_BASE_COLUMNS

    def test_every_selected_column_is_a_known_base_column(self):
        assert set(VISIT_SELECT_COLUMNS) <= RAW_VISIT_BASE_COLUMNS

    def test_every_base_column_is_a_real_column_on_the_model(self):
        """`visit_datetime` was in the window list and has never existed on
        `labs_raw_visit_cache`, so a window field could legally partition by it.
        Deriving the assertion from the model is what stops that recurring."""
        real = {f.column for f in RawVisitCache._meta.concrete_fields}
        assert RAW_VISIT_BASE_COLUMNS <= real, sorted(RAW_VISIT_BASE_COLUMNS - real)

    def test_visit_datetime_is_gone(self):
        assert "visit_datetime" not in RAW_VISIT_BASE_COLUMNS

    def test_review_status_is_selectable_now_that_it_validates(self):
        """It was in the shadow-collision list — so a config field couldn't be
        named it — while never being selected. Validatable and unreachable is
        the worst of both."""
        assert "review_status" in RAW_VISIT_BASE_COLUMNS
        assert "review_status" in VISIT_SELECT_COLUMNS

    def test_the_three_reported_columns_are_selected(self):
        for col in ("flag_reason", "date_created", "review_status"):
            assert col in VISIT_SELECT_COLUMNS

    def test_passthrough_columns_are_selected_and_have_no_visit_row_attribute(self):
        """They ride in `computed` precisely because VisitRow has nowhere to put
        them. If VisitRow ever grows one of these, this test should be updated
        on purpose rather than silently double-carrying the value."""
        from connect_labs.labs.analysis.models import VisitRow

        row = VisitRow(id="v", username="u")
        for col in VISIT_PASSTHROUGH_COLUMNS:
            assert col in VISIT_SELECT_COLUMNS
            assert not hasattr(row, col)


class TestTheSelectEmitsThem:
    def _config(self, opp_id):
        from connect_labs.workflow.data_access import PipelineDataAccess

        schema = {
            "terminal_stage": "visit_level",
            "data_source": {"type": "connect_export", "endpoint": "user_visits"},
            "fields": [{"name": "muac_cm", "path": "form.muac", "aggregation": "first"}],
        }
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        return access._schema_to_config(schema, definition_id=opp_id)

    def test_sql_names_the_previously_unreachable_columns(self):
        sql, _ = build_visit_extraction_query(self._config(30001), opportunity_id=30001)
        assert "flag_reason" in sql
        assert "review_status" in sql
        assert "date_created" in sql

    def test_date_created_is_cast_to_text_not_selected_raw(self):
        """It lands in a JSONField written straight from the cursor row, so a
        `datetime` there fails to serialize."""
        sql, _ = build_visit_extraction_query(self._config(30002), opportunity_id=30002)
        assert "to_char(date_created" in sql


@pytest.mark.django_db
class TestEndToEndAgainstPostgres:
    def _seed(self, opp_id):
        RawVisitCache.objects.create(
            opportunity_id=opp_id,
            pipeline_id=opp_id,
            visit_count=1,
            expires_at=timezone.now() + timezone.timedelta(days=1),
            visit_id="v1",
            username="flw_a",
            visit_date="2026-08-01",
            status="approved",
            flagged=True,
            flag_reason={"reason": "duplicate image", "code": "DUP"},
            review_status="pending",
            date_created="2026-08-03T14:30:00+00:00",
            form_json={"form": {"muac": "11.4"}},
        )

    def _config(self, opp_id):
        from connect_labs.workflow.data_access import PipelineDataAccess

        schema = {
            "terminal_stage": "visit_level",
            "data_source": {"type": "connect_export", "endpoint": "user_visits"},
            "fields": [{"name": "muac_cm", "path": "form.muac", "aggregation": "first"}],
        }
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        return access._schema_to_config(schema, definition_id=opp_id)

    def _run(self, opp_id):
        sql, _ = build_visit_extraction_query(self._config(opp_id), opportunity_id=opp_id)
        with connection.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]

    def test_the_flag_reason_reaches_the_row(self, db):
        """The concrete cost in the report: a dashboard could show a record was
        flagged but not why.

        At the SQL layer a raw cursor hands JSONB back as text; the payload gets
        the decoded object — see test_flag_reason_is_decoded_for_consumers.
        """
        import json as _json

        self._seed(30010)
        (row,) = self._run(30010)
        assert row["flagged"] is True
        assert _json.loads(row["flag_reason"]) == {"reason": "duplicate image", "code": "DUP"}

    def test_flag_reason_is_decoded_for_consumers(self, db):
        """The fresh path reads a raw cursor (JSON text) and the cached path
        reads a JSONField (decoded object). Handing render code two different
        types for one field is the ace#1657 shape one field over, so the
        passthrough helper normalizes it."""
        from connect_labs.labs.analysis.backends.sql.backend import _with_passthrough_columns

        self._seed(30015)
        (row,) = self._run(30015)
        computed = _with_passthrough_columns(row, {})

        assert computed["flag_reason"] == {"reason": "duplicate image", "code": "DUP"}
        assert computed["review_status"] == "pending"
        assert computed["date_created"].startswith("2026-08-03T14:30:00")

    def test_a_non_json_flag_reason_is_passed_through_not_dropped(self, db):
        from connect_labs.labs.analysis.backends.sql.backend import _with_passthrough_columns

        computed = _with_passthrough_columns({"flag_reason": "just a sentence"}, {})
        assert computed["flag_reason"] == "just a sentence"

    def test_the_whole_computed_dict_is_json_serializable(self, db):
        """It is written straight into a JSONField, so anything that isn't
        serializable fails the cache write rather than the read."""
        import json as _json

        from connect_labs.labs.analysis.backends.sql.backend import _with_passthrough_columns

        self._seed(30016)
        (row,) = self._run(30016)
        _json.dumps(_with_passthrough_columns(row, {"muac_cm": row.get("muac_cm")}))

    def test_submission_time_is_separable_from_meeting_date(self, db):
        """`visit_date` is when the meeting happened; `date_created` is when it
        was submitted. A timeliness check needs both, and only one was visible."""
        self._seed(30011)
        (row,) = self._run(30011)
        assert str(row["visit_date"]) == "2026-08-01"
        assert row["date_created"].startswith("2026-08-03T14:30:00")

    def test_date_created_comes_back_json_serializable(self, db):
        """A datetime here would blow up the ComputedVisitCache JSONField write."""
        import json

        self._seed(30012)
        (row,) = self._run(30012)
        json.dumps({"date_created": row["date_created"], "flag_reason": row["flag_reason"]})

    def test_review_status_reaches_the_row(self, db):
        self._seed(30013)
        (row,) = self._run(30013)
        assert row["review_status"] == "pending"

    def test_a_field_path_may_name_a_base_column(self, db):
        """Every path used to compile to `form_json->'...'` unconditionally, so a
        field named after a base column silently extracted NULL — even though
        `linking_field` had resolved base columns for some time."""
        self._seed(30014)
        from connect_labs.workflow.data_access import PipelineDataAccess

        schema = {
            "terminal_stage": "visit_level",
            "data_source": {"type": "connect_export", "endpoint": "user_visits"},
            "fields": [{"name": "why_flagged", "path": "flag_reason", "aggregation": "first"}],
        }
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        config = access._schema_to_config(schema, definition_id=30014)
        sql, _ = build_visit_extraction_query(config, opportunity_id=30014)
        with connection.cursor() as cur:
            cur.execute(sql)
            cols = [c[0] for c in cur.description]
            (row,) = (dict(zip(cols, r)) for r in cur.fetchall())

        assert row["why_flagged"] is not None, "a base-column path must not extract NULL"
        assert "duplicate image" in row["why_flagged"]
