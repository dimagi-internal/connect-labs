"""Smoke tests for the mbw_monitoring_v3 template.

Verifies:
- the template is auto-discovered and registered
- its declared schema fields validate against FieldComputation
- the SQL builder accepts every aggregation/filter combination it declares
  (no silent ValueError at runtime when a real workflow tries to run it)

This isn't a parity test; it's a "the template will at least load" test.
"""

import pytest

from commcare_connect.labs.analysis.backends.sql.query_builder import _aggregation_to_sql, _jsonb_path_to_sql
from commcare_connect.labs.analysis.config import FieldComputation


class TestV3TemplateLoads:
    def test_registered(self):
        from commcare_connect.workflow.templates import TEMPLATES

        assert "mbw_monitoring_v3" in TEMPLATES, sorted(TEMPLATES.keys())

    def test_template_dict_shape(self):
        from commcare_connect.workflow.templates.mbw_monitoring_v3 import TEMPLATE

        assert TEMPLATE["key"] == "mbw_monitoring_v3"
        assert TEMPLATE["definition"]["templateType"] == "mbw_monitoring_v3"
        # No job_type — v3's whole point is that the pipeline is the job.
        assert "job_type" not in TEMPLATE["definition"]["config"]
        assert isinstance(TEMPLATE["render_code"], str) and len(TEMPLATE["render_code"]) > 50
        # 4 pipelines: visits (aggregated, FLW summaries), visits_gps (visit-level
        # with lag_haversine window), registrations + gs_forms.
        assert len(TEMPLATE["pipeline_schemas"]) == 4
        assert {p["alias"] for p in TEMPLATE["pipeline_schemas"]} == {
            "visits",
            "visits_gps",
            "registrations",
            "gs_forms",
        }

    def test_every_visits_field_validates_as_field_computation(self):
        """If any field in the v3 visits schema fails validation, the
        pipeline can't run for that opp. Catch it at template-load time."""
        from commcare_connect.workflow.templates.mbw_monitoring_v3 import VISITS_SCHEMA

        for field_dict in VISITS_SCHEMA["fields"]:
            # Translate raw schema field-dict to a FieldComputation. The
            # framework normally does this in get_pipeline_data but we want
            # the validation to fire here.
            kwargs = {k: v for k, v in field_dict.items() if k not in ("paths",)}
            if "paths" in field_dict:
                kwargs["paths"] = field_dict["paths"]
            FieldComputation(**kwargs)  # raises if invalid

    def test_every_aggregation_emits_legal_sql(self):
        """Every aggregation declared in v3 must round-trip through the SQL
        builder without raising ValueError. This catches typos and any
        future regression where a v3 schema declares an aggregation the
        backend doesn't support.
        """
        from commcare_connect.workflow.templates.mbw_monitoring_v3 import (
            GS_FORMS_SCHEMA,
            REGISTRATIONS_SCHEMA,
            VISITS_GPS_SCHEMA,
            VISITS_SCHEMA,
        )

        for schema in (VISITS_SCHEMA, VISITS_GPS_SCHEMA, REGISTRATIONS_SCHEMA, GS_FORMS_SCHEMA):
            for field_dict in schema["fields"]:
                # Synthesize a value_expr of the right shape for the SQL
                # builder. The real pipeline does this; here we just need
                # SOMETHING legal so _aggregation_to_sql can emit SQL.
                if "paths" in field_dict:
                    value_expr = _jsonb_path_to_sql(field_dict["paths"][0])
                else:
                    value_expr = _jsonb_path_to_sql(field_dict["path"])
                sql = _aggregation_to_sql(
                    field_dict["aggregation"],
                    value_expr,
                    field_dict["name"],
                    filter_path=field_dict.get("filter_path", ""),
                    filter_value=field_dict.get("filter_value", ""),
                    filter_op=field_dict.get("filter_op", "eq"),
                )
                assert sql, f"empty SQL for {field_dict['name']}"


class TestSchemaToConfigWiring:
    """End-to-end: JSON schema dict → AnalysisPipelineConfig → SQL.

    This is the path the live workflow runner uses. Catches regressions
    where a new FieldComputation parameter is added but not threaded through
    PipelineDataAccess._schema_to_config — the symptom would be silent loss
    of filter_op / pre_aggregate_by / window_fields between template
    declaration and runtime execution.
    """

    def test_v3_schemas_round_trip_to_config(self):
        """Every v3 PIPELINE_SCHEMA must construct a valid AnalysisPipelineConfig
        without crashing. Catches schema-shape regressions early — if a field
        type or window_op declared in v3 doesn't survive parsing, this fails.
        """
        from commcare_connect.workflow.data_access import PipelineDataAccess
        from commcare_connect.workflow.templates.mbw_monitoring_v3 import (
            GS_FORMS_SCHEMA,
            REGISTRATIONS_SCHEMA,
            VISITS_GPS_SCHEMA,
            VISITS_SCHEMA,
        )

        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        for schema in (VISITS_SCHEMA, VISITS_GPS_SCHEMA, REGISTRATIONS_SCHEMA, GS_FORMS_SCHEMA):
            config = access._schema_to_config(schema, definition_id=0)
            assert config is not None
            assert len(config.fields) > 0

    def test_filter_op_threads_through_schema_parsing(self):
        """A field declared with filter_op="contains_word" must keep that
        attribute on the resulting FieldComputation. Without the parsing
        threading, contains_word silently degrades to "eq" — a regression
        that wouldn't fail any aggregation test but would corrupt EBF
        counting for every v3 dashboard.
        """
        from commcare_connect.workflow.data_access import PipelineDataAccess

        schema = {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [
                {
                    "name": "ebf_count",
                    "path": "form.feeding_history.pnc_current_bf_status",
                    "aggregation": "count",
                    "filter_path": "form.feeding_history.pnc_current_bf_status",
                    "filter_value": "ebf",
                    "filter_op": "contains_word",
                },
            ],
        }
        # Construct a fake instance that has just the method we need.
        # (PipelineDataAccess.__init__ requires DB plumbing we don't want here.)
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        config = access._schema_to_config(schema, definition_id=42)
        assert config.fields[0].filter_op == "contains_word"
        assert config.fields[0].filter_value == "ebf"

    def test_pre_aggregate_by_threads_through_schema_parsing(self):
        """A field declared with pre_aggregate_by must keep that attribute."""
        from commcare_connect.workflow.data_access import PipelineDataAccess

        schema = {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "aggregated",
            "fields": [
                {
                    "name": "parity_mode_share",
                    "path": "form.parity",
                    "aggregation": "mode_share",
                    "pre_aggregate_by": "form.parents.parent.case.@case_id",
                    "pre_aggregation": "last",
                },
            ],
        }
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        config = access._schema_to_config(schema, definition_id=42)
        assert config.fields[0].pre_aggregate_by == "form.parents.parent.case.@case_id"
        assert config.fields[0].pre_aggregation == "last"

    def test_window_fields_thread_through_schema_parsing(self):
        """A schema with window_fields must produce a config with the
        WindowFieldComputation list populated."""
        from commcare_connect.workflow.data_access import PipelineDataAccess

        schema = {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [
                {"name": "latitude", "path": "form.lat", "aggregation": "first"},
                {"name": "longitude", "path": "form.lon", "aggregation": "first"},
                {"name": "mother_case_id", "path": "form.case", "aggregation": "first"},
                {"name": "visit_datetime", "path": "form.timeEnd", "aggregation": "first"},
            ],
            "window_fields": [
                {
                    "name": "distance_from_prev_case_visit_m",
                    "operation": "lag_haversine",
                    "partition_by": "mother_case_id",
                    "order_by": "visit_datetime",
                    "lat_field": "latitude",
                    "lon_field": "longitude",
                },
            ],
        }
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        config = access._schema_to_config(schema, definition_id=42)
        assert len(config.window_fields) == 1
        wf = config.window_fields[0]
        assert wf.name == "distance_from_prev_case_visit_m"
        assert wf.operation == "lag_haversine"
        assert wf.partition_by == "mother_case_id"
        assert wf.lat_field == "latitude"

    def test_gps_lat_transform_resolves(self):
        """gps_lat / gps_lon transforms exist in the registry and parse the
        packed 'lat lon altitude accuracy' string format."""
        from commcare_connect.workflow.data_access import PipelineDataAccess

        schema = {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [
                {"name": "latitude", "path": "form.meta.location", "aggregation": "first", "transform": "gps_lat"},
                {"name": "longitude", "path": "form.meta.location", "aggregation": "first", "transform": "gps_lon"},
            ],
        }
        access = type("_Fake", (PipelineDataAccess,), {"__init__": lambda self: None})()
        config = access._schema_to_config(schema, definition_id=42)
        # Transform is a callable (lambda). Apply it to a packed GPS string
        # to verify the runtime parsing.
        lat_fn = config.fields[0].transform
        lon_fn = config.fields[1].transform
        assert lat_fn("-1.2345 35.6789 1000 10") == pytest.approx(-1.2345)
        assert lon_fn("-1.2345 35.6789 1000 10") == pytest.approx(35.6789)
        # Empty / malformed → None (no crash on real-world bad data)
        assert lat_fn("") is None
        assert lat_fn(None) is None
        assert lat_fn("notanumber") is None


class TestFilterOpValidation:
    def test_unknown_filter_op_rejected_at_field_construction(self):
        with pytest.raises(ValueError, match="Invalid filter_op"):
            FieldComputation(
                name="x",
                path="form.x",
                aggregation="count",
                filter_path="form.x",
                filter_value="y",
                filter_op="not_a_real_op",
            )

    def test_unknown_filter_op_rejected_at_sql_build(self):
        """Defense in depth: even if a field is constructed via dict spreading
        that bypasses __post_init__, the SQL builder fails loudly."""
        with pytest.raises(ValueError, match="Unknown filter_op"):
            _aggregation_to_sql(
                "count",
                "v",
                "f",
                filter_path="x",
                filter_value="y",
                filter_op="not_a_real_op",
            )
