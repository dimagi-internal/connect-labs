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
        assert len(TEMPLATE["pipeline_schemas"]) == 3
        assert {p["alias"] for p in TEMPLATE["pipeline_schemas"]} == {"visits", "registrations", "gs_forms"}

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
            VISITS_SCHEMA,
        )

        for schema in (VISITS_SCHEMA, REGISTRATIONS_SCHEMA, GS_FORMS_SCHEMA):
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
