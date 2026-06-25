"""CHC Audit History — multi-opp program-level audit view for Program 176.

Three tabs:
  Audit History    — sortable table of audit reports across all opps
  Metric Detail    — FLW × metric pivot for a selected report/opp
  FLW Longitudinal — per-FLW flag trend across audit cycles (newest left)

Data sources:
  audit_reports       — /export/opportunity/<id>/audit_reports/
  audit_entries       — /export/opportunity/<id>/audit_report_entries/
  tasks               — /export/opportunity/<id>/assigned_tasks/
"""

from pathlib import Path

DEFINITION = {
    "name": "CHC Audit History",
    "description": "Program-level audit history across all CHC-RCT opportunities.",
    "version": 1,
    "templateType": "chc_audit_history",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
    ],
    "config": {
        "auth_requires": ["connect"],
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

AUDIT_REPORTS_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "audit_reports"},
    "grouping_key": "audit_report.id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "report_id", "path": "audit_report.id", "aggregation": "first"},
        {"name": "opportunity_id", "path": "audit_report.opportunity", "aggregation": "first"},
        {"name": "period_start", "path": "audit_report.period_start", "aggregation": "first"},
        {"name": "period_end", "path": "audit_report.period_end", "aggregation": "first"},
        {"name": "status", "path": "audit_report.status", "aggregation": "first"},
        {"name": "completed_by_username", "path": "audit_report.completed_by_username", "aggregation": "first"},
        {"name": "completed_date", "path": "audit_report.completed_date", "aggregation": "first"},
        {"name": "date_created", "path": "audit_report.date_created", "aggregation": "first"},
    ],
}

AUDIT_ENTRIES_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "audit_report_entries"},
    "grouping_key": "audit_entry.id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "report_id", "path": "audit_entry.audit_report", "aggregation": "first"},
        {"name": "username", "path": "audit_entry.username", "aggregation": "first"},
        # results is a JSON object — extracted as a JSON string, parsed by the render layer
        {"name": "results", "path": "audit_entry.results", "aggregation": "first"},
        # "is_flagged" avoids DuckDB treating the column as BOOLEAN (the API returns the string "true"/"false")
        {"name": "is_flagged", "path": "audit_entry.flagged", "aggregation": "first"},
        {"name": "date_created", "path": "audit_entry.date_created", "aggregation": "first"},
    ],
}

TASKS_SCHEMA = {
    "data_source": {"type": "connect_export", "endpoint": "assigned_tasks"},
    "grouping_key": "assigned_task.id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "username", "path": "assigned_task.username", "aggregation": "first"},
        {"name": "status", "path": "assigned_task.status", "aggregation": "first"},
        {"name": "date_created", "path": "assigned_task.date_created", "aggregation": "first"},
        {"name": "completed_at", "path": "assigned_task.completed_at", "aggregation": "first"},
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "audit_reports",
        "name": "CHC Audit Reports",
        "description": "One row per audit report — period, status, reviewer",
        "schema": AUDIT_REPORTS_SCHEMA,
    },
    {
        "alias": "audit_entries",
        "name": "CHC Audit Report Entries",
        "description": "One row per FLW per audit report — metric results, flag status",
        "schema": AUDIT_ENTRIES_SCHEMA,
    },
    {
        "alias": "tasks",
        "name": "CHC Assigned Tasks",
        "description": "One row per assigned task — FLW, status, dates, duration",
        "schema": TASKS_SCHEMA,
    },
]

RENDER_CODE = (Path(__file__).parent / "chc_audit_history_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "chc_audit_history",
    "name": "CHC Audit History",
    "description": "Program-level audit history across all CHC-RCT opportunities. "
    "Three tabs: Audit History, Metric Detail, FLW Longitudinal.",
    "icon": "fa-clipboard-list",
    "color": "green",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
