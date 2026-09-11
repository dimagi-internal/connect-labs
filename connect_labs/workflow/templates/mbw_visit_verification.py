"""MBW Visit Verification — single-table audit of the visit-verification block.

V1 is intentionally a single table, no job handler, no saved runs (action-shaped).

IMPORTANT — this reads from the TEST domain, not opp 765's own production app.
The visit-verification block (GPS/QR/signature/mother-questions/ANC-card/outcome
fields) is not yet deployed to the CommCare app currently linked to opportunity
765. It exists today only in "MBW Deliver (MasterSep)"
(app_id 00b59eb524884abc80c6a272a61cbc23) on the ccc-mbw-experiments-1 domain,
which has no Connect opportunity of its own. The pipeline engine can't reach a
domain with no opportunity through its normal data sources (cchq_forms and
cchq_cases both normally resolve the CommCare domain from the owning
opportunity's own metadata) -- so this uses data_source.domain, an explicit
override added for this (see labs/analysis/config.py::DataSourceConfig.domain,
labs/analysis/backends/sql/cchq_fetcher.py, and
labs/analysis/backends/sql/cchq_cases_fetcher.py).

FOLLOW-UP ONCE PRODUCTION CATCHES UP: once the verification block ships to
opp 765's own production app, re-point the six visit pipelines back to a
single "connect_csv" pipeline against opp 765 directly (the mechanism the MBW
Auditing V5 family already uses to merge all 6 visit forms into one stream) --
simpler and doesn't require the user's CommCare HQ OAuth session to have
standing access to the test domain. The eligible-FLW pipeline can drop its
domain override too, once the commcare-user cases with visit_verification are
tagged in opp 765's own domain.

Six separate visit pipelines, one per visit-type form (cchq_forms fetches one
form_name/xmlns at a time -- unlike connect_csv, which merges across form types
automatically). Today only "ANC Visit " has any real submissions with the
verification block; the other five return empty lists (harmless) until the
block is copied into those forms too -- at that point this template starts
surfacing them with zero code changes, since the row filter (see render code)
is generic on field presence, not hardcoded to form_name="ANC Visit ".

A seventh pipeline (eligible_flws, cchq_cases over case_type="commcare-user")
gates which FLWs' visits are shown, per the visit_verification user-case
property -- Connect's own worker export doesn't expose custom CommCare
user-case properties (traced OpportunityUserDataSerializer in
dimagi/commcare-connect: closed field list), so this can't come from the
`workers` prop.

Other derived columns computed client-side because the pipeline engine's
window_fields can't express expanding-window computations today (only
"lag_haversine" is implemented -- see
labs/analysis/backends/sql/query_builder.py::_window_field_to_sql):
  - visit_number: 1st/2nd/3rd... visit per mother_case_id
  - prior_verification_pass_rate: pass rate of this mother's earlier visits
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Test-domain override (see module docstring — remove once production catches up)
# ---------------------------------------------------------------------------

DOMAIN_OVERRIDE = "ccc-mbw-experiments-1"
APP_ID_OVERRIDE = "00b59eb524884abc80c6a272a61cbc23"

# ---------------------------------------------------------------------------
# Visit pipelines (one per visit-type form)
# ---------------------------------------------------------------------------

# The current app version only ever populates mother_case_id via one of these
# per-next-visit-type "_logic" blocks (whichever next visit is being scheduled)
# -- there is no single canonical "form.mother_case_id" path. Exactly one is
# populated per submission, so a "first" aggregation over this fallback list
# always resolves to the real value. Mirrors the pattern the (removed) V4
# REGISTRATIONS_SCHEMA used for the analogous var_visit_1..6 fallback.
_MOTHER_CASE_ID_PATHS = [
    "form.confirm_visit_information.postnatal_visit_logic.mother_case_id",
    "form.confirm_visit_information.one_week_visit_logic.mother_case_id",
    "form.confirm_visit_information.one_month_visit_logic.mother_case_id",
    "form.confirm_visit_information.three_month_visit_logic.mother_case_id",
    "form.confirm_visit_information.six_month_visit_logic.mother_case_id",
    "form.visit_rescheduling.visit_rescheduling.mother_case_id",
    "form.visit_rescheduling.postnatal_visit_logic.mother_case_id",
]

_VISIT_FIELDS = [
    {"name": "mother_case_id", "paths": _MOTHER_CASE_ID_PATHS, "aggregation": "first"},
    {"name": "form_instance_id", "path": "form.meta.instanceID", "aggregation": "first"},
    {"name": "form_name", "path": "form.@name", "aggregation": "first"},
    {
        "name": "where_is_the_visit_being_conducted",
        "path": "form.visit_location.where_is_the_visit_being_conducted",
        "aggregation": "first",
    },
    {
        "name": "visit_location_has_prev_home_gps",
        "path": "form.gps_verification.location_check.visit_location_has_prev_home_gps",
        "aggregation": "first",
    },
    {
        "name": "visit_location_has_prev_health_facility_gps",
        "path": "form.gps_verification.location_check.visit_location_has_prev_health_facility_gps",
        "aggregation": "first",
    },
    {
        "name": "gps_visit_verification_matches",
        "path": "form.gps_verification.location_check.gps_visit_verification_matches",
        "aggregation": "first",
    },
    {
        "name": "qr_code_visit_verification",
        "path": "form.qr_code_verification.qr_code_visit_verification",
        "aggregation": "first",
    },
    {
        "name": "mother_initial_visit_verification",
        "path": "form.additional_visit_verification_block.mother_initial_visit_verification",
        "aggregation": "first",
    },
    {
        "name": "show_mother_questions",
        "path": "form.additional_visit_verification_block.show_mother_questions",
        "aggregation": "first",
    },
    {
        "name": "mother_questions_visit_verification",
        "path": "form.additional_visit_verification_block.mother_questions_visit_verification",
        "aggregation": "first",
    },
    {
        "name": "capture_anc_card_visit_verification",
        "path": "form.additional_visit_verification_block.capture_anc_card_visit_verification",
        "aggregation": "first",
    },
    {
        "name": "visit_verification_outcome",
        "path": "form.verification_properties.visit_verification_outcome",
        "aggregation": "first",
    },
]

# form_name must match exactly what CommCare HQ's Application Structure API
# returns (get_form_xmlns does an exact string match against the form's name
# dict) -- including the trailing space on "ANC Visit ".
ANC_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "ANC Visit ",
        "domain": DOMAIN_OVERRIDE,
        "app_id": APP_ID_OVERRIDE,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

POST_DELIVERY_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Post delivery visit",
        "domain": DOMAIN_OVERRIDE,
        "app_id": APP_ID_OVERRIDE,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

ONE_WEEK_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "1 Week Visit",
        "domain": DOMAIN_OVERRIDE,
        "app_id": APP_ID_OVERRIDE,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

ONE_MONTH_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "1 Month Visit",
        "domain": DOMAIN_OVERRIDE,
        "app_id": APP_ID_OVERRIDE,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

THREE_MONTH_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "3 Month Visit",
        "domain": DOMAIN_OVERRIDE,
        "app_id": APP_ID_OVERRIDE,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

SIX_MONTH_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "6 Month Visit",
        "domain": DOMAIN_OVERRIDE,
        "app_id": APP_ID_OVERRIDE,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

# ---------------------------------------------------------------------------
# Eligible-FLW pipeline (commcare-user cases, gates which FLWs' visits show)
# ---------------------------------------------------------------------------

ELIGIBLE_FLW_SCHEMA = {
    "data_source": {
        "type": "cchq_cases",
        "case_type": "commcare-user",
        "domain": DOMAIN_OVERRIDE,
    },
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {
            "name": "visit_verification",
            "path": "case.properties.visit_verification",
            "aggregation": "first",
        },
    ],
}

# Alias names are read directly by RENDER_CODE (VISIT_PIPELINE_ALIASES /
# ELIGIBLE_FLW_ALIAS) to merge all six visit pipelines and read the FLW gate --
# keep them in sync.
PIPELINE_SCHEMAS = [
    {
        "alias": "visits_anc_visit",
        "name": "MBW Visit Verification — ANC Visit",
        "description": "ANC Visit form submissions with the visit-verification block (test domain).",
        "schema": ANC_VISIT_SCHEMA,
    },
    {
        "alias": "visits_post_delivery_visit",
        "name": "MBW Visit Verification — Post delivery visit",
        "description": "Post delivery visit form submissions (test domain). Empty until this form gains the block.",
        "schema": POST_DELIVERY_VISIT_SCHEMA,
    },
    {
        "alias": "visits_1_week_visit",
        "name": "MBW Visit Verification — 1 Week Visit",
        "description": "1 Week Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": ONE_WEEK_VISIT_SCHEMA,
    },
    {
        "alias": "visits_1_month_visit",
        "name": "MBW Visit Verification — 1 Month Visit",
        "description": "1 Month Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": ONE_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_3_month_visit",
        "name": "MBW Visit Verification — 3 Month Visit",
        "description": "3 Month Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": THREE_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_6_month_visit",
        "name": "MBW Visit Verification — 6 Month Visit",
        "description": "6 Month Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": SIX_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "eligible_flws",
        "name": "MBW Visit Verification — Eligible FLWs",
        "description": "commcare-user cases with the visit_verification property (test domain).",
        "schema": ELIGIBLE_FLW_SCHEMA,
    },
]

# ---------------------------------------------------------------------------
# Definition
# ---------------------------------------------------------------------------

DEFINITION = {
    "name": "MBW Visit Verification",
    "description": (
        "Single-table audit of GPS, QR, signature, ANC-card, and mother-questions "
        "verification outcomes per visit, for FLWs flagged for visit verification. "
        "Sources from the ccc-mbw-experiments-1 test domain until the verification "
        "block ships to opp 765's own production app — see module docstring."
    ),
    "version": 1,
    "templateType": "mbw_visit_verification",
    "statuses": [
        {"id": "active", "label": "Active", "color": "blue"},
    ],
    "config": {
        # Both the visit pipelines (cchq_forms) and the eligible-FLW pipeline
        # (cchq_cases) require the user's CommCare HQ OAuth session.
        "auth_requires": ["connect", "commcare_hq"],
        "showSummaryCards": False,
        "showFilters": True,
    },
    "pipeline_sources": [],
}

RENDER_CODE = (Path(__file__).parent / "mbw_visit_verification_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "mbw_visit_verification",
    "name": "MBW Visit Verification",
    "description": "Single-table visit-verification audit (GPS/QR/signature/ANC-card/mother-questions outcomes).",
    "icon": "fa-table",
    "color": "purple",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
