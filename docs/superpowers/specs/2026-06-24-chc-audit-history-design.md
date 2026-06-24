# CHC Audit History — Program 176 Workflow Template

**Date:** 2026-06-24  
**Program:** 176 (DIMAGI-CHC-RCT, Nigeria)  
**Opps:** EHA #1973, JHF #1976, SOLINA #1978, ISODAF #1982

## What it builds

A multi-opp workflow template (`chc_audit_history`) that reads audit reports, entries, and
tasks from the Connect export API and presents three tabs:

1. **Audit History** — sortable table of audit reports (one row per report per opp)
2. **Metric Detail** — FLW × metric pivot for a selected report/opp
3. **FLW Longitudinal** — per-FLW flag trend across audit cycles (newest cycle leftmost)

## New infrastructure: `connect_export` pipeline data source type

The existing pipeline system supports `connect_csv`, `cchq_forms`, and `ocs_sessions`.
This feature adds a fourth type: `connect_export`. It fetches paginated JSON from the
Connect production export API endpoints (`/export/opportunity/<id>/<endpoint>/`) and
normalizes records to visit-dict shape so the existing SQL extraction path works unchanged.

### Changes to existing files

**`commcare_connect/labs/analysis/config.py`**
- Add `endpoint: str = ""` to `DataSourceConfig`
- Add `"connect_export"` to the whitelist in `__post_init__`

**`commcare_connect/labs/analysis/pipeline.py`** — 3 dispatch sites
- Site 1 (~line 522): inside the cached-path `elif`s, add a `connect_export` branch
- Site 2 (~line 619): same pattern for the force-refresh path
- Site 3 (~line 748): after the `ocs_sessions` block, add a `connect_export` block

### New file

**`commcare_connect/labs/analysis/backends/sql/connect_export_fetcher.py`**
- `fetch_connect_export_as_visit_dicts(request, data_source, access_token, opportunity_id)`
- Paginates `GET {CONNECT_PRODUCTION_URL}/export/opportunity/<id>/<endpoint>/`
- Uses `Accept: application/json; version=2.0` and Bearer token auth
- Normalizes records: `{"form_json": {"<endpoint_singular>": record}, "username": ..., ...}`

Endpoint-to-singular map:
| Endpoint | Key in form_json | username source | visit_date source |
|---|---|---|---|
| `audit_reports` | `audit_report` | `completed_by_username` | `period_start` |
| `audit_report_entries` | `audit_entry` | `username` | `date_created` |
| `assigned_tasks` | `assigned_task` | `username` | `date_created` |
| `work_areas` | `work_area` | `""` | `date_created` |

## Pipeline schemas

### `audit_reports`
- `data_source: {"type": "connect_export", "endpoint": "audit_reports"}`
- `terminal_stage: "visit_level"` — one row per report
- Fields: `report_id`, `opportunity_id`, `period_start`, `period_end`, `status`, `completed_by_username`, `completed_date`, `date_created`

### `audit_entries`
- `data_source: {"type": "connect_export", "endpoint": "audit_report_entries"}`
- `terminal_stage: "visit_level"` — one row per FLW per report
- Fields: `report_id` (FK int), `username`, `results` (JSON string of metric dict), `flagged`, `date_created`

### `tasks`
- `data_source: {"type": "connect_export", "endpoint": "assigned_tasks"}`
- `terminal_stage: "visit_level"` — one row per task
- Fields: `username`, `task_status` (aliased from `status`), `date_created`, `completed_at`, `duration`

## Audit History tab — column spec

| Column | Source | Note |
|---|---|---|
| Created Date | `audit_report.date_created` | Date the report record was created |
| Audit Period | `period_start` + `period_end` | "MMM D – MMM D, YYYY" |
| FLWs | count of entries for this report | Derived from `audit_entries` rows |
| Status | `audit_report.status` | Pill: Completed / Pending |
| % FLWs Passed | entries with `flagged = false` / total | `% (N/D)` format |
| % Tasks Completed | tasks with `status=closed` in period / all tasks in period | `% (N/D)` |
| % FLWs w/ Pending Task | FLWs with any open task / total FLWs | `% (N/D)` |
| Time to Task Completed | avg `duration` for closed tasks (minutes) | Rounded integer |
| Run By | `completed_by_username` | |

Columns are sortable (click header toggles asc/desc).

## Metric Detail tab — metric list

10 metrics from `AuditReportEntry.results` JSON:
1. Camping (Visit:Building Ratio)
2. Gender Ratio Deviation
3. MUAC Photo Compliance
4. Age Heaping
5. WA Coverage to Visit Ratio
6. Inaccessible WA Rate – Early Warning
7. Inaccessible WA Rate – Last Completed WAG
8. Vaccine Rate
9. Vaccine Card Photo Compliance
10. MUAC Distribution Pattern Index (MDPI)

The `results` JSON field is extracted whole (`path: "audit_entry.results"`, `aggregation: "first"`).
The render layer calls `JSON.parse(row.results)` and reads metric values by key.

Flag thresholds (for amber cell highlighting):
- Camping > 0
- MUAC Photo Compliance < 90
- Age Heaping > 15
- WA Coverage to Visit Ratio > 0.13

Filters: Report (dropdown of completed audits) × Opportunity.

## FLW Longitudinal tab

Columns: FLW Name | **Total Flags** (sum across all cycles) | [Newest Cycle] | … | [Oldest Cycle]

Cycle columns show: `% metrics passed` + pass-rate bar + `% tasks done`.
Cell background encodes severity: 0 flags = white, 1 = lightest amber, 2 = mid amber, 3+ = strong amber.
Cycles ordered newest-leftmost, oldest-rightmost.

## Template config

```python
TEMPLATE = {
    "key": "chc_audit_history",
    "name": "CHC Audit History",
    "description": "Program-level audit history view across all CHC-RCT opportunities.",
    "icon": "fa-clipboard-list",
    "color": "green",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
```

`supports_saved_runs` is `False` for the initial version. The data refreshes on each view.

## Task-to-report linkage

No FK between `AssignedTask` and `AuditReport` exists in production. Tasks are linked
to an audit period by timing: tasks whose `date_created` falls within `period_start`..`period_end`
of a given report are considered linked to that report. This is compute-only in the render
layer — no write-path change.

## Safety notes

- The `connect_export` type is purely additive — existing pipeline types are unchanged.
- No new migrations, no changes to non-labs apps.
- The fetcher uses the same Bearer token already in scope for `connect_csv` pipelines.
