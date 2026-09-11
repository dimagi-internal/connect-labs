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
        # entity_id only as the last resort, for sources with no case block
        # (synthetic clones). The form-dependent ordering before it is pinned in
        # test_the_baby_key_is_conditional_on_the_registration_design.
        assert field["paths"][-1] == "entity_id"


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


def test_the_baby_key_is_conditional_on_the_registration_design():
    """Neal's section 1 keys the baby by a FORM-dependent path. Pinned because every
    single-list ordering was tried against production on 2026-09-11 and each split
    one design: design A's registration carries a subcase that is not the baby,
    design B's carries a form.case that is the mother. See BABY_CASE_ID_FIELD."""
    from connect_labs.workflow.templates.kmc_programme_metrics import (
        BABY_CASE_ID_FIELD,
        CASE_PROPERTIES_SCHEMA,
        WEIGHT_SERIES_SCHEMA,
    )

    for schema in (CASE_PROPERTIES_SCHEMA, WEIGHT_SERIES_SCHEMA):
        [field] = [f for f in schema["fields"] if f["name"] == "baby_case_id"]
        assert field is BABY_CASE_ID_FIELD, "both pipelines must key the baby identically"

    paths = BABY_CASE_ID_FIELD["paths"]
    # Visit ids first: a visit carries a fresh subcase_0 and, on design B, the mother's form.case.
    assert paths[:2] == ["form.kmc_beneficiary_case_id", "form.child_case_id"]
    assert paths.index("form.case.@case_id") < paths.index("entity_id") == len(paths) - 1
    assert "form.subcase_0.case.@case_id" not in paths, "subcase_0 is the baby only on design B's registration"

    [cond] = BABY_CASE_ID_FIELD["conditional_paths"]
    assert cond == {
        "when_path": "form.@name",
        "when_value": "Child Registration Form",
        "paths": ["form.subcase_0.case.@case_id"],
    }


def test_the_baby_key_builds_as_a_field_computation():
    # The declaration has to survive the real schema parser, not only this module.
    from connect_labs.workflow.data_access import PipelineDataAccess
    from connect_labs.workflow.templates.kmc_programme_metrics import CASE_PROPERTIES_SCHEMA

    config = PipelineDataAccess.__new__(PipelineDataAccess)._schema_to_config(CASE_PROPERTIES_SCHEMA, 1)
    field = next(f for f in config.fields if f.name == "baby_case_id")
    assert field.conditional_entries() == [
        ("form.@name", ["Child Registration Form"], ["form.subcase_0.case.@case_id"])
    ]


def test_both_pipelines_keep_only_valid_visits():
    """Neal's rule 0: approved and over_limit only. On BOTH pipelines, so the case
    index and the indicators count the same visits."""
    from connect_labs.workflow.templates.kmc_programme_metrics import VALID_VISIT_FILTER

    assert VALID_VISIT_FILTER == {"status": ["approved", "over_limit"]}
    for schema in (CASE_PROPERTIES_SCHEMA, WEIGHT_SERIES_SCHEMA):
        assert schema["filters"] is VALID_VISIT_FILTER


def test_the_case_pipeline_groups_by_the_baby_key():
    """One row per BABY, keyed exactly as the indicators and the weight series are.

    Grouping by Connect's entity_id gave 9,183 rows for the indicators' 8,823 babies
    (2026-09-11), and the drill -- which joins a case to its weight series on the
    baby key -- lost the series of every case whose entity_id differed.
    """
    assert CASE_PROPERTIES_SCHEMA["linking_field"] == "baby_case_id"
    assert "baby_case_id" in _field_names(CASE_PROPERTIES_SCHEMA)
