"""
MBW Monitoring V2 Workflow Template.

Pipeline-based version of the MBW monitoring dashboard. Uses 3 pipeline
sources (Connect visits, CCHQ registrations, CCHQ GS forms) and an
mbw_monitoring job handler for complex computations.

Replaces the custom_analysis SSE streaming approach in mbw_monitoring/.

The render code is standalone (not derived from V1) and lives in
mbw_monitoring_v2_render.js alongside this file.
"""

from pathlib import Path

# Default Gold Standard supervisor app on the MBW Solina production domain.
# V1 also defaults to this literal in mbw_monitoring/template.py when no
# monitoring_session.gs_app_id is set — so for the common case both paths use
# the same value. State-driven per-run overrides (via instance.state.gs_app_id)
# are a known gap; full plumbing requires threading state through
# WorkflowDataAccess.get_pipeline_data → execute_pipeline → _schema_to_config.
DEFAULT_GS_APP_ID = "2ca67a89dd8a2209d75ed5599b45a5d1"

DEFINITION = {
    "name": "MBW Monitoring V2",
    "description": "Pipeline-based MBW monitoring with GPS analysis, follow-up rates, and FLW assessment",
    "version": 1,
    "templateType": "mbw_monitoring_v2",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
        "job_type": "mbw_monitoring",
    },
    "pipeline_sources": [],
}

# Pipeline schemas — these create pipeline definitions when the template is initialized
PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "MBW Visit Forms",
        "description": "Connect CSV visit data for MBW monitoring",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [
                {"name": "gps_location", "path": "form.meta.location.#text", "aggregation": "first"},
                {"name": "case_id", "path": "form.case.@case_id", "aggregation": "first"},
                {"name": "mother_case_id", "path": "form.parents.parent.case.@case_id", "aggregation": "first"},
                {"name": "form_name", "path": "form.@name", "aggregation": "first"},
                {"name": "visit_datetime", "path": "form.meta.timeEnd", "aggregation": "first"},
                {
                    "name": "entity_id_deliver",
                    "paths": [
                        "form.mbw_visit.deliver.entity_id",
                        "form.visit_completion.mbw_visit.deliver.entity_id",
                    ],
                    "aggregation": "first",
                },
                {
                    "name": "entity_name",
                    "paths": [
                        "form.mbw_visit.deliver.entity_name",
                        "form.visit_completion.mbw_visit.deliver.entity_name",
                    ],
                    "aggregation": "first",
                },
                {
                    "name": "parity",
                    "path": "form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
                    "aggregation": "first",
                },
                {
                    "name": "anc_completion_date",
                    "path": "form.visit_completion.anc_completion_date",
                    "aggregation": "first",
                },
                {"name": "pnc_completion_date", "path": "form.pnc_completion_date", "aggregation": "first"},
                {
                    "name": "baby_dob",
                    "path": "form.capture_the_following_birth_details.baby_dob",
                    "aggregation": "first",
                },
                {
                    "name": "app_build_version",
                    "path": "form.meta.app_build_version",
                    "aggregation": "first",
                    "transform": "int",
                },
                {
                    "name": "bf_status",
                    "paths": [
                        "form.feeding_history.pnc_current_bf_status",
                        "form.feeding_history.oneweek_current_bf_status",
                        "form.feeding_history.onemonth_current_bf_status",
                        "form.feeding_history.threemonth_current_bf_status",
                        "form.feeding_history.sixmonth_current_bf_status",
                    ],
                    "aggregation": "first",
                },
            ],
        },
    },
    {
        "alias": "registrations",
        "name": "CCHQ Registration Forms",
        "description": "CCHQ registration forms for mother data",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Register Mother",
                "app_id_source": "opportunity",
            },
            "grouping_key": "case_id",
            "terminal_stage": "visit_level",
            "fields": [
                {"name": "expected_visits", "path": "form.expected_visits", "aggregation": "first"},
                {"name": "mother_name", "path": "form.mother_name", "aggregation": "first"},
                {"name": "user_connect_id", "path": "form.user_connect_id", "aggregation": "first"},
            ],
        },
    },
    {
        "alias": "gs_forms",
        "name": "CCHQ Gold Standard Forms",
        "description": "CCHQ Gold Standard visit checklist forms",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Gold Standard Visit Checklist",
                "app_id_source": "opportunity",
                "gs_app_id": DEFAULT_GS_APP_ID,
            },
            "grouping_key": "case_id",
            "terminal_stage": "visit_level",
            "fields": [
                # gs_score: the team's own parity command reads
                # `computed.gs_score or form.checklist_percentage` because real
                # GS forms in prod use one field or the other depending on
                # form version. We list both paths so V2 picks up whichever
                # is present rather than silently extracting None.
                {
                    "name": "gs_score",
                    "paths": ["form.gs_score", "form.checklist_percentage"],
                    "aggregation": "first",
                },
                {"name": "assessor_name", "path": "form.assessor_name", "aggregation": "first"},
                {"name": "assessment_date", "path": "form.meta.timeEnd", "aggregation": "first"},
                # user_connect_id is the FLW-link key the JS-side enrichment
                # joins on to attribute scores to the right FLW. V1 reads it
                # off the raw form as `load_flw_connect_id`; modern forms
                # use `user_connect_id`. We try both for robustness — the
                # extractor uses whichever path matches first.
                {
                    "name": "user_connect_id",
                    "paths": ["form.user_connect_id", "form.load_flw_connect_id"],
                    "aggregation": "first",
                },
            ],
        },
    },
]

# ---------------------------------------------------------------------------
# Load standalone render code from adjacent .js file
# ---------------------------------------------------------------------------

RENDER_CODE = (Path(__file__).parent / "mbw_monitoring_v2_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "mbw_monitoring_v2",
    "name": "MBW Monitoring V2",
    "description": "Pipeline-based MBW monitoring with GPS analysis, follow-up rates, and FLW assessment",
    "icon": "fa-baby",
    "color": "pink",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
