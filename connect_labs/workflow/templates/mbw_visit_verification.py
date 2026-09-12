"""MBW Visit Verification — single-table audit of the visit-verification block.

V1 is intentionally a single table, no job handler, no saved runs (action-shaped).

Reads from BOTH domains at once: the test domain (ccc-mbw-experiments-1,
app 00b59eb524884abc80c6a272a61cbc23 -- "MBW Deliver (MasterSep)"), where the
visit-verification block already lives, and opp 765's real production domain
(ccc-mbw-production, app 08cccdc1cdf58efea4a8b898587530b6). Production doesn't
have the block yet as of this writing -- those pipelines just return empty
rows until it ships -- but this way the report starts surfacing real
production visits automatically the moment it does, with no code change and
no one needing to be online to flip a switch.

Test-domain pipelines use data_source.domain, an explicit override added for
this (see labs/analysis/config.py::DataSourceConfig.domain,
labs/analysis/backends/sql/cchq_fetcher.py, and
labs/analysis/backends/sql/cchq_cases_fetcher.py) -- needed because that
domain has no Connect opportunity of its own, so the normal
app_id_source="opportunity" resolution can't reach it. Production-domain
pipelines use the normal, standard resolution (app_id_source="opportunity"):
no override needed there since ccc-mbw-production genuinely is opp 765's own
linked domain -- this is also more robust than hardcoding its app_id, since
it re-resolves automatically if the app is ever rebuilt under a new id.

Twelve visit pipelines (one per visit-type form, per domain -- cchq_forms
fetches one form_name/xmlns at a time, unlike connect_csv, which merges across
form types automatically) plus two eligible-FLW pipelines (cchq_cases over
case_type="commcare-user", one per domain), merged in the render code. Today
only the test domain's "ANC Visit " form has any real submissions with the
verification block; every other pipeline (both domains) returns empty lists
harmlessly until their form/domain combination gains the block too -- at that
point this template starts surfacing them with zero code changes, since the
row filter (see render code) is generic on field presence, not hardcoded to
any specific form_name or domain.

Other derived columns computed client-side because the pipeline engine's
window_fields can't express expanding-window computations today (only
"lag_haversine" is implemented -- see
labs/analysis/backends/sql/query_builder.py::_window_field_to_sql):
  - visit_number: 1st/2nd/3rd... visit per mother_case_id
  - prior_verification_pass_rate: pass rate of this mother's earlier visits
"""

from pathlib import Path

# ---------------------------------------------------------------------------
# Test-domain override (see module docstring)
# ---------------------------------------------------------------------------

TEST_DOMAIN = "ccc-mbw-experiments-1"
TEST_APP_ID = "00b59eb524884abc80c6a272a61cbc23"

# ---------------------------------------------------------------------------
# Visit pipelines (one per visit-type form, per domain)
# ---------------------------------------------------------------------------

# form.parents.parent.case.@case_id is the visit case's own parent-case
# index -- verified present on 100/100 real ANC Visit submissions (scanned
# via CommCare HQ's Form API directly). The "_logic" sub-block paths below
# were the ORIGINAL (wrong) primary source: they only populate when a
# next-visit-scheduling branch happens to fire, which produced blank
# mother_case_id for plenty of real rows (e.g. the last scheduled visit,
# where there's no next visit to schedule). Kept as trailing fallbacks in
# case some other visit-type form's parent-index differs.
_MOTHER_CASE_ID_PATHS = [
    "form.parents.parent.case.@case_id",
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
    # Built-in visit_date is date-only (always midnight) -- this carries the
    # real submission time-of-day for display.
    {"name": "visit_datetime", "path": "form.meta.timeEnd", "aggregation": "first"},
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
        # Scanned real submissions: whenever this answer is 'no', the FLW
        # never got a QR code to scan, so qr_code_visit_verification is
        # always blank -- not an error, just "not applicable this visit".
        # Distinguishes that from a genuinely missing/unexpected blank.
        "name": "mother_has_qr_code_available",
        "path": (
            "form.qr_code_verification.qr_code_scan."
            "Does_the_mother_have__the_QR_code_photo_she_took_at_registration"
        ),
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
        # Scanned real submissions: verification_properties is entirely
        # absent on some forms, with visit_verification_outcome sitting at
        # the top level of `form` directly instead (confirmed on 2 of 9
        # forms that had any outcome at all). Both paths needed.
        "name": "visit_verification_outcome",
        "paths": [
            "form.verification_properties.visit_verification_outcome",
            "form.visit_verification_outcome",
        ],
        "aggregation": "first",
    },
]

_VISIT_FORM_NAMES = [
    "ANC Visit ",
    "Post delivery visit",
    "1 Week Visit",
    "1 Month Visit",
    "3 Month Visit",
    "6 Month Visit",
]

# form_name must match exactly what CommCare HQ's Application Structure API
# returns (get_form_xmlns does an exact string match against the form's name
# dict) -- including the trailing space on "ANC Visit ".
ANC_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "ANC Visit ", "domain": TEST_DOMAIN, "app_id": TEST_APP_ID},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

POST_DELIVERY_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Post delivery visit",
        "domain": TEST_DOMAIN,
        "app_id": TEST_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

ONE_WEEK_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "1 Week Visit",
        "domain": TEST_DOMAIN,
        "app_id": TEST_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

ONE_MONTH_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "1 Month Visit",
        "domain": TEST_DOMAIN,
        "app_id": TEST_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

THREE_MONTH_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "3 Month Visit",
        "domain": TEST_DOMAIN,
        "app_id": TEST_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

SIX_MONTH_VISIT_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "6 Month Visit",
        "domain": TEST_DOMAIN,
        "app_id": TEST_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

# --- Production-domain twins ------------------------------------------------
# Same six forms, same fields, but no domain/app_id override: opp 765 owns
# ccc-mbw-production, so the standard app_id_source="opportunity" resolution
# reaches it directly.

PROD_ANC_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "ANC Visit ", "app_id_source": "opportunity"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

PROD_POST_DELIVERY_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "Post delivery visit", "app_id_source": "opportunity"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

PROD_ONE_WEEK_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "1 Week Visit", "app_id_source": "opportunity"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

PROD_ONE_MONTH_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "1 Month Visit", "app_id_source": "opportunity"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

PROD_THREE_MONTH_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "3 Month Visit", "app_id_source": "opportunity"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

PROD_SIX_MONTH_VISIT_SCHEMA = {
    "data_source": {"type": "cchq_forms", "form_name": "6 Month Visit", "app_id_source": "opportunity"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": _VISIT_FIELDS,
}

# ---------------------------------------------------------------------------
# Eligible-FLW pipelines (commcare-user cases, gate which FLWs' visits show)
# ---------------------------------------------------------------------------

ELIGIBLE_FLW_SCHEMA = {
    "data_source": {"type": "cchq_cases", "case_type": "commcare-user", "domain": TEST_DOMAIN},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "visit_verification", "path": "case.properties.visit_verification", "aggregation": "first"},
    ],
}

PROD_ELIGIBLE_FLW_SCHEMA = {
    "data_source": {"type": "cchq_cases", "case_type": "commcare-user"},
    "grouping_key": "entity_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "visit_verification", "path": "case.properties.visit_verification", "aggregation": "first"},
    ],
}

# Alias names are read directly by RENDER_CODE (VISIT_PIPELINE_ALIASES and the
# eligible_flws.../eligible_flws_prod pair) to merge all twelve visit
# pipelines and both FLW-eligibility gates -- keep them in sync.
PIPELINE_SCHEMAS = [
    {
        "alias": "visits_anc_visit",
        "name": "MBW Visit Verification — ANC Visit (test)",
        "description": "ANC Visit form submissions with the visit-verification block (test domain).",
        "schema": ANC_VISIT_SCHEMA,
    },
    {
        "alias": "visits_post_delivery_visit",
        "name": "MBW Visit Verification — Post delivery visit (test)",
        "description": "Post delivery visit form submissions (test domain). Empty until this form gains the block.",
        "schema": POST_DELIVERY_VISIT_SCHEMA,
    },
    {
        "alias": "visits_1_week_visit",
        "name": "MBW Visit Verification — 1 Week Visit (test)",
        "description": "1 Week Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": ONE_WEEK_VISIT_SCHEMA,
    },
    {
        "alias": "visits_1_month_visit",
        "name": "MBW Visit Verification — 1 Month Visit (test)",
        "description": "1 Month Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": ONE_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_3_month_visit",
        "name": "MBW Visit Verification — 3 Month Visit (test)",
        "description": "3 Month Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": THREE_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_6_month_visit",
        "name": "MBW Visit Verification — 6 Month Visit (test)",
        "description": "6 Month Visit form submissions (test domain). Empty until this form gains the block.",
        "schema": SIX_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_prod_anc_visit",
        "name": "MBW Visit Verification — ANC Visit (production)",
        "description": "ANC Visit form submissions from opp 765's real production app. Empty until it gains the block.",
        "schema": PROD_ANC_VISIT_SCHEMA,
    },
    {
        "alias": "visits_prod_post_delivery_visit",
        "name": "MBW Visit Verification — Post delivery visit (production)",
        "description": (
            "Post delivery visit form submissions from opp 765's real production app. "
            "Empty until it gains the block."
        ),
        "schema": PROD_POST_DELIVERY_VISIT_SCHEMA,
    },
    {
        "alias": "visits_prod_1_week_visit",
        "name": "MBW Visit Verification — 1 Week Visit (production)",
        "description": "1 Week Visit form submissions from opp 765's real production app. Empty until it gains the block.",
        "schema": PROD_ONE_WEEK_VISIT_SCHEMA,
    },
    {
        "alias": "visits_prod_1_month_visit",
        "name": "MBW Visit Verification — 1 Month Visit (production)",
        "description": (
            "1 Month Visit form submissions from opp 765's real production app. Empty until it gains the block."
        ),
        "schema": PROD_ONE_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_prod_3_month_visit",
        "name": "MBW Visit Verification — 3 Month Visit (production)",
        "description": (
            "3 Month Visit form submissions from opp 765's real production app. Empty until it gains the block."
        ),
        "schema": PROD_THREE_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "visits_prod_6_month_visit",
        "name": "MBW Visit Verification — 6 Month Visit (production)",
        "description": (
            "6 Month Visit form submissions from opp 765's real production app. Empty until it gains the block."
        ),
        "schema": PROD_SIX_MONTH_VISIT_SCHEMA,
    },
    {
        "alias": "eligible_flws",
        "name": "MBW Visit Verification — Eligible FLWs (test)",
        "description": "commcare-user cases with the visit_verification property (test domain).",
        "schema": ELIGIBLE_FLW_SCHEMA,
    },
    {
        "alias": "eligible_flws_prod",
        "name": "MBW Visit Verification — Eligible FLWs (production)",
        "description": "commcare-user cases with the visit_verification property (opp 765's real production domain).",
        "schema": PROD_ELIGIBLE_FLW_SCHEMA,
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
        "Reads from both the ccc-mbw-experiments-1 test domain and opp 765's own "
        "production app — see module docstring."
    ),
    "version": 1,
    "templateType": "mbw_visit_verification",
    "statuses": [
        {"id": "active", "label": "Active", "color": "blue"},
    ],
    "config": {
        # Both the visit pipelines (cchq_forms) and the eligible-FLW pipelines
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
