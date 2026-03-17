# MBW Monitoring Dashboard - Technical Documentation

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [Workflow Module Integration](#workflow-module-integration)
4. [Data Flow](#data-flow)
5. [Four-Tab Dashboard](#four-tab-dashboard)
6. [Data Sources & APIs](#data-sources--apis)
7. [Pipeline Configuration](#pipeline-configuration)
8. [Follow-Up Rate Business Logic](#follow-up-rate-business-logic)
9. [Quality Metrics](#quality-metrics)
10. [Action Handler System](#action-handler-system)
11. [Workflow Data Model](#workflow-data-model)
12. [Caching Strategy](#caching-strategy)
13. [Authentication & OAuth](#authentication--oauth)
14. [Frontend Architecture](#frontend-architecture)
15. [Template Sync & Build Pipeline](#template-sync--build-pipeline)
16. [Features & Capabilities](#features--capabilities)
17. [Configuration](#configuration)
18. [File Reference](#file-reference)
19. [Key Field Paths](#key-field-paths)
20. [Troubleshooting](#troubleshooting)

---

## Overview

The MBW (Mother Baby Wellness) Monitoring Dashboard is a real-time performance monitoring tool for frontline health workers (FLWs) operating within the CommCare Connect ecosystem. It provides supervisors with a unified view of FLW performance across three dimensions:

- **Overview**: High-level per-FLW summary combining cases registered, follow-up rate, GS score, GPS metrics, and quality/fraud indicators
- **GPS Analysis**: Distance-based anomaly detection using Haversine calculations to flag suspicious travel patterns
- **Follow-Up Rate**: Visit completion tracking across 6 visit types (ANC, Postnatal, Week 1, Month 1, Month 3, Month 6) with per-mother drill-down, eligibility filtering, and grace period
- **FLW Performance**: Aggregated case metrics grouped by each FLW's latest assessment status (Eligible for Renewal, Probation, Suspended, No Category)

The dashboard runs within the **Workflow module** of Connect Labs. The UI is a React component defined as a JSX string in Python (`template.py`), transpiled client-side by Babel standalone, and rendered dynamically by `DynamicWorkflow.tsx`. All data is loaded via a single Server-Sent Events (SSE) connection, enabling real-time progress feedback during data loading.

### Key Design Decisions

- **Render code pattern**: The entire dashboard UI (~1,860 lines of JSX) lives in a Python string (`RENDER_CODE`), transpiled by Babel in the browser. Only `React` is available as a global - no imports. All declarations use `var` (not `const`/`let`) for Babel compatibility.
- **Single SSE connection**: All three tabs load data from one streaming endpoint, avoiding redundant API calls
- **Hybrid filtering**: Some filters (Visit Status, App Version) are server-side and trigger an SSE reload with updated pipeline data; others (FLW, Mother, Date) are client-side and operate on already-fetched data via React state. Server-side filters require clicking "Apply" and state is persisted in `sessionStorage` to survive OAuth redirects.
- **Two-layer caching**: Pipeline-level cache (Redis) for visit form data + Django cache for CCHQ form/case data
- **Tolerance-based cache validation**: Caches are accepted if they meet count, percentage, or time-based tolerance thresholds
- **No database writes**: All data is fetched from external APIs (Connect Production + CommCare HQ) and cached transiently. Workflow state is persisted via the LabsRecord API.
- **CCHQ Form API for metadata**: Registration forms and GS forms are fetched directly from CCHQ Form API v1 (not from cases), with dynamic xmlns discovery via the Application Structure API
- **Cross-app xmlns discovery**: GS forms live in a separate supervisor app; the system searches all apps in the domain to find the correct xmlns
- **Action handler abstraction**: All user actions (assessments, task creation, OCS integration) go through a centralized `ActionHandlers` interface defined in `workflow-runner.tsx`, making the render code workflow-agnostic

---

## Architecture

### High-Level Architecture

```text
User Browser
    |
    +-- GET /labs/workflow/<id>/run/
    |   +-- WorkflowRunView
    |       +-- Loads definition, render code, workers, run state
    |       +-- Renders run.html with JSON payload in <script> tag
    |       +-- Browser loads workflow-runner-bundle.js
    |           +-- workflow-runner.tsx bootstraps React app
    |           +-- DynamicWorkflow.tsx loads Babel standalone
    |           +-- Babel transpiles RENDER_CODE (JSX string -> JS)
    |           +-- eval() creates WorkflowUI React component
    |           +-- Component renders with WorkflowProps
    |
    +-- SSE /custom_analysis/mbw_monitoring/stream/
    |   +-- MBWMonitoringStreamView (streams all dashboard data)
    |       +-- Step 1: AnalysisPipeline -> Connect API (visit forms, 13 fields)
    |       +-- Step 2: Connect API -> FLW names
    |       +-- Step 3: GPS analysis (Haversine distances)
    |       +-- Step 4a: CCHQ Form API -> Registration forms (mother metadata)
    |       +-- Step 4b: CCHQ Form API -> GS forms (Gold Standard scores)
    |       +-- Step 5: Follow-up metric aggregation (eligibility + grace period)
    |       +-- Step 6: Overview metric computation (quality, GPS, follow-up, GS)
    |
    +-- POST /labs/workflow/api/run/<id>/worker-result/
    |   +-- save_worker_result_api (save FLW assessment)
    |
    +-- POST /labs/workflow/api/run/<id>/complete/
    |   +-- complete_run_api (mark monitoring session complete)
    |
    +-- GET /custom_analysis/mbw_monitoring/api/gps/<username>/
    |   +-- MBWGPSDetailView (JSON drill-down for GPS visits)
    |   +-- Params: start_date, end_date (default: today-30 to today),
    |   |          opportunity_id, app_version_op, app_version_val
    |
    +-- POST /custom_analysis/mbw_monitoring/api/save-snapshot/
    |   +-- MBWSaveSnapshotView (save dashboard snapshot with GPS visits)
    |
    +-- GET /custom_analysis/mbw_monitoring/api/opportunity-flws/
    |   +-- OpportunityFLWListAPIView (FLW list with audit history)
    |
    +-- GET /custom_analysis/mbw_monitoring/api/snapshot/
    |   +-- MBWSnapshotView (returns stored dashboard snapshot or {has_snapshot: false})
    |
    +-- GET /custom_analysis/mbw_monitoring/api/oauth-status/
    |   +-- MBWOAuthStatusView (checks Connect/CommCare/OCS token expiry)
    |
    +-- GET /tasks/api/<task_id>/
    |   +-- task_detail_api (returns task data as JSON for inline task management)
    |
    +-- GET /tasks/<task_id>/ai/transcript/
    |   +-- task_ai_transcript (AI conversation messages)
    |
    +-- POST /tasks/api/<task_id>/update/
        +-- task_update (update task status, resolution details)
```

### Component Relationships

```text
+-------------------+     +-------------------------+     +------------------+
|  WorkflowRunView  |---->|  run.html               |---->| workflow-runner   |
|  (Django view)    |     |  (Django template)       |     | -bundle.js       |
+-------------------+     +------------+-------------+     +--------+---------+
                                       |                            |
                          JSON payload via                 React app bootstrap
                          <script id="workflow-data">               |
                                                           +--------v---------+
                                                           | DynamicWorkflow  |
                                                           | .tsx             |
                                                           +--------+---------+
                                                                    |
                                                           Babel transpile +
                                                           eval(RENDER_CODE)
                                                                    |
                                                           +--------v---------+
                                                           | WorkflowUI       |
                                                           | (React component)|
                                                           +--------+---------+
                                                                    |
                                              +---------------------+-------------------+
                                              |                     |                   |
                                     +--------v------+    +--------v------+   +--------v------+
                                     | SSE Stream    |    | Action        |   | onUpdateState |
                                     | (dashboard    |    | Handlers      |   | (persist      |
                                     |  data)        |    | (20 methods)  |   |  state)       |
                                     +---------------+    +---------------+   +---------------+
```

### Render Code Execution Pipeline

```text
Python (template.py)          Browser                         React
+------------------+     +-------------------+     +-------------------+
| RENDER_CODE      |     | Babel standalone  |     | DynamicWorkflow   |
| (JSX string,     |---->| transpiles JSX    |---->| wraps component   |
|  ~1860 lines)    |     | to plain JS       |     | with error        |
|                  |     |                   |     | boundary + props  |
| Stored in DB as  |     | eval() creates    |     |                   |
| WorkflowRender   |     | WorkflowUI fn     |     | Renders with:     |
| CodeRecord       |     |                   |     | definition,       |
+------------------+     +-------------------+     | instance, workers,|
                                                   | pipelines, links, |
                                                   | actions,          |
                                                   | onUpdateState     |
                                                   +-------------------+
```

---

## Workflow Module Integration

### How MBW Fits into the Workflow Framework

The MBW Monitoring Dashboard is one of several workflow templates registered in the Workflow module. Each template provides:

| Component         | MBW Value            | Purpose                                |
| ----------------- | -------------------- | -------------------------------------- |
| `key`             | `"mbw_monitoring"`   | Unique identifier in template registry |
| `name`            | `"MBW Monitoring"`   | Human-readable name                    |
| `definition`      | `DEFINITION` dict    | Workflow metadata, statuses, config    |
| `render_code`     | `RENDER_CODE` string | Full React component as JSX            |
| `pipeline_schema` | `None`               | No pipeline (uses SSE stream instead)  |

### Template Registry

Templates are auto-discovered by `commcare_connect/workflow/templates/__init__.py`:

```python
# Auto-discovers all modules with a TEMPLATE dict
from commcare_connect.workflow.templates import (
    TEMPLATES,           # Dict of all registered templates
    get_template,        # Get template by key
    list_templates,      # List all templates
    create_workflow_from_template,  # Create workflow + render code + optional pipeline
)
```

### Render Code Pattern

The RENDER_CODE is a Python triple-quoted string containing a single JSX function:

```javascript
function WorkflowUI({
  definition,
  instance,
  workers,
  pipelines,
  links,
  actions,
  onUpdateState,
}) {
  // All React hooks, state, effects, and JSX go here
  // Constraints:
  //   - Only `React` is available as a global (no imports)
  //   - Use `var` for all declarations (not const/let)
  //   - No ES module syntax (import/export)
  //   - Must export a function named `WorkflowUI`
  //   - Can use React.useState, React.useEffect, React.useMemo, React.useRef, etc.
}
```

### WorkflowProps Interface

Every render code component receives these standardized props:

```typescript
interface WorkflowProps {
  definition: WorkflowDefinition; // Template metadata, statuses, config, pipeline_sources
  instance: WorkflowInstance; // Run data: id, status, state (selected_workers, worker_results)
  workers: WorkerData[]; // FLW list from Connect API
  pipelines: Record<string, PipelineResult>; // Pipeline data keyed by alias (not used by MBW)
  links: LinkHelpers; // URL helpers: auditUrl(), taskUrl()
  actions: ActionHandlers; // 20 action methods (see Action Handler System section)
  onUpdateState: (newState: Record<string, unknown>) => Promise<void>; // Persist state changes
}
```

### DynamicWorkflow Component

`components/workflow/DynamicWorkflow.tsx` handles the transpilation pipeline:

1. **Load Babel**: Injects `@babel/standalone` script tag on first mount
2. **Transpile**: Wraps RENDER_CODE in a factory function, transpiles JSX via `Babel.transform()`
3. **Create Component**: `eval()` the transpiled code, extract `WorkflowUI` function
4. **Render**: Wraps in `DynamicErrorBoundary`, passes all `WorkflowProps`

---

## Data Flow

### Phase 1: Initial Page Load

1. **Browser requests** `GET /labs/workflow/<definition_id>/run/?run_id=<id>&opportunity_id=<id>`
2. **WorkflowRunView** (`commcare_connect/workflow/views.py`):
   - Loads workflow definition and render code from LabsRecord API
   - Loads or creates a workflow run (with initial state)
   - Fetches worker list from Connect API
   - Serializes everything into `workflow_data` JSON
3. **run.html** template renders:
   - `{{ workflow_data|json_script:"workflow-data" }}` — JSON payload in a `<script>` tag
   - `<script src="{% static 'bundles/js/workflow-runner-bundle.js' %}?v=3" defer></script>`
   - `<div id="workflow-root" data-csrf-token="{{ csrf_token }}">`
4. **workflow-runner.tsx** bootstraps:
   - Reads JSON from `#workflow-data` script tag
   - Creates action handlers via `createActionHandlers(csrfToken)`
   - Mounts `DynamicWorkflow` component with all props

### Phase 2: Dashboard Data Loading (SSE)

After the user selects FLWs and enters the dashboard step, the render code opens an SSE connection:

```javascript
var params = new URLSearchParams({
  run_id: String(instance.id),
  start_date: startStr, // today - 30 days
  end_date: endStr, // today
});
if (instance.opportunity_id) {
  params.set('opportunity_id', String(instance.opportunity_id));
}
if (appliedAppVersionOp && appliedAppVersionVal) {
  params.set('app_version_op', appliedAppVersionOp);
  params.set('app_version_val', appliedAppVersionVal);
}
var url = '/custom_analysis/mbw_monitoring/stream/?' + params.toString();
var es = new EventSource(url);
```

**Note**: Including the `opportunity_id` parameter avoids 302 redirects from `LabsContextMiddleware`. If omitted, EventSource handles the redirect transparently, but the extra round-trip adds latency.

The stream view executes 7 steps, yielding progress messages at each stage:

| Step | Data Source                                  | What It Fetches                                                                                                                                                                    | Cache                                                                 |
| ---- | -------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------- |
| 1    | Connect API via AnalysisPipeline             | Visit form data (13 FieldComputations: GPS, case IDs, form names, dates, parity, etc.). Filtered by `status_filter` param (default: approved only) via SQL `WHERE status IN (...)` | Pipeline cache (Redis, config-hash based; filter changes reuse cache) |
| 2    | Connect API                                  | Active FLW usernames + display names                                                                                                                                               | In-memory                                                             |
| 3    | In-memory                                    | GPS metrics (Haversine distances, daily travel)                                                                                                                                    | None (computed)                                                       |
| 4a   | CCHQ Form API v1                             | Registration forms -> mother metadata (name, age, phone, eligibility, EDD, etc.)                                                                                                   | Django cache (1hr)                                                    |
| 4b   | CCHQ Form API v1                             | Gold Standard Visit Checklist forms -> GS scores per FLW                                                                                                                           | Django cache (1hr)                                                    |
| 5    | In-memory                                    | Follow-up metrics (visit status, completion rates with eligibility + grace period)                                                                                                 | None (computed)                                                       |
| 6    | In-memory                                    | Overview metrics (merge follow-up rate, GS score, GPS, quality metrics)                                                                                                            | None (computed)                                                       |
| 7    | Connect API (audit sessions + workflow runs) | FLW Performance by assessment status (latest status per FLW + aggregated case metrics)                                                                                             | None (computed)                                                       |

#### Sectioned SSE Streaming (OOM Prevention)

Data is NOT sent as one large final payload. Instead, the backend sends 3 separate `data_section` SSE events to prevent OOM on large opportunities (50K+ visits):

1. **GPS section** — `gps_data` (FLW summaries without visits, date range, flag threshold, all_coordinates for aggregate map)
2. **Follow-up section** — `followup_data` (per-FLW/per-mother metrics, visit status distribution)
3. **Overview + Performance section** — `overview_data`, `performance`, monitoring session metadata

Each section is freed from server memory immediately after sending (`del` + `gc.collect()`). The frontend accumulates sections in `sseSectionsRef.current` and merges them when the final `"Complete!"` event arrives:

```javascript
es.onmessage = function (event) {
  var parsed = JSON.parse(event.data);
  if (parsed.data_section) {
    Object.assign(sseSectionsRef.current, parsed.data_section);
  }
  if (parsed.message === 'Complete!' && parsed.data) {
    var fullData = Object.assign({}, sseSectionsRef.current, parsed.data);
    sseSectionsRef.current = {};
    setDashData(fullData);
  }
};
```

#### Additional OOM Optimizations

- **Stream-parse-and-store**: CSV downloads go to a temp file (0 bytes in Python), parsed in 1000-row chunks, stored to DB immediately. Peak memory: ~50 MB instead of ~2 GB.
- **Intermediate structure freeing**: `del` statements release large dicts after each SSE section is sent.
- **`skip_raw_store=True`**: Prevents slim dicts (no form_json) from overwriting full form_json already stored to DB during streaming.
- **GPS visits excluded from SSE**: `serialize_flw_summary()` does NOT include a `visits` key. GPS visit details are lazy-loaded via `/api/gps/<username>/` when the user drills down.

### Dashboard Data Snapshotting

On subsequent page opens, the frontend checks for a snapshot first via `GET /api/snapshot/?run_id=X&opportunity_id=Y` and renders immediately if found, skipping SSE streaming entirely.

**Saving snapshots:**

- **Manual**: A "Save Snapshot" button in the tab bar triggers `POST /api/save-snapshot/`. The backend calls `_rebuild_gps_with_visits()` to embed GPS visit details from `ComputedVisitCache` (local SQL, not a pipeline re-run), then saves to `run.data["snapshot"]`.
- **Auto on Complete**: When the user clicks "Complete Audit", a snapshot is auto-saved before marking the run complete.

**`_rebuild_gps_with_visits()` (views.py):** Queries the `ComputedVisitCache` via `SQLCacheManager` to re-read GPS visits from the local SQL computed cache. This is a fast local DB query (~20s) instead of a full pipeline re-download (~8 min). If the computed cache is cold/expired, it skips visit embedding — the frontend falls back to API lazy-loading on drill-down.

**Lifecycle-based size management:**

- **In-progress runs**: Full snapshot including `followup_data.flw_drilldown` (~2.2MB for 100 FLWs). Assessors need drill-down access while working.
- **Completed runs**: On completion, `flw_drilldown` is stripped from the snapshot (~200KB). Historical reference only needs summary tables.

**Data freshness indicator:** The tab bar always shows "Data from: \<timestamp\>" with a colored badge:

- **(live)** in green — data came from the SSE stream (fresh)
- **(snapshot)** in amber — data loaded from or saved as a snapshot

**Completed audit behavior:**

- "Save Snapshot" and "Refresh Data" buttons are hidden when `isCompleted` (i.e., `instance.status === 'completed'`)
- "Complete Audit" button is replaced with a "Completed" badge

**Storage**: Snapshot is stored at `run.data["snapshot"]`, a sibling to `run.data["state"]`, so `update_run_state()` shallow-merge won't interfere.

### Two-Source Visit Architecture

The follow-up rate calculation relies on two fundamentally different data sources that are merged at computation time:

| Data Layer                                   | Source                                            | What It Provides                                                                                                | Granularity                                          |
| -------------------------------------------- | ------------------------------------------------- | --------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------- |
| **Completed visits**                         | Connect API via AnalysisPipeline                  | Form submissions forwarded from CCHQ (ANC Visit, PNC Visit, 1 Week, etc.) with GPS, timestamps, case IDs        | 1 form submission = 1 VisitRow                       |
| **Expected visits** (scheduled, due, missed) | CCHQ Form API (registration forms)                | Visit schedules extracted from `var_visit_1..6` blocks: visit type, scheduled date, expiry date, mother_case_id | 1 registration form = up to 6 expected visit records |
| **Mother metadata**                          | CCHQ Form API (registration forms)                | Name, phone, age, household size, eligibility, EDD, preferred visit time                                        | 1 registration form = 1 mother record                |
| **GS scores**                                | CCHQ Form API (GS forms, separate supervisor app) | Gold Standard assessment scores per FLW                                                                         | 1 GS form = 1 FLW score                              |

**Key insight**: The pipeline (via Connect API) contains **only completed visits** - actual form submissions. It has no knowledge of what visits _should_ exist. The expected visit schedules come entirely from CCHQ registration forms (`var_visit_1..6`). These registration forms are not available through the Connect API, so they are fetched directly from the CCHQ Form API.

**Merge logic** (in `build_followup_from_pipeline()` and `followup_analysis.py`):

1. Registration forms (from CCHQ) provide the expected visits with scheduled dates and expiry dates
2. Pipeline rows (from Connect API) provide completed form submissions with timestamps
3. For each expected visit, the system checks if a matching completed visit exists (via `COMPLETION_FLAGS` + `FORM_NAME_TO_VISIT_TYPE` normalization)
4. Unmatched expected visits become "due" or "missed" depending on the current date vs expiry date

### Data Payload Structure

The final SSE payload (`data.data`) contains:

```json
{
  "success": true,
  "opportunity_id": 123,
  "opportunity_name": "MBW Nigeria",
  "from_cache": true,
  "gps_data": {
    "total_visits": 500,
    "total_flagged": 12,
    "date_range_start": "2025-01-01",
    "date_range_end": "2025-01-31",
    "flw_summaries": [...],  // Each summary includes cases_with_revisits and median_meters_per_visit
    "all_coordinates": [...]  // Array of {lat, lng, username, entity, date, flagged} for aggregate map
  },
  "followup_data": {
    "total_cases": 300,
    "flw_summaries": [...],
    "flw_drilldown": {
      "flw001": [
        {
          "mother_case_id": "abc123",
          "mother_name": "Fatima Ibrahim",
          "eligible": true,
          "completed": 4,
          "total": 5,
          "follow_up_rate": 80,
          "visits": [...]
        }
      ]
    }
  },
  "overview_data": {
    "flw_summaries": [...],
    "visit_status_distribution": {
      "completed_on_time": 150,
      "completed_late": 30,
      "due_on_time": 20,
      "due_late": 15,
      "missed": 5,
      "total": 220
    }
  },
  "active_usernames": ["flw001", "flw002"],
  "flw_names": {"flw001": "Alice Mensah", "flw002": "Bob Kone"},
  "open_task_usernames": ["flw002"],
  "performance_data": [
    {"status": "eligible_for_renewal", "num_flws": 5, "total_cases": 200, "eligible_at_registration": 180, "still_eligible": 150, "pct_still_eligible": 83.3, ...},
    {"status": "probation", ...},
    {"status": "suspended", ...},
    {"status": "none", ...}
  ],
  "monitoring_session": { "id": 1, "title": "...", "status": "in_progress", "worker_results": {...} }
}
```

---

## Four-Tab Dashboard

### Overview Tab

Provides a bird's-eye view of each FLW's performance by merging data from all sources.

**Summary Card**: Visit Status Distribution (per-visit-type stacked bar chart)

- 6 vertical stacked bars, one per visit type: ANC, Postnatal, Week 1, Month 1, Month 3, Month 6
- Stacking order (bottom to top): Completed On Time (#22c55e), Completed Late (#86efac), Due On Time (#facc15), Due Late (#fb923c), Missed (#ef4444), Not Due Yet (#9ca3af grey)
- "Not Due Yet" category: visits whose scheduled date is in the future
- Interactive legend below the chart: click categories to toggle visibility on/off (dimmed + strike-through when hidden)
- Bar heights proportional to count (tallest bar = full height, others scaled)

**FLW Table Columns** (14 columns, toggleable via Column Selector):

| Column         | Data Source                   | Description                                                                                                                                                                 |
| -------------- | ----------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| FLW Name       | Connect API                   | Display name with avatar (locked, always visible)                                                                                                                           |
| # Mothers      | Registration forms + pipeline | Total registered / eligible for full intervention bonus                                                                                                                     |
| GS Score       | CCHQ GS forms                 | First (oldest) Gold Standard Visit Checklist score. Color: green >=70, yellow 50-69, red <50                                                                                |
| Post-Test      | TBD                           | Post-test attempts (placeholder, shows "--")                                                                                                                                |
| Follow-up Rate | Follow-up analysis            | % of visits due 5+ days ago that are completed, among eligible mothers                                                                                                      |
| Eligible 5+    | Drill-down data               | Eligible mothers still on track (5+ completed OR <=1 missed). Color: green >=70%, yellow 50-69%, red <50%                                                                   |
| Revisit Dist.  | GPS analysis                  | Median haversine distance (km) between revisits to the same mother, with "(N)" denominator showing cases with 2+ GPS visits                                                 |
| Meter/Visit    | GPS analysis                  | Median meters traveled per visit (configurable app version filter via Filter bar)                                                                                           |
| Minute/Visit   | GPS analysis                  | Median minutes per visit                                                                                                                                                    |
| Dist. Ratio    | GPS analysis                  | Revisit distance x 1000 / meter per visit. Higher values may indicate suspicious patterns                                                                                   |
| Phone Dup %    | Quality metrics               | % of mothers sharing duplicate phone numbers                                                                                                                                |
| ANC = PNC      | Quality metrics               | Count of mothers where ANC and PNC completion dates match                                                                                                                   |
| Parity         | Quality metrics               | Parity value concentration (% duplicate + mode)                                                                                                                             |
| Age            | Quality metrics               | Age value concentration (% duplicate + mode)                                                                                                                                |
| Age = Reg      | Quality metrics               | % of mothers where DOB month/day matches registration date                                                                                                                  |
| % EBF          | Pipeline (bf_status)          | % of FLW's postnatal visits reporting exclusive breastfeeding. Color: green 50-85%, yellow 31-49% or 86-95%, red 0-30% or 96-100%. Red flag in OCS prompt when in red zone. |
| Actions        | Action handlers               | Assessment buttons, notes, filter, task creation (locked, always visible)                                                                                                   |

**Column Selector**: Dropdown next to "FLW Overview" title showing N/17 visible columns. Toggle individual columns, "Show All", or "Minimal" presets.

**Actions per FLW** (Overview tab only - other tabs have Filter only):

- **Assessment buttons** (monitoring session only): Eligible for Renewal (green), Probation (yellow), Suspended (red) - toggle on click, stored in `worker_results`
- **Notes button**: Opens modal with assessment + notes for the FLW
- **Filter button**: Adds FLW to the multi-select filter
- **Task button**: Two modes depending on task state:
  - **No open task** (blue): Opens OCS modal to create a task + AI session
  - **Open task exists** (grey): Click expands an inline panel showing AI conversation preview + task management controls (status dropdown, save, discard, close task with outcome/resolution)

### GPS Analysis Tab

Identifies potential fraud or GPS anomalies by analyzing distances between consecutive visits to the same mother case.

**Summary Cards**: Total Visits, Flagged Visits, Date Range, Flag Threshold (5 km)

**FLW Table Columns** (all columns are sortable):

| Column            | Description                                                                                                                                         |
| ----------------- | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| FLW Name          | With avatar initial                                                                                                                                 |
| Total Visits      | Within date range                                                                                                                                   |
| With GPS          | Count + percentage                                                                                                                                  |
| Flagged           | Visits exceeding 5km threshold (highlighted red)                                                                                                    |
| Unique Cases      | Distinct mother_case_id count                                                                                                                       |
| Revisit Dist.     | Median haversine distance (km) between revisits to the same mother, with "(N)" denominator showing cases with 2+ GPS visits                         |
| Max Revisit Dist. | Maximum revisit distance (red if >5km)                                                                                                              |
| Meter/Visit       | Median haversine distance (m) between consecutive visits to different mothers on the same day. Color-coded: green >=1000m, yellow >=100m, red <100m |
| Dist. Ratio       | Revisit distance x 1000 / meter per visit. Higher values may indicate suspicious patterns (close revisits but far daily travel, or vice versa)      |
| Trailing 7 Days   | Sparkline bar chart of daily travel distance                                                                                                        |

**Aggregate Map**: A collapsible map at the top of the GPS tab showing all FLW visits with color-coded pins (HSL hue rotation per FLW). Uses MarkerCluster for performance with large datasets. Each popup shows FLW name, entity, date, and flagged status. Collapsed by default — click to expand. A legend below the map shows the FLW-to-color mapping.

**Actions per FLW**: Filter button, Details drill-down button (no assessment or task buttons)

**Drill-Down**: Clicking "Details" on a FLW row expands an inline panel showing individual visit records with date, form name, entity, GPS coordinates, revisit distance (haversine distance from previous visit to the same mother), and flagged status.

### Follow-Up Rate Tab

Tracks visit completion across 6 visit types with per-mother granularity, eligibility filtering, and grace period.

**Summary Cards**: Total Visit Cases, Total FLWs, Average Follow-up Rate (color-coded: green >=80%, yellow >=60%, red <60%)

**FLW Table Columns**:

| Column              | Description                                                                     |
| ------------------- | ------------------------------------------------------------------------------- |
| FLW Name            | Color-coded avatar (green/yellow/red based on follow-up rate)                   |
| Follow-up Rate      | Progress bar + percentage (business definition: eligible mothers, 5+ day grace) |
| Completed           | Total completed visits with percentage                                          |
| Due                 | Due visits (on-time + late only, excludes completed and missed)                 |
| Missed              | Missed visits count                                                             |
| ANC through Month 6 | Per-visit-type breakdown showing completed/due/missed counts in mini columns    |

**Eligibility Filter**: "Full intervention bonus only" checkbox (default checked). When checked, follow-up rate only counts mothers with `eligible_full_intervention_bonus = "1"`.

**Drill-Down**: Clicking a FLW row expands to show per-mother visit details with metadata (name, age, phone, registration date, household size, preferred visit time, ANC/PNC dates, EDD, baby DOB, eligibility).

### FLW Performance Tab

Aggregated case metrics grouped by each FLW's latest known assessment status. Computed by `compute_flw_performance_by_status()` in `followup_analysis.py`.

**Status Groups**: Eligible for Renewal, Probation, Suspended, No Category (FLWs without any assessment)

**Status Source**: FLW assessment statuses are retrieved from both audit sessions (`AuditDataAccess.get_audit_sessions()`) and all workflow monitoring runs (`WorkflowDataAccess.list_runs()`), matching the logic in `flw_api._build_flw_history()`. The latest status by date is used.

**Table Columns**:

| Column              | Description                                                                                   |
| ------------------- | --------------------------------------------------------------------------------------------- |
| Status              | Assessment status category (color-coded chip)                                                 |
| # FLWs              | Number of FLWs in this status group                                                           |
| Total Cases         | Total registered mothers across all FLWs in the group                                         |
| Eligible at Reg     | Mothers marked eligible for full intervention bonus at registration                           |
| Still Eligible      | Mothers with 5+ completed visits OR <=1 missed visits                                         |
| % Still Eligible    | Still Eligible / Eligible at Reg (color: green >=85%, yellow 50-84%, red <50%)                |
| % <=1 Missed        | Eligible cases with 0 or 1 missed visits / eligible cases                                     |
| % 4 Visits On Track | Eligible cases with 3+ completed visits among those whose Month 1 visit is due (5-day buffer) |
| % 5 Visits Complete | Eligible cases with 4+ completed visits among those whose Month 3 visit is due (5-day buffer) |
| % 6 Visits Complete | Eligible cases with 5+ completed visits among those whose Month 6 visit is due (5-day buffer) |

**Totals Row**: Aggregated totals across all status groups.

**Data in Snapshot**: Performance data is included in the dashboard snapshot (`performance_data` key) and restored from it on subsequent loads.

---

## Data Sources & APIs

### Connect Production API

Used for: Visit form data, FLW names, opportunity metadata

- **Visit forms**: Fetched via `AnalysisPipeline` using `MBW_GPS_PIPELINE_CONFIG` from `pipeline_config.py`. Extracts 13 fields per visit using FieldComputations (all path-based; `gps_location` uses a two-path COALESCE for dict/string handling).
- **FLW names**: `fetch_flw_names()` from `labs/analysis/data_access.py`
- **Opportunity metadata**: `GET /export/opportunity/{id}/` -> extracts `cc_domain` and `cc_app_id` from `deliver_app` or `learn_app`

Authentication: Connect OAuth token from `request.session["labs_oauth"]`

### CommCare HQ Form API v1

Used for: Registration forms (expected visit schedules + mother metadata) and Gold Standard Visit Checklist forms (GS scores). These forms are not available through the Connect API and must be fetched directly from CCHQ.

- **Registration forms**: `fetch_registration_forms()` in `data_fetchers.py`
  - Dynamically discovers xmlns for "Register Mother" via Application Structure API
  - Endpoint: `GET /a/{domain}/api/form/v1/?xmlns={xmlns}`
  - Extracts expected visit schedules and mother metadata
- **Gold Standard forms**: `fetch_gs_forms()` in `data_fetchers.py`
  - GS form lives in a separate supervisor app (not the deliver app)
  - GS App ID is configurable in the FLW selection view (default: `2ca67a89dd8a2209d75ed5599b45a5d1`)
  - `gs_app_id` stored in run state and passed to `fetch_gs_forms()`
  - If the configured GS app's xmlns cannot be found, returns empty (no fallback to download all forms)

Authentication: CommCare OAuth token from `request.session["commcare_oauth"]`

### CommCare HQ Application Structure API

Used for: Dynamic xmlns discovery

- **Single app**: `GET /a/{domain}/api/application/v1/{app_id}/` -> walks `modules[].forms[]` matching by multilingual name dict
- **All apps**: `GET /a/{domain}/api/application/v1/` -> paginated listing of all apps in the domain

### Data Relationships

```text
Pipeline Visit Forms (Connect API)
    |
    +-- username ----------> FLW Names (Connect API)
    +-- GPS coordinates --> GPS Analysis (Haversine, meter/visit, min/visit, dist. ratio, aggregate map)
    +-- form_name ---------> Visit type normalization (FORM_NAME_TO_VISIT_TYPE)
    +-- mother_case_id ----> Mother-to-FLW mapping
    +-- parity ------------> Quality metrics (from ANC Visit rows)
    +-- anc/pnc dates ----> Quality metrics + drill-down metadata
    +-- baby_dob ----------> Drill-down metadata (from Post delivery visit rows)

Registration Forms (CCHQ Form API)
    |
    +-- var_visit_1..6 ----> Expected visit schedules (type, dates, mother_case_id)
    +-- mother_details ----> Mother metadata (name, phone, age/DOB)
    +-- eligible_full_intervention_bonus --> Eligibility filtering
    +-- metadata.username --> FLW-to-mother mapping

Gold Standard Forms (CCHQ Form API, separate supervisor app)
    |
    +-- load_flw_connect_id --> Maps to FLW username (assessed FLW)
    +-- checklist_percentage -> GS score (0-100)
    +-- meta.timeEnd --------> Sorting (oldest first)
```

---

## Pipeline Configuration

The `MBW_GPS_PIPELINE_CONFIG` in `pipeline_config.py` defines 13 FieldComputations for visit-level data extraction:

| Name                  | Type           | Path / Extractor                                                                    | Notes                                                                                                |
| --------------------- | -------------- | ----------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------- |
| `gps_location`        | paths          | `form.meta.location.#text`, `form.meta.location`                                    | COALESCE: dict `#text` key or string fallback                                                        |
| `case_id`             | path           | `form.case.@case_id`                                                                |                                                                                                      |
| `mother_case_id`      | path           | `form.parents.parent.case.@case_id`                                                 |                                                                                                      |
| `form_name`           | path           | `form.@name`                                                                        | Has trailing space variant ("ANC Visit ")                                                            |
| `visit_datetime`      | path           | `form.meta.timeEnd`                                                                 | ISO datetime string                                                                                  |
| `entity_id_deliver`   | paths          | `form.mbw_visit.deliver.entity_id` (+ alt)                                          |                                                                                                      |
| `entity_name`         | paths          | `form.mbw_visit.deliver.entity_name` (+ alt)                                        |                                                                                                      |
| `parity`              | path           | `form.confirm_visit_information.parity__of_...`                                     | From ANC forms only                                                                                  |
| `anc_completion_date` | path           | `form.visit_completion.anc_completion_date`                                         | From ANC forms only                                                                                  |
| `pnc_completion_date` | path           | `form.pnc_completion_date`                                                          | From PNC forms only                                                                                  |
| `baby_dob`            | path           | `form.capture_the_following_birth_details.baby_dob`                                 | From PNC forms only                                                                                  |
| `app_build_version`   | path+transform | `form.meta.app_build_version` via `_safe_parse_int`                                 | Integer; SQL: regex-guarded `::INTEGER` cast                                                         |
| `bf_status`           | paths          | `form.feeding_history.{pnc,oneweek,onemonth,threemonth,sixmonth}_current_bf_status` | Multi-choice, space-separated; "ebf" = exclusive breastfeeding. From postnatal forms only (not ANC). |

**Important**: `gps_location` uses a two-path COALESCE (`form.meta.location.#text`, `form.meta.location`) because CommCare's XML-to-JSON conversion produces either a dict (`{"#text": "lat lon alt acc", ...}`) or a plain string for `form.meta.location`. The COALESCE extracts `#text` from the dict case, falling back to the string case — all in SQL, no Python post-processing. All 13 fields are now path-based; no extractors remain.

---

## Follow-Up Rate Business Logic

### Business Definition

**Follow-up rate** = % of visits due 5+ days ago that have been completed, among mothers marked as eligible for full intervention bonus at registration.

### Key Constants

```python
GRACE_PERIOD_DAYS = 5       # Only count visits due 5+ days ago
THRESHOLD_GREEN = 80        # Follow-up rate >=80% = green
THRESHOLD_YELLOW = 60       # Follow-up rate >=60% = yellow
```

### Eligibility Filtering

- `eligible_full_intervention_bonus` is extracted from registration form top-level field
- Value `"1"` = eligible, `"0"` = not eligible
- Non-eligible mothers show "Not eligible" badge in drill-down and "N/A" rate

### Visit Status Calculation

| Status              | Condition                                                  |
| ------------------- | ---------------------------------------------------------- |
| Completed - On Time | Completed within the on-time window (varies by visit type) |
| Completed - Late    | Completed after on-time window but before expiry           |
| Due - On Time       | Not completed, within on-time window                       |
| Due - Late          | Not completed, past on-time window but before expiry       |
| Missed              | Not completed, past expiry date                            |

#### On-Time Windows by Visit Type (MBW Schedule Spec)

| Visit Type                     | Scheduled Date                                    | On-Time Range                                         | Late Range                 | Expiry                   |
| ------------------------------ | ------------------------------------------------- | ----------------------------------------------------- | -------------------------- | ------------------------ |
| ANC Visit                      | 28 weeks (or today if >=28 weeks at registration) | Within 7 days of scheduled date                       | >7 days, before delivery   | After delivery           |
| PNC / Postnatal Delivery Visit | EDD (expected delivery date)                      | Delivery through **4 days** post delivery             | 5-6 days after delivery    | 7+ days after delivery   |
| 1 Week Visit                   | 7 days after delivery                             | 7-13 days after delivery (within 7 days of scheduled) | 14-29 days after delivery  | 30+ days after delivery  |
| 1 Month Visit                  | 30 days after delivery                            | 30-36 days (within 7 days of scheduled)               | 37-89 days after delivery  | 90+ days after delivery  |
| 3 Month Visit                  | 90 days after delivery                            | 90-96 days (within 7 days of scheduled)               | 97-179 days after delivery | 180+ days after delivery |
| 6 Month Visit                  | 180 days after delivery                           | 180-186 days (within 7 days of scheduled)             | —                          | —                        |

Configured via `VISIT_ON_TIME_DAYS` mapping in `followup_analysis.py`. Expiry dates come from CommCare case properties (`visit_expiry_date`). Reschedule window: PNC = 3 days later, all others = 7 days later.

### Key Mappings

```python
COMPLETION_FLAGS = {
    "ANC Visit": "antenatal_visit_completion",
    "Postnatal Visit": "postnatal_visit_completion",
    "Postnatal Delivery Visit": "postnatal_visit_completion",
    "1 Week Visit": "one_two_week_visit_completion",
    "1 Month Visit": "one_month_visit_completion",
    "3 Month Visit": "three_month_visit_completion",
    "6 Month Visit": "six_month_visit_completion",
}

FORM_NAME_TO_VISIT_TYPE = {
    "ANC Visit": "ANC Visit",
    "ANC Visit ": "ANC Visit",       # trailing space variant
    "Post delivery visit": "Postnatal Delivery Visit",
    "1 Week Visit": "1 Week Visit",
    "1 Month Visit": "1 Month Visit",
    "3 Month Visit": "3 Month Visit",
    "6 Month Visit": "6 Month Visit",
}
```

---

## Quality Metrics

Computed per FLW in `compute_overview_quality_metrics()` from `followup_analysis.py`:

| Metric               | Description                                                       | Fraud Signal                                       |
| -------------------- | ----------------------------------------------------------------- | -------------------------------------------------- |
| Phone Dup %          | % of mothers sharing duplicate phone numbers                      | High % = possible fabrication                      |
| ANC = PNC            | Count of mothers where ANC and PNC completion dates are identical | Same-day = suspicious                              |
| Parity Concentration | % of parity values appearing more than once + mode value          | High concentration = possible data copying         |
| Age Concentration    | % of age values appearing more than once + mode value             | High concentration = possible data copying         |
| Age = Reg %          | % of mothers where DOB month/day matches registration month/day   | Suggests DOB was fabricated from registration date |

---

## Action Handler System

All user actions flow through the `ActionHandlers` interface, created by `createActionHandlers(csrfToken)` in `workflow-runner.tsx`. The render code calls these via the `actions` prop.

### All 20 Action Handlers

| #   | Action                 | Endpoint                                              | Purpose                                                                          |
| --- | ---------------------- | ----------------------------------------------------- | -------------------------------------------------------------------------------- |
| 1   | `createTask`           | `POST /tasks/api/single-create/`                      | Create a task with username, title, description                                  |
| 2   | `checkOCSStatus`       | `GET /labs/workflow/api/ocs/status/`                  | Check if OCS OAuth is configured                                                 |
| 3   | `listOCSBots`          | `GET /labs/workflow/api/ocs/bots/`                    | List available OCS experiments                                                   |
| 4   | `initiateOCSSession`   | `POST /tasks/{taskId}/ai/initiate/`                   | Start OCS conversation on a task                                                 |
| 5   | `createTaskWithOCS`    | Combines #1 + #4                                      | Create task + start OCS session                                                  |
| 6   | `startJob`             | `POST /labs/workflow/api/run/{runId}/job/start/`      | Start async Celery job                                                           |
| 7   | `cancelJob`            | `POST /labs/workflow/api/job/{taskId}/cancel/`        | Cancel running Celery task                                                       |
| 8   | `deleteRun`            | `POST /labs/workflow/api/run/{runId}/delete/`         | Delete workflow run                                                              |
| 9   | `streamJobProgress`    | `EventSource /labs/workflow/api/job/{taskId}/status/` | SSE stream for job progress                                                      |
| 10  | `createAudit`          | `POST /audit/api/audit/create-async/`                 | Create async audit task                                                          |
| 11  | `getAuditStatus`       | `GET /audit/api/audit/task/{taskId}/status/`          | Poll audit task status                                                           |
| 12  | `streamAuditProgress`  | `EventSource /audit/api/audit/task/{taskId}/stream/`  | SSE stream for audit progress                                                    |
| 13  | `cancelAudit`          | `POST /audit/api/audit/task/{taskId}/cancel/`         | Cancel audit creation                                                            |
| 14  | **`saveWorkerResult`** | `POST /labs/workflow/api/run/{runId}/worker-result/`  | Save FLW assessment (MBW-specific)                                               |
| 15  | **`completeRun`**      | `POST /labs/workflow/api/run/{runId}/complete/`       | Mark run completed (MBW-specific)                                                |
| 16  | **`openTaskCreator`**  | Opens `/tasks/new/?{params}`                          | Open task creation in new window (replaced by in-page OCS modal in MBW template) |
| 17  | `getTaskDetail`        | `GET /tasks/api/{taskId}/`                            | Fetch task data as JSON (generic, reusable)                                      |
| 18  | `getAITranscript`      | `GET /tasks/{taskId}/ai/transcript/`                  | Fetch AI conversation messages (generic, reusable)                               |
| 19  | `updateTask`           | `POST /tasks/api/{taskId}/update/`                    | Update task fields (status, resolution, etc.) (generic, reusable)                |
| 20  | `saveAITranscript`     | `POST /tasks/{taskId}/ai/save-transcript/`            | Save AI transcript to task events (generic, reusable)                            |

### MBW-Specific Action Details

**saveWorkerResult**:

```javascript
// Request
actions.saveWorkerResult(instance.id, {
    username: "flw@domain.com",
    result: "eligible_for_renewal",  // or "probation", "suspended", null
    notes: "Good performance"
})

// Response
{
    "success": true,
    "worker_results": { "flw@domain.com": { "result": "eligible_for_renewal", "notes": "...", "assessed_at": "..." } },
    "progress": { "percentage": 50, "assessed": 5, "total": 10 }
}
```

**Assessment button behavior**:

- Buttons are **toggles**: clicking an active status clears it (sends `result: null`)
- **Optimistic UI**: button state updates instantly before the API responds; reverts on error
- Only `saveWorkerResult` is called per click — `onUpdateState` is NOT called (the backend already persists the state, so the second POST to `/state/` is unnecessary)
- Notes are preserved when toggling status

**completeRun**:

```javascript
// Request
actions.completeRun(instance.id, {
    overall_result: "completed",
    notes: "All FLWs assessed"
})

// Response
{ "success": true, "status": "completed", "overall_result": "completed" }
```

---

## Workflow Data Model

All workflow data is persisted via the **LabsRecord API** (no local database). Records are stored on the Connect production server.

### WorkflowDefinitionRecord

```python
{
    "id": int,
    "name": "MBW Monitoring",
    "description": str,
    "version": 1,
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"}
    ],
    "config": {"showSummaryCards": False, "showFilters": False, "templateType": "mbw_monitoring"},
    "pipeline_sources": [],  # MBW uses SSE, not pipelines
    "is_shared": bool,
    "shared_scope": "global" | "organization" | "program"
}
```

### WorkflowRunRecord

```python
{
    "id": int,
    "definition_id": int,
    "opportunity_id": int,
    "status": "in_progress" | "completed",
    "created_at": "2026-02-17T14:30:00.123456",  # ISO timestamp, added at creation time
    "state": {
        # FLW Selection
        "selected_workers": ["flw1@domain", "flw2@domain"],
        "selected_flws": ["flw1@domain", "flw2@domain"],  # Backwards compat key

        # Worker Assessment Results
        "worker_results": {
            "flw1@domain": {
                "result": "eligible_for_renewal",  # or "probation" or "suspended" or null
                "notes": "Performer exceeded targets",
                "assessed_by": user_id,
                "assessed_at": "2026-02-17T10:30:00Z"
            }
        },
        "flw_results": { ... },  # Legacy key name

        # Session metadata
        "title": "MBW Monitoring - QA Round 4",
        "tag": "qa4",

        # App version filter (GPS only)
        "app_version_op": "gt",  # "gt", "gte", "eq", "lte", "lt", or "" (no filter)
        "app_version_val": "14"  # Version number string, or "" (no filter)
    },
    "data": {
        # Dashboard data snapshot (saved after SSE load, stripped on completion)
        "snapshot": {
            "timestamp": "2026-02-17T14:30:00Z",
            "gps_data": {...},
            "followup_data": {...},  # flw_drilldown included while in-progress, removed on completion
            "overview_data": {...},
            "active_usernames": [...],
            "flw_names": {...},
            "open_task_usernames": [...],
            "open_tasks": {"flw@domain": {"task_id": 123, "status": "investigating", "title": "..."}}
        }
    }
}
```

### WorkflowRenderCodeRecord

```python
{
    "id": int,
    "definition_id": int,
    "component_code": str,  # Full RENDER_CODE JSX string (~1860 lines)
    "version": int
}
```

### FLW Assessment Values

| Status               | Value                  | Color  | Meaning                                                     |
| -------------------- | ---------------------- | ------ | ----------------------------------------------------------- |
| (No assessment)      | `null`                 | -      | Not yet assessed                                            |
| Eligible for Renewal | `eligible_for_renewal` | Green  | Good performance                                            |
| Probation            | `probation`            | Yellow | Poor performance, not eligible for renewal                  |
| Suspended            | `suspended`            | Red    | Strong evidence of fraud or severe performance deficiencies |

Note: "Suspended" is a **label only** - it does NOT trigger any action on Connect.

---

## Caching Strategy

### Cache Layers

| Layer                | What                                                     | Key Pattern                       | TTL                            | Scope                                                                                   |
| -------------------- | -------------------------------------------------------- | --------------------------------- | ------------------------------ | --------------------------------------------------------------------------------------- |
| Pipeline Cache       | Processed visit form data                                | Config hash-based                 | Configurable                   | Per opportunity + config hash                                                           |
| Registration Forms   | CCHQ registration forms                                  | `mbw_registration_forms:{domain}` | 1hr                            | Per domain                                                                              |
| GS Forms             | CCHQ Gold Standard forms                                 | `mbw_gs_forms:{domain}`           | 1hr                            | Per domain                                                                              |
| Metadata Cache       | Opportunity metadata                                     | `mbw_opp_metadata:{opp_id}`       | 1hr                            | Per opportunity_id                                                                      |
| HQ Case Cache        | Visit + mother cases                                     | `mbw_visit_cases:{domain}`        | 1hr prod / 24hr dev            | Per domain                                                                              |
| Computed Visit Cache | Visit-level computed fields (GPS, case IDs, dates, etc.) | `opportunity_id` + `config_hash`  | Configurable TTL               | Keyed by opportunity_id + config_hash; username as secondary index for filtered queries |
| Dashboard Snapshot   | Computed dashboard metrics                               | `run.data["snapshot"]`            | Permanent (updated on refresh) | Per run                                                                                 |

### Filter-Aware Caching

The pipeline config hash **excludes filters** (via `get_config_hash()` in `utils.py`), so one cached dataset serves multiple filtered queries. When a config has filters (e.g., `status_filter`), the cache tolerance check uses `expected_count=0`, accepting any non-expired cache. This means filter changes reuse existing cached data instantly — only the SQL `WHERE` clause changes. The `expires_at` TTL still guards against stale data. Force refresh (`?refresh=1`) bypasses the cache entirely regardless of filters.

### Tolerance-Based Cache Validation

HQ case caches use a 3-tier validation system (implemented in `_validate_hq_cache()`):

1. **Count check**: If cached case count >= requested count -> valid
2. **Percentage tolerance**: If cached/requested ratio >= threshold -> valid
3. **Time tolerance**: If cache age <= time threshold -> valid

| Mode                              | % Tolerance | Time Tolerance | Redis TTL |
| --------------------------------- | ----------- | -------------- | --------- |
| Production                        | 98%         | 30 minutes     | 1 hour    |
| Dev Fixture (`MBW_DEV_FIXTURE=1`) | 85%         | 90 minutes     | 24 hours  |

---

## Authentication & OAuth

### Triple OAuth Requirement

The dashboard uses up to three OAuth tokens:

1. **Connect OAuth** (`labs_oauth` in session): For accessing Connect Production API (visit data, FLW names, metadata)
2. **CommCare OAuth** (`commcare_oauth` in session): For accessing CommCare HQ APIs (Form API, Application API)
3. **OCS OAuth** (`ocs_oauth` in session): For AI task creation via Open Chat Studio (optional)

### Pre-Refresh OAuth Expiration Check

Before starting an SSE data stream, the frontend calls `GET /custom_analysis/mbw_monitoring/api/oauth-status/` to check if Connect and CommCare HQ tokens are still active. If either is expired, a red warning banner is shown with "Authorize" buttons linking to the OAuth initiate endpoints. The `next` parameter uses `window.location.pathname + window.location.search` (relative path) to redirect back after authorization.

This check is skipped when loading from a snapshot (no OAuth needed for cached data). OCS token expiry is displayed but not blocking (OCS is optional).

**Endpoint**: `MBWOAuthStatusView` in `views.py` — returns `{connect: {active}, commcare: {active, authorize_url}, ocs: {active, authorize_url}}`

### Automatic Token Refresh

The `CommCareDataAccess` client automatically refreshes expired tokens:

1. `check_token_valid()` compares `expires_at` against current time
2. If expired, calls `_refresh_token()` which POSTs to `/oauth/token/` with `grant_type=refresh_token`
3. On success, updates both instance state and session storage
4. On failure, returns `False` -> caller raises `ValueError` prompting re-authorization

---

## Frontend Architecture

### Technology Stack

| Component        | Technology                                         | Purpose                          |
| ---------------- | -------------------------------------------------- | -------------------------------- |
| UI Rendering     | React (via Babel standalone)                       | Component rendering, virtual DOM |
| State Management | React hooks (useState, useEffect, useMemo, useRef) | Reactive state                   |
| Styling          | Tailwind CSS (utility classes) + inline styles     | Layout and appearance            |
| Data Loading     | Server-Sent Events (EventSource)                   | Real-time streaming from backend |
| API Calls        | Fetch API                                          | Action handler requests          |
| Transpilation    | @babel/standalone                                  | JSX -> JS conversion in browser  |

### Render Code Constraints

Since the render code is a string transpiled by Babel in the browser:

1. **No imports** - only `React` is available as a global
2. **Use `var`** for all declarations (not `const`/`let`) to avoid Babel scoping issues
3. **No ES modules** - no `import`/`export` syntax
4. **Must define `WorkflowUI`** - the component function name that gets returned
5. **React.useState** etc. (not destructured `useState`) since no import
6. **Inline styles for dynamic values** - Tailwind classes work but dynamic CSS needs `style={{}}`
7. **No TypeScript** - plain JavaScript only

### Filtering

The dashboard uses two types of filters:

#### Server-Side Filters (trigger SSE reload)

These filters modify the SSE stream URL and cause a new server-side query:

- **Visit Status filter**: Filters by Connect visit approval status (Approved, Pending, Rejected, Over Limit). Default: Approved only. Applied server-side via pipeline SQL `WHERE status IN (...)`. Changing this filter triggers a new SSE stream but reuses the cached pipeline data (no re-download from Connect). State persisted in `sessionStorage` to survive OAuth redirects.
- **App Version filter** (GPS only): Operator (>, >=, =, <=, <) + version number. Default: > 14. Applied server-side to GPS visit data.

Both require clicking "Apply" to take effect. "Reset" restores defaults (Approved only, > 14).

#### Client-Side Filters (no API calls)

These filters happen entirely in the browser:

- **FLW filter**: Multi-select listbox. Filters all three tabs by `username` set membership
- **Mother filter**: Multi-select listbox (populated from drilldown data). Filters follow-up tab
- **Date filter**: Start/end date inputs affect GPS data only
- **Eligibility checkbox**: Toggles follow-up rate eligibility filtering
- **Column selector**: Toggle individual overview columns, "Show All" / "Minimal" presets

### Sorting

Each table has independent sort state. Clicking a column header toggles ascending/descending. Numeric columns sort numerically; string columns sort alphabetically.

### Sticky Table Headers

Table headers freeze below the Connect Labs header bar (64px) when scrolling long tables. Implemented via JavaScript (not CSS `position: sticky`) because Chrome doesn't support sticky on `<thead>`/`<th>` elements when ancestor containers have `overflow` or `border-collapse: collapse` (Tailwind preflight).

**Approach**: A `useEffect` with a `scroll` event listener finds all `<table data-sticky-header>` elements, calculates each `<thead>`'s document offset via `offsetTop`/`offsetParent` chain, and applies `transform: translateY(offset)` when the header scrolls past 64px. The transform keeps the thead within the table's bounds, so `overflow: clip` on card wrappers doesn't clip it.

**Dependencies**: Re-runs on `[activeTab, sseComplete]` to detect tables in newly rendered tabs. The `data-sticky-header` attribute marks which tables participate.

### Table Horizontal Scrolling

The overview table uses a CSS pattern to enable horizontal scrolling within flex layouts:

```jsx
<div style={{ width: 0, minWidth: '100%', overflowX: 'auto', WebkitOverflowScrolling: 'touch' }}>
    <table style={{ width: 'max-content', minWidth: '100%' }}>
```

- `width: 0; minWidth: '100%'` prevents the table's intrinsic width from propagating up through flex item `min-width: auto` defaults
- `overflowX: 'auto'` creates a horizontal scrollbar when table exceeds container width

---

## Template Sync & Build Pipeline

### Template Sync Mechanism

When `RENDER_CODE` is updated in `template.py`, existing workflows in the DB still use the old version. Two mechanisms sync the template to the DB:

**URL Parameter**: Append `?sync=true` to the workflow run URL:

```text
/labs/workflow/<id>/run/?run_id=<id>&sync=true
```

For workflows whose name doesn't match the template name, use an explicit template key:

```text
/labs/workflow/<id>/run/?run_id=<id>&sync=true&template=mbw_monitoring
```

The view matches the definition name against registered templates and updates the render code.

**API Endpoint**: `POST /labs/workflow/api/<definition_id>/sync-template/`

```json
{ "template_key": "mbw_monitoring" }


// Optional, auto-detected from name
```

Both are **manual actions only** - never automatic. User-modified render code is preserved until explicitly synced.

### Static File Build Pipeline

After modifying TypeScript/JSX files, **both steps are required**:

```bash
# Step 1: Webpack builds to commcare_connect/static/bundles/js/
npm run dev

# Step 2: Django copies to staticfiles/ and updates manifest
python manage.py collectstatic --noinput
```

Django uses `CompressedManifestStaticFilesStorage` (whitenoise). The `{% static %}` template tag resolves filenames via `staticfiles/staticfiles.json` manifest, which maps original names to content-hashed versions. Without running `collectstatic`, the manifest points to the old bundle hash even after webpack produces a new one.

---

## Features & Capabilities

### Implemented Features

| Feature                       | Tab           | Description                                                                                                                                                                                                                                 |
| ----------------------------- | ------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Four-tab navigation           | All           | Overview, GPS Analysis, Follow-Up Rate, FLW Performance tabs                                                                                                                                                                                |
| SSE streaming with progress   | All           | Real-time loading messages during data loading                                                                                                                                                                                              |
| FLW filter (multi-select)     | All           | Filter by FLW name across all tabs                                                                                                                                                                                                          |
| Mother filter (multi-select)  | Follow-Up     | Filter by mother name                                                                                                                                                                                                                       |
| Column selector               | Overview      | Toggle 17 columns with Show All / Minimal presets                                                                                                                                                                                           |
| Column sorting                | All           | Click column headers to sort asc/desc (all GPS tab columns are now sortable)                                                                                                                                                                |
| Horizontal table scrolling    | Overview      | Scroll wrapper with `width: 0; minWidth: 100%` pattern                                                                                                                                                                                      |
| GPS drill-down                | GPS           | Click "Details" sets `expandedGps` state; `useEffect([expandedGps, dashData, instance.opportunity_id, appliedAppVersionOp, appliedAppVersionVal])` checks for embedded visits (snapshot) first, then lazy-loads from `/api/gps/<username>/` |
| Follow-up drill-down          | Follow-Up     | Per-mother visit details with metadata                                                                                                                                                                                                      |
| Visit status distribution     | Overview      | Per-visit-type stacked bar chart (6 bars) with toggleable legend and "Not Due Yet" category                                                                                                                                                 |
| Per-visit-type breakdown      | Follow-Up     | ANC through Month 6 mini columns                                                                                                                                                                                                            |
| Trailing 7-day sparkline      | GPS           | Daily travel distance bar chart                                                                                                                                                                                                             |
| GPS flag threshold (5km)      | GPS           | Red highlighting for suspicious distances                                                                                                                                                                                                   |
| Aggregate GPS map             | GPS           | Collapsible map at top of tab showing all FLW visits with color-coded pins (HSL hue rotation), MarkerCluster for performance, FLW legend below map                                                                                          |
| Meter/Visit column            | GPS, Overview | Median haversine distance (m) between consecutive visits to different mothers on same day. Color-coded: green >=1000m, yellow >=100m, red <100m                                                                                             |
| Dist. Ratio column            | GPS, Overview | Revisit distance x 1000 / meter per visit — higher values may indicate suspicious patterns                                                                                                                                                  |
| Revisit denominator           | GPS, Overview | Revisit Dist. now shows "(N)" where N = cases with 2+ GPS visits                                                                                                                                                                            |
| Follow-up rate (business def) | Follow-Up     | Eligibility + grace period filtered rate                                                                                                                                                                                                    |
| GS Score from CCHQ            | Overview      | First Gold Standard score from supervisor app                                                                                                                                                                                               |
| Quality/fraud metrics         | Overview      | Phone dup, parity/age concentration, ANC=PNC, age=reg                                                                                                                                                                                       |
| 3-option FLW assessment       | Overview      | Eligible for Renewal / Probation / Suspended with progress                                                                                                                                                                                  |
| Task creation                 | Overview      | Create task for FLW with OCS integration                                                                                                                                                                                                    |
| Inline task management        | Overview      | Expand FLW row to view AI conversation, update status, close task with outcome                                                                                                                                                              |
| Visit Status filter           | Filter Bar    | Server-side filter by Connect approval status (Approved, Pending, Rejected, Over Limit); default Approved only; reuses cached pipeline data on filter change; state persisted in `sessionStorage` to survive OAuth redirects                |
| App version filter (GPS)      | Filter Bar    | User-configurable operator (>, >=, =, <=, <) + version number for GPS data filtering; default > 14; persisted in run state                                                                                                                  |
| Template sync                 | -             | Sync render code from template.py to DB via `?sync=true`                                                                                                                                                                                    |
| Template registry             | -             | Auto-discovery of workflow templates                                                                                                                                                                                                        |
| Automatic token refresh       | Backend       | CommCare OAuth token auto-refreshed when expired                                                                                                                                                                                            |
| Cross-app xmlns discovery     | Backend       | GS form xmlns found by searching all apps in domain                                                                                                                                                                                         |
| Tolerance-based caching       | Backend       | 3-tier cache validation (count, percentage, time)                                                                                                                                                                                           |
| In-page OCS Task + AI modal   | Overview      | Auto-creates task with pre-filled fields, opens AI bot config with auto-populated prompt based on FLW performance data and red flag indicators                                                                                              |
| Dashboard data snapshotting   | All           | "Save Snapshot" button saves metrics + GPS visits (from ComputedVisitCache); instant reopen from snapshot; "Refresh Data" forces new SSE stream                                                                                             |
| Data freshness indicator      | All           | "Data from: \<time\> (live/snapshot)" in tab bar — green for SSE, amber for snapshot                                                                                                                                                        |
| Completed audit UX            | All           | Save Snapshot and Refresh Data buttons hidden on completed audits                                                                                                                                                                           |
| Sectioned SSE streaming       | Backend       | Data sent in 3 separate `data_section` events to prevent OOM; each freed after sending                                                                                                                                                      |
| Stream-parse-and-store        | Backend       | CSV downloads go to temp file, parsed in 1000-row chunks, stored to DB immediately; peak ~50 MB                                                                                                                                             |
| GS App ID configuration       | FLW Selection | Configurable Gold Standard app ID with default value; removed download-all-forms fallback                                                                                                                                                   |
| Connect ID column             | FLW Selection | Shows worker Connect ID in the FLW selection table                                                                                                                                                                                          |
| Clickable selection rows      | FLW Selection | Entire row toggles checkbox, not just the small checkbox                                                                                                                                                                                    |
| Workflow listing sort/filter  | Listing page  | Sort by name/date/latest run; filter by template type; runs sorted latest-first with Created and FLWs columns                                                                                                                               |
| Tasks pagination              | Tasks page    | Previous/Next controls when >50 tasks; preserves filter query params across pages                                                                                                                                                           |
| Case-insensitive usernames    | Backend       | All username comparisons normalized to lowercase (Connect vs CCHQ casing differences)                                                                                                                                                       |
| Optimistic assessment UI      | Overview      | Status buttons update instantly; revert on error. Toggle behavior (click active status to clear). 1 GET + 1 POST per click (down from 5 GETs + 2 POSTs)                                                                                     |
| FLW Performance tab           | Performance   | Aggregated case metrics grouped by FLW assessment status (Eligible/Probation/Suspended/None) with visit milestone tracking                                                                                                                  |
| Pre-refresh OAuth check       | All           | Checks token expiry before SSE stream; shows warning banner with authorize buttons if expired; skipped for snapshot loads                                                                                                                   |
| Sticky table headers          | All           | JS-based scroll handler using `transform: translateY()` pins `<thead>` at 64px below viewport top when scrolling long tables                                                                                                                |

### Placeholder / TBD Features

| Feature                          | Status   | Notes                                        |
| -------------------------------- | -------- | -------------------------------------------- |
| Post-Test attempts               | TBD      | Column present in overview table, shows "--" |
| User suspension (Connect action) | Disabled | "Suspended" is an assessment label only      |

---

## Configuration

### Environment Variables

| Variable                       | Purpose                             | Default                      |
| ------------------------------ | ----------------------------------- | ---------------------------- |
| `MBW_DEV_FIXTURE`              | Enable dev mode (relaxed caching)   | `False`                      |
| `COMMCARE_OAUTH_CLIENT_ID`     | CommCare OAuth client ID            | Required                     |
| `COMMCARE_OAUTH_CLIENT_SECRET` | CommCare OAuth client secret        | Required                     |
| `COMMCARE_HQ_URL`              | CommCare HQ base URL                | `https://www.commcarehq.org` |
| `CONNECT_PRODUCTION_URL`       | Connect production API URL          | Required                     |
| `OCS_URL`                      | Open Chat Studio URL (for AI tasks) | Optional                     |
| `OCS_OAUTH_CLIENT_ID`          | OCS OAuth client ID                 | Optional                     |
| `OCS_OAUTH_CLIENT_SECRET`      | OCS OAuth client secret             | Optional                     |

### Follow-Up Analysis Constants

| Constant            | Value              | Description                                                                 |
| ------------------- | ------------------ | --------------------------------------------------------------------------- |
| `GRACE_PERIOD_DAYS` | 5                  | Only count visits due 5+ days ago                                           |
| `THRESHOLD_GREEN`   | 80%                | Follow-up rate for green status                                             |
| `THRESHOLD_YELLOW`  | 60%                | Follow-up rate for yellow status                                            |
| On-time window      | 7 days (4 for PNC) | Days after scheduled date for on-time completion (see `VISIT_ON_TIME_DAYS`) |

**Visit status categories** (in `calculate_visit_status`): Completed On Time, Completed Late, Due On Time, Due Late, Missed, Not Due Yet (visits with `current_date < scheduled_date`).

**Aggregation** (`aggregate_visit_status_distribution`): Returns per-visit-type breakdown (ANC, Postnatal, Week 1, Month 1, Month 3, Month 6) with counts for each status category, plus overall totals.

### GPS Analysis Constants

| Constant       | Value       | Description                             |
| -------------- | ----------- | --------------------------------------- |
| Flag threshold | 5 km        | Distance above which a visit is flagged |
| Trailing days  | 7           | Number of days for the sparkline chart  |
| Earth radius   | 6,371,000 m | Used in Haversine calculation           |

---

## File Reference

### MBW Monitoring Module

All files under `commcare_connect/workflow/templates/mbw_monitoring/`:

| File                   | Purpose                                                                                                                                                                                     |
| ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `template.py`          | DEFINITION dict + RENDER_CODE JSX string (~1,860 lines) + TEMPLATE export                                                                                                                   |
| `__init__.py`          | Registers template in workflow template registry                                                                                                                                            |
| `views.py`             | SSE streaming endpoint + MBW API views (GPS detail, snapshot save/load, FLW results, session). Key functions: `_rebuild_gps_with_visits()` (ComputedVisitCache query for snapshot fidelity) |
| `data_fetchers.py`     | CCHQ form fetching (registration + GS), case fetching, caching                                                                                                                              |
| `followup_analysis.py` | Visit status calculation, per-FLW/per-mother aggregation, quality metrics                                                                                                                   |
| `gps_analysis.py`      | GPS metrics computation (Haversine, flagging, daily travel)                                                                                                                                 |
| `pipeline_config.py`   | MBW_GPS_PIPELINE_CONFIG (13 FieldComputations)                                                                                                                                              |
| `flw_api.py`           | FLW list endpoint with audit history enrichment                                                                                                                                             |
| `session_adapter.py`   | Monitoring session persistence (selected FLWs, results)                                                                                                                                     |
| `serializers.py`       | Data normalization for SSE payload                                                                                                                                                          |
| `gps_utils.py`         | Haversine distance calculation, GPS coordinate parsing                                                                                                                                      |
| `urls.py`              | URL routing for MBW-specific endpoints                                                                                                                                                      |

### Workflow Framework Files

| File                                              | Purpose                                                                                                                                                                |
| ------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `commcare_connect/workflow/views.py`              | WorkflowRunView, save_worker_result_api, complete_run_api, PipelineDataStreamView                                                                                      |
| `commcare_connect/workflow/urls.py`               | All workflow URL patterns (96 routes)                                                                                                                                  |
| `commcare_connect/workflow/data_access.py`        | WorkflowDataAccess (incl. save_run_snapshot()), PipelineDataAccess, proxy records. Properties: `template_type` on definitions, `created_at` + `selected_count` on runs |
| `commcare_connect/workflow/templates/__init__.py` | Template auto-discovery and registry                                                                                                                                   |
| `commcare_connect/templates/workflow/run.html`    | Django template (JSON script tag, bundle loading, overflow-x: clip for sticky header support)                                                                          |

### Frontend Files

| File                                             | Purpose                                                                      |
| ------------------------------------------------ | ---------------------------------------------------------------------------- |
| `commcare_connect/static/js/workflow-runner.tsx` | Entry point, createActionHandlers (20 handlers), React app bootstrap         |
| `components/workflow/DynamicWorkflow.tsx`        | Babel loading, JSX transpilation, error boundary, prop passing               |
| `components/workflow/types.ts`                   | TypeScript interfaces: WorkflowProps, ActionHandlers, WorkflowInstance, etc. |

### Task Module Files (used by inline task management)

| File                              | Purpose                                                                                      |
| --------------------------------- | -------------------------------------------------------------------------------------------- |
| `commcare_connect/tasks/views.py` | Task CRUD views, AI transcript, OCS integration. New: `task_detail_api` for JSON task detail |
| `commcare_connect/tasks/urls.py`  | Task URL routing. New: `api/<int:task_id>/` for task detail JSON API                         |

### Shared Dependencies

| File                                        | What's Reused                                                       |
| ------------------------------------------- | ------------------------------------------------------------------- |
| `labs/analysis/pipeline.py`                 | `AnalysisPipeline` - data fetching and caching facade               |
| `labs/analysis/sse_streaming.py`            | `BaseSSEStreamView`, `AnalysisPipelineSSEMixin`, `send_sse_event()` |
| `labs/analysis/data_access.py`              | `fetch_flw_names()`                                                 |
| `labs/integrations/commcare/api_client.py`  | `CommCareDataAccess` - CommCare HQ API client with OAuth            |
| `labs/integrations/commcare/oauth_views.py` | CommCare OAuth initiate/callback/logout views                       |

---

## Key Field Paths

### Registration Form (CCHQ)

| Field                  | Path                                                                                                       | Notes                               |
| ---------------------- | ---------------------------------------------------------------------------------------------------------- | ----------------------------------- |
| Mother name            | `form.mother_details.format_mother_name` (fallbacks: `mother_full_name`, `mother_name` + `mother_surname`) |                                     |
| Phone                  | `form.mother_details.phone_number` (fallback: `back_up_phone_number`)                                      |                                     |
| Age                    | Computed from `form.mother_details.mother_dob` (fallback: `age_in_years_rounded`, `mothers_age`)           |                                     |
| Household size         | `form.number_of_other_household_members`                                                                   | Top-level                           |
| Eligibility            | `form.eligible_full_intervention_bonus`                                                                    | Top-level, "1"/"0"                  |
| Expected delivery date | `form.mother_birth_outcome.expected_delivery_date`                                                         |                                     |
| Preferred visit time   | `form.var_visit_1.preferred_visit_time`                                                                    | Per-visit block                     |
| Mother case ID         | `form.var_visit_N.mother_case_id`                                                                          | First non-empty from var_visit_1..6 |

### Gold Standard Form (CCHQ, supervisor app)

| Field                   | Path                        | Notes                    |
| ----------------------- | --------------------------- | ------------------------ |
| Assessed FLW connect ID | `form.load_flw_connect_id`  | Maps to FLW username     |
| GS Score                | `form.checklist_percentage` | 0-100 integer            |
| Visit datetime          | `form.meta.timeEnd`         | For oldest-first sorting |

### Pipeline FieldComputation — Special Extraction

| Field               | Mode           | Source                                              | Notes                                         |
| ------------------- | -------------- | --------------------------------------------------- | --------------------------------------------- |
| `gps_location`      | paths          | `form.meta.location.#text`, `form.meta.location`    | COALESCE: dict `#text` key or string fallback |
| `visit_datetime`    | path           | `form.meta.timeEnd`                                 | Direct JSONB extraction                       |
| `app_build_version` | path+transform | `form.meta.app_build_version` via `_safe_parse_int` | SQL: regex-guarded `::INTEGER` cast           |

---

## Troubleshooting

### Action Buttons Not Working (Missing Action Handlers)

**Symptom**: Clicking assessment/task/complete buttons does nothing. Console shows fewer than 20 action keys.

**Cause**: After `npm run dev`, `collectstatic` was not run. Django's manifest still points to the old bundle.

**Fix**:

```bash
npm run dev && python manage.py collectstatic --noinput
```

### Table Extends Beyond Viewport (No Horizontal Scroll)

**Symptom**: With many columns, the table pushes the entire page wider. No scrollbar appears.

**Cause**: In a flex layout, flex items have `min-width: auto` by default, which prevents them from shrinking below their content's intrinsic width. The table's `width: max-content` propagates up through every ancestor.

**Fix**: Use `width: 0; min-width: 100%` on the scroll wrapper:

```jsx
<div style={{ width: 0, minWidth: '100%', overflowX: 'auto' }}>
    <table style={{ width: 'max-content', minWidth: '100%' }}>
```

Also apply `min-w-0` on React flex items in `workflow-runner.tsx` and `overflow-x-hidden` on the `run.html` content wrapper.

### Render Code Changes Not Reflected

**Symptom**: Changes to `template.py` RENDER_CODE don't appear in the UI.

**Cause**: Render code is stored in the DB (WorkflowRenderCodeRecord) at workflow creation time. Changes to the Python template don't auto-apply to existing workflows.

**Fix**: Append `?sync=true` to the workflow run URL, or POST to `/labs/workflow/api/<id>/sync-template/`.

### Follow-Up Tab Empty

**Symptom**: Follow-Up Rate tab shows 0 results.

**Cause**: CommCare OAuth token expired. Registration forms (needed for expected visit schedules) require a valid CommCare OAuth token.

**Fix**: Re-authorize CommCare OAuth via `/labs/commcare/initiate/`.

### SSE Connection Fails

**Symptom**: Dashboard shows loading spinner indefinitely.

**Cause**: The SSE endpoint at `/custom_analysis/mbw_monitoring/stream/` requires both Connect OAuth and CommCare OAuth tokens.

**Fix**: Ensure both OAuth tokens are valid in the session. Check server logs for 401/403 errors.

### OOM / "Connection Lost" on Large Opportunities

**Symptom**: Dashboard crashes mid-load on opportunities with 40K+ visits. Server logs show the SSE stream starts but never completes. Container may restart (OOM kill).

**Cause**: Without the streaming optimizations, loading 50K+ visits (~700 MB CSV) causes Python memory to spike above container limits (~1 GB).

**Mitigations** (already implemented):

1. **Stream-parse-and-store**: CSV goes to temp file, parsed in 1000-row chunks. See `labs/analysis/backends/sql/backend.py:_parse_and_store_streaming()`.
2. **Sectioned SSE**: Data sent in 3 separate sections, each freed after sending. See `views.py` SSE streaming logic.
3. **Intermediate `del` statements**: Large dicts freed with `del` + `gc.collect()` between pipeline stages.
4. **GPS visits excluded from SSE**: `serialize_flw_summary()` omits `visits` key; drill-down lazy-loads from API.

**Debug**: Check RSS logging in server output — lines like `[MBW Dashboard] RSS at after pipeline download: 991.0 MB`. If RSS exceeds container memory, the OOM kill is the cause.

### GPS Drill-Down Not Working

**Symptom**: Clicking "Details" on GPS tab shows spinner that never resolves, or shows a render error.

**Cause**: The GPS drill-down uses a `React.useEffect([expandedGps])` that fetches from `/api/gps/<username>/`. Common issues:

- Missing `opportunity_id` in the URL → 302 redirect from `LabsContextMiddleware` → fetch fails silently
- Undefined variables in the `useEffect` callback → `ReferenceError` stops execution
- Computed cache cold/expired → API returns empty results

**Fix**: Check browser Network tab for the `/api/gps/` request. Check server logs for the GPS detail endpoint. Verify `opportunity_id` is in the fetch URL params.
