"""
MBW Auditing V4 Workflow Template.

Streamlined MBW audit dashboard focused on core auditing metrics, task workflow,
and final performance categorization. Built on the mbw_monitoring job backend
with a simplified, performant 4-tab UI.

Tabs:
  1. Per FLW Audit Report  — core metrics, flags, performance categories, task triggers
  2. Improvement Within Audit — flagged/task FLWs, change since run start
  3. Summary by Performance Band — aggregate metrics grouped by assessment status
  4. Guide — metric definitions

Architecture: Same 3-pipeline + job backend as MBW Monitoring V2, but with
a greatly simplified render layer (no GPS maps, no drill-downs, no FLW selection step).
"""

from pathlib import Path

DEFINITION = {
    "name": "MBW Auditing V4",
    "description": "Streamlined bi-weekly MBW audit: flag FLWs, trigger tasks, track improvement, categorize performance.",
    "version": 1,
    "templateType": "mbw_auditing_v4",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
        "job_type": "mbw_auditing_v4",
    },
    "pipeline_sources": [],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "MBW Visit Forms",
        "description": "Connect CSV visit data — GPS coordinates, breastfeeding status, case linking",
        "schema": {
            "data_source": {"type": "connect_csv"},
            "grouping_key": "username",
            "terminal_stage": "visit_level",
            "fields": [
                # lat/lon instead of raw gps_location string — the pipeline layer
                # auto-generates distance_from_prev_case_visit_m via haversine window fn
                {
                    "name": "latitude",
                    "paths": ["form.meta.location.#text", "form.meta.location"],
                    "aggregation": "first",
                },
                {
                    "name": "longitude",
                    "paths": ["form.meta.location.#text", "form.meta.location"],
                    "aggregation": "first",
                },
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
                {
                    "name": "pnc_completion_date",
                    "path": "form.pnc_completion_date",
                    "aggregation": "first",
                },
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
        "description": "CommCare HQ Register Mother forms — visit schedules, eligibility, mother metadata",
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
                {
                    "name": "eligible_full_intervention_bonus",
                    "path": "form.eligible_full_intervention_bonus",
                    "aggregation": "first",
                },
                {"name": "registration_date", "path": "form.meta.timeEnd", "aggregation": "first"},
            ],
        },
    },
    {
        "alias": "gs_forms",
        "name": "CCHQ Gold Standard Forms",
        "description": "CommCare HQ Gold Standard Visit Checklist forms — GS scores per FLW",
        "schema": {
            "data_source": {
                "type": "cchq_forms",
                "form_name": "Gold Standard Visit Checklist",
                "app_id_source": "opportunity",
                "gs_app_id": "2ca67a89dd8a2209d75ed5599b45a5d1",
            },
            "grouping_key": "case_id",
            "terminal_stage": "visit_level",
            "fields": [
                # checklist_percentage fallback handles older form versions
                {
                    "name": "gs_score",
                    "paths": ["form.gs_score", "form.checklist_percentage"],
                    "aggregation": "first",
                },
                {"name": "assessor_name", "path": "form.assessor_name", "aggregation": "first"},
                {"name": "assessment_date", "path": "form.meta.timeEnd", "aggregation": "first"},
                # load_flw_connect_id fallback handles older form versions
                {
                    "name": "user_connect_id",
                    "paths": ["form.user_connect_id", "form.load_flw_connect_id"],
                    "aggregation": "first",
                },
            ],
        },
    },
]

RENDER_CODE = (Path(__file__).parent / "mbw_auditing_v4_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "mbw_auditing_v4",
    "name": "MBW Auditing V4",
    "description": "Streamlined bi-weekly MBW audit: flag FLWs, trigger tasks, track improvement, categorize performance.",
    "icon": "fa-clipboard-check",
    "color": "pink",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
