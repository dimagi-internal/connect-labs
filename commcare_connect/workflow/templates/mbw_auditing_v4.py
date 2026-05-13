"""MBW Auditing V4 — pipeline-native audit workflow.

Three pipelines feed a Python job handler that computes all audit metrics.
No form_json reads — all data comes from SQL-computed pipeline rows.

Pipeline aliases (must match pipeline_sources in DEFINITION):
  visits        — per-visit rows with GPS coords, bf_status, form_name,
                  and lag_haversine distance_from_prev_case_visit_m
  registrations — per-mother rows with mbw_visit_schedules extractor
                  and eligible_full_intervention_bonus
  gs_forms      — per-GS-visit rows with gs_score and user_connect_id
"""

from pathlib import Path

DEFAULT_GS_APP_ID = "2ca67a89dd8a2209d75ed5599b45a5d1"

DEFINITION = {
    "name": "MBW Auditing V4",
    "description": "Pipeline-native MBW audit dashboard. Computes follow-up rates, GPS metrics, and GS scores without reading raw form JSON.",
    "version": 1,
    "templateType": "mbw_auditing_v4",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        "job_type": "mbw_auditing_v4",
        "server_fetch_pipelines": True,
        "auth_requires": ["connect", "commcare_hq"],
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

VISITS_GPS_SCHEMA = {
    # visit_level required for lag_haversine window function.
    # Lean schema: only fields consumed by the job handler.
    # Eight fields that existed in the original pipeline (entity_id_deliver,
    # entity_name, parity, anc_completion_date, pnc_completion_date, baby_dob,
    # app_build_version, case_id) were removed — none are read by the handler.
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "mother_case_id", "path": "form.parents.parent.case.@case_id", "aggregation": "first"},
        {"name": "visit_datetime", "path": "form.meta.timeEnd", "aggregation": "first"},
        {"name": "form_name", "path": "form.@name", "aggregation": "first"},
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
        # GPS — .#text fallback covers both XML text and direct string
        {
            "name": "latitude",
            "paths": ["form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lat",
        },
        {
            "name": "longitude",
            "paths": ["form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lon",
        },
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

REGISTRATIONS_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Register Mother",
        "app_id_source": "opportunity",
    },
    "grouping_key": "case_id",
    "terminal_stage": "visit_level",
    "fields": [
        {
            "name": "mother_case_id",
            "paths": [
                "form.var_visit_1.mother_case_id",
                "form.var_visit_2.mother_case_id",
                "form.var_visit_3.mother_case_id",
                "form.var_visit_4.mother_case_id",
                "form.var_visit_5.mother_case_id",
                "form.var_visit_6.mother_case_id",
            ],
            "aggregation": "first",
        },
        {
            "name": "eligible_full_intervention_bonus",
            "path": "form.eligible_full_intervention_bonus",
            "aggregation": "first",
        },
        # Per-mother visit schedules used by the job handler for follow-up rate
        # and % still eligible. The extractor walks var_visit_1..6 and returns
        # a list of {visit_type, visit_date_scheduled, visit_expiry_date, mother_case_id}.
        {"name": "schedules", "extractor": "mbw_visit_schedules", "aggregation": "first"},
    ],
}

GS_FORMS_SCHEMA = {
    # aggregated stage: one row per FLW with max gs_score — avoids returning
    # one row per GS assessment visit and pre-computes the max at SQL level.
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Gold Standard Visit Checklist",
        "app_id_source": "opportunity",
        "gs_app_id": DEFAULT_GS_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "gs_score",
            "paths": ["form.gs_score", "form.checklist_percentage"],
            "aggregation": "max",
        },
        {
            "name": "user_connect_id",
            "paths": ["form.user_connect_id", "form.load_flw_connect_id"],
            "aggregation": "first",
        },
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "MBW Visit Forms (V4)",
        "description": "Per-visit rows with GPS coords, bf_status, and lag_haversine distance to previous mother visit",
        "schema": VISITS_GPS_SCHEMA,
    },
    {
        "alias": "registrations",
        "name": "CCHQ Registration Forms (V4)",
        "description": "Per-mother registration rows with visit schedules and intervention eligibility",
        "schema": REGISTRATIONS_SCHEMA,
    },
    {
        "alias": "gs_forms",
        "name": "CCHQ Gold Standard Forms (V4)",
        "description": "Gold Standard visit checklist forms with FLW scores",
        "schema": GS_FORMS_SCHEMA,
    },
]

RENDER_CODE = (Path(__file__).parent / "mbw_auditing_v4_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "mbw_auditing_v4",
    "name": "MBW Auditing V4",
    "description": "Pipeline-native MBW audit: follow-up rates, GPS metrics, GS scores, and performance categorization.",
    "icon": "fa-clipboard-check",
    "color": "blue",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
