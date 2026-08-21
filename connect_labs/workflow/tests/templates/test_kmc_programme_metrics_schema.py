"""The KMC programme-metrics template's PIPELINE schemas must carry the entity-key fields.

A workflow created from this template gets its pipelines from ``pipeline_schemas`` here,
NOT from whatever the hand-tuned synthetic pipelines happen to hold. When the two drift,
every workflow created from the template is silently wrong on real data — which is exactly
what happened: the template was resynced for its render_code only, so a PROD workflow came
up missing ``baby_case_id`` and ``form_names`` and would have scattered each baby across
one row per visit (connect-labs#1224).

These assert the invariant rather than byte-equality with a live pipeline, so they run in
CI with no network.
"""

from connect_labs.workflow.templates.kmc_programme_metrics import (
    CASE_PROPERTIES_SCHEMA,
    TEMPLATE,
    WEIGHT_SERIES_SCHEMA,
)


def _field_names(schema):
    return [f["name"] for f in schema["fields"]]


def test_both_pipelines_carry_baby_case_id():
    """entity_id is per-VISIT on real Connect data; baby_case_id is what groups a baby."""
    for schema in (CASE_PROPERTIES_SCHEMA, WEIGHT_SERIES_SCHEMA):
        assert "baby_case_id" in _field_names(schema)
        field = next(f for f in schema["fields"] if f["name"] == "baby_case_id")
        # form.case.@case_id first, entity_id only as the fallback for sources
        # with no case block (synthetic clones).
        assert field["paths"] == ["form.case.@case_id", "entity_id"]


def test_case_properties_carries_form_names():
    """Without form_names, REGISTERED and STARTED collapse and C01/C02/C05 are identical."""
    assert "form_names" in _field_names(CASE_PROPERTIES_SCHEMA)


def test_terminal_stages_are_what_the_render_expects():
    assert CASE_PROPERTIES_SCHEMA["terminal_stage"] == "entity"
    assert WEIGHT_SERIES_SCHEMA["terminal_stage"] == "visit_level"


def test_template_exposes_both_pipelines_under_the_aliases_the_render_reads():
    aliases = {p["alias"]: p["schema"] for p in TEMPLATE["pipeline_schemas"]}
    assert aliases["children"] is CASE_PROPERTIES_SCHEMA
    assert aliases["visits"] is WEIGHT_SERIES_SCHEMA
