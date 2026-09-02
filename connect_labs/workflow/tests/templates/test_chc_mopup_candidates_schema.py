"""Schema invariants for the CHC Mop-up Candidate Analysis template.

The render code (`chc_mopup_candidates_render.js`) depends on very specific
choices in `VISIT_QUALITY_SCHEMA` that are easy to silently break on a future
edit -- `terminal_stage="entity"` (not "aggregated": see the module docstring
in `chc_mopup_candidates.py` for why `grouping_key="wa_case_id"` +
`terminal_stage="aggregated"` would silently group by FLW username instead of
work area), the approved-only + form-name-filtered fields that make
`hsd_visit_count`/`ncf_visit_count`/`inaccessible_visit_count` safe to use for
the EVC-shortfall and NCF/inaccessible mop-up criteria, and the age-months
histogram shape the client-side age-heaping (Whipple index) calculation
depends on. These assert the invariants rather than byte-equality with a live
pipeline, so they run in CI with no network -- same convention as
`test_kmc_programme_metrics_schema.py`.
"""

from connect_labs.workflow.templates.chc_mopup_candidates import PIPELINE_SCHEMAS, TEMPLATE, VISIT_QUALITY_SCHEMA


def _field_names(schema):
    return [f["name"] for f in schema["fields"]]


def _field(schema, name):
    return next(f for f in schema["fields"] if f["name"] == name)


def test_terminal_stage_is_entity_not_aggregated():
    """`aggregated` is hardcoded elsewhere to GROUP BY username -- entity stage
    with linking_field="wa_case_id" is the only way to get one row per WA."""
    assert VISIT_QUALITY_SCHEMA["terminal_stage"] == "entity"
    assert VISIT_QUALITY_SCHEMA["linking_field"] == "wa_case_id"


def test_approved_only_filter_present():
    assert VISIT_QUALITY_SCHEMA["filters"] == {"status": ["approved"]}


def test_form_name_fields_are_the_three_confirmed_deliver_unit_forms():
    """Each of hsd/ncf/inaccessible counts must be gated on the real,
    confirmed CommCare form.@name -- not a guess. See chc_mopup_candidates.py's
    module docstring for how each string was confirmed (flw_daily_summary_compute.py
    for "No Children Found", templates/labs/docs/chc_content.html for "Inaccessible
    WA", plus arithmetic reconciliation against total_visits via pipeline_preview)."""
    expected = {
        "hsd_visit_count": "Health Service Delivery",
        "ncf_visit_count": "No Children Found",
        "inaccessible_visit_count": "Inaccessible WA",
    }
    for field_name, form_name in expected.items():
        field = _field(VISIT_QUALITY_SCHEMA, field_name)
        assert field["path"] == "form.@name"
        assert field["filter_path"] == "form.@name"
        assert field["filter_value"] == form_name
        assert field["aggregation"] == "count"


def test_dq_numerator_denominator_fields_present():
    """Every metric needs BOTH sides exposed as counts (never a bare rate) --
    the render code computes rates client-side, gated by a configurable
    minimum-N floor."""
    names = _field_names(VISIT_QUALITY_SCHEMA)
    for expected in (
        "wa_case_id",
        "hsd_visit_count",
        "ncf_visit_count",
        "inaccessible_visit_count",
        "deworming_given_count",
        "muac_recorded_count",
        "vaccination_given_count",
        "gender_recorded_count",
        "gender_male_count",
        "gender_female_count",
    ):
        assert expected in names, f"missing field {expected!r}"


def test_age_months_histogram_shape_matches_render_code_bucket_naming():
    """The render code's ageMonthBucketField(m) builds field names as
    'age_months_<m>_0_<m+1>_0_visits' -- this depends on num_bins=60 over a
    0-60 range (1-month-wide bins)."""
    histograms = {h["name"]: h for h in VISIT_QUALITY_SCHEMA["histograms"]}
    age = histograms["age_months"]
    assert age["lower_bound"] == 0
    assert age["upper_bound"] == 60
    assert age["num_bins"] == 60
    assert age["bin_name_prefix"] == "age_months"


def test_wa_case_id_is_the_linking_field_and_coalesces_ncf_form_shape():
    """HSD visits carry the WA case id nested under work_area_info; the No
    Children Found form stores it at a different top-level path -- both must
    be tried so NCF-only work areas still link correctly."""
    field = _field(VISIT_QUALITY_SCHEMA, "wa_case_id")
    assert field["paths"] == ["form.work_area_info.wa_caseid", "form.wa_case_id"]


def test_template_registers_visit_quality_under_the_alias_the_render_reads():
    aliases = {p["alias"]: p["schema"] for p in PIPELINE_SCHEMAS}
    assert aliases["visit_quality"] is VISIT_QUALITY_SCHEMA
    assert TEMPLATE["pipeline_schemas"] is PIPELINE_SCHEMAS


def test_template_is_multi_opp_and_does_not_declare_pipeline_sources_it_does_not_own():
    """work_areas (12965) / wa_geometry (12971) / audit_entries (13013) are
    pre-existing, already-live pipelines attached via workflow_add_pipeline_source
    at creation time -- this template must not redeclare their schemas."""
    assert TEMPLATE["multi_opp"] is True
    aliases = {p["alias"] for p in PIPELINE_SCHEMAS}
    assert aliases == {"visit_quality"}
    assert TEMPLATE["definition"]["pipeline_sources"] == []


def test_render_code_is_loaded_from_the_sidecar_js_file():
    """Matches the chc_audit_history.py convention: a .py template file + a
    _render.js sidecar, not an inline giant Python string."""
    render_code = TEMPLATE["render_code"]
    assert render_code, "RENDER_CODE must not be empty"
    assert "function WorkflowUI(props)" in render_code
    assert "var ce = React.createElement" in render_code
