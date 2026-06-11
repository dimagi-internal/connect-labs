"""MBW Auditing V5 — SQL-native audit workflow (no Python job handler).

Same four pipelines as v4, but compute lives entirely in the render layer.
The Python job handler v4 used (`workflow/job_handlers/mbw_auditing_v4.py`)
is replaced by JSX useMemo Maps over the raw pipeline rows. This unlocks
saved-runs by making the framework's default snapshot path (pipelines +
workers + state) sufficient — no opaque job-result blob to capture.

Pipeline aliases (must match pipeline_sources in DEFINITION):
  visits        — per-visit rows with GPS coords, bf_status, form_name,
                  and lag_haversine distance_from_prev_case_visit_m
  visits_agg    — per-FLW aggregated counts: num_mothers, bf_count, ebf_count,
                  visits_completed (NEW in v5), anc_ok_mother_count (NEW in v5)
  registrations — per-mother rows with mbw_visit_schedules extractor
                  and eligible_full_intervention_bonus
  gs_forms      — per-GS-visit rows with gs_score and user_connect_id
"""

from pathlib import Path

DEFAULT_GS_APP_ID = "2ca67a89dd8a2209d75ed5599b45a5d1"

DEFINITION = {
    "name": "MBW Auditing V5",
    "description": "SQL-native MBW audit dashboard. Pipelines + JSX only; no Python job handler. Snapshots saved on completion.",
    "version": 1,
    "templateType": "mbw_auditing_v5",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        # No job_type / server_fetch_pipelines — v5 has no Python job handler.
        # All compute is SQL (pipelines) + JSX (render).
        "auth_requires": ["connect", "commcare_hq"],
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

_BF_STATUS_PATHS = [
    "form.feeding_history.pnc_current_bf_status",
    "form.feeding_history.oneweek_current_bf_status",
    "form.feeding_history.onemonth_current_bf_status",
    "form.feeding_history.threemonth_current_bf_status",
    "form.feeding_history.sixmonth_current_bf_status",
]

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
        {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
        {"name": "form_name", "path": "form.@name", "aggregation": "first"},
        {"name": "bf_status", "paths": _BF_STATUS_PATHS, "aggregation": "first"},
        {
            "name": "antenatal_visit_completion",
            "path": "form.visit_completion.antenatal_visit_completion",
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

VISITS_AGG_SCHEMA = {
    # aggregated stage: one row per FLW. Identical to v4's schema so the engine
    # produces byte-identical cache rows (same config_hash, same SQL). v5's
    # render reads these aggregates plus iterates raw `visits` rows in JSX for
    # everything else — keeping the SQL surface unchanged is the guarantee that
    # v4 and v5 see exactly the same numbers for shared metrics.
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "num_mothers",
            "path": "form.parents.parent.case.@case_id",
            "aggregation": "count_distinct",
        },
        {
            "name": "bf_count",
            "paths": _BF_STATUS_PATHS,
            "aggregation": "count",
        },
        {
            # contains_word matches "ebf" as a whitespace-separated token,
            # mirroring v1: `if "ebf" in bf_status.split()`
            "name": "ebf_count",
            "paths": _BF_STATUS_PATHS,
            "aggregation": "count",
            "filter_paths": _BF_STATUS_PATHS,
            "filter_value": "ebf",
            "filter_op": "contains_word",
        },
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
        "name": "MBW Visit Forms (V5)",
        "description": "Per-visit rows with GPS coords, bf_status, and lag_haversine distance to previous mother visit",
        "schema": VISITS_GPS_SCHEMA,
    },
    {
        "alias": "visits_agg",
        "name": "MBW Visit Forms — Aggregated (V5)",
        "description": "Per-FLW aggregated counts: distinct mothers, BF visits, EBF visits",
        "schema": VISITS_AGG_SCHEMA,
    },
    {
        "alias": "registrations",
        "name": "CCHQ Registration Forms (V5)",
        "description": "Per-mother registration rows with visit schedules and intervention eligibility",
        "schema": REGISTRATIONS_SCHEMA,
    },
    {
        "alias": "gs_forms",
        "name": "CCHQ Gold Standard Forms (V5)",
        "description": "Gold Standard visit checklist forms with FLW scores",
        "schema": GS_FORMS_SCHEMA,
    },
]

RENDER_CODE = (Path(__file__).parent / "mbw_auditing_v5_render.js").read_text(encoding="utf-8")

# Saved-runs snapshot manifest. v5 opts into the framework's
# in_progress | completed lifecycle so that completed runs preserve the
# exact data the dashboard rendered. The Monday audit-of-v5 workflow
# (sub-project 2) will consume these snapshots.
#
# What we capture:
#   - NO raw pipeline rows. A real MBW opp has 100k+ visits — a verbatim
#     capture measured 112 MB on opp 765 and OOM-killed a web worker. The
#     dashboard table the user was actually looking at is what gets frozen:
#     the render saves it into state as `concluded_*` keys in the same
#     state write that precedes view.complete(), so conclude does zero
#     pipeline work server-side.
#   - workers (FLW list at completion time).
#   - State keys: every key the render writes via onUpdateState, plus the
#     concluded_* dashboard captures.
#
# Per-FLW aggregates for ~100 FLWs come to a few hundred KB — under the
# framework's 1 MB warn line. See WORKFLOW_REFERENCE.md §9.
SNAPSHOT_INPUTS = {
    "pipelines": [],
    "workers": True,
    "state_keys": [
        "selected_workers",
        "worker_results",
        "task_states",
        "audit_statuses",
        "previous_metrics",
        "previous_categories",
        "concluded_summaries",
        "concluded_prev_categories",
        "concluded_tab2",
    ],
}

SNAPSHOT_SCHEMA = {
    "version": 2,
    "keys": {
        "workers": "FLW list at completion (with opportunity_id tags for multi-opp)",
        "state.selected_workers": "FLWs selected at run launch",
        "state.worker_results": "Per-FLW performance category decisions",
        "state.task_states": "Per-FLW task creation/closure tracking",
        "state.audit_statuses": "Per-FLW audit-required / audit-not-required gate",
        "state.previous_metrics": "Per-FLW metric snapshot captured at conclude time",
        "state.previous_categories": "Per-FLW category snapshot captured at conclude time",
        "state.concluded_summaries": "Computed per-FLW dashboard rows (flw_summaries) as rendered at conclude",
        "state.concluded_prev_categories": "The baseline categories the Prev Category column showed at conclude",
        "state.concluded_tab2": "Per-flagged-FLW baseline-rate (Tab 2) results as of conclude",
    },
}

TEMPLATE = {
    "key": "mbw_auditing_v5",
    "name": "MBW Auditing V5",
    "description": "SQL-native MBW audit (no Python job handler). Pipeline-pure + saved runs.",
    "icon": "fa-clipboard-check",
    "color": "blue",
    "supports_saved_runs": True,
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
