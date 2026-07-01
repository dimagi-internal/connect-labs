# Bulk Image Audit Workflow Template — Design

**Date:** 2026-02-25
**Branch:** labs-auditv2

---

## Overview

Add a new workflow template called **"Bulk Image Audit"** to the CommCare Connect Labs workflow engine. This template lets a reviewer select one or more opportunities, choose a specific image type (Scale, ORS, or MUAC), configure visit selection and sampling, set a passing threshold, and then review all matching photos inline — without leaving the workflow run page.

Also rename the existing "Weekly Audit with AI Review" template to **"Weekly KMC Audit with AI Review"** (one-line change).

---

## Files Changed

| File | Change |
|---|---|
| `connect_labs/workflow/templates/audit_with_ai_review.py` | Rename template `name` and `description` fields |
| `connect_labs/workflow/templates/bulk_image_audit.py` | New file — full template definition and React render code |

No changes to `audit/`, no new Django views, no new URL patterns. All API endpoints used already exist.

---

## Image Type Mapping

| Display Name | Form Path |
|---|---|
| Scale Photo | `anthropometric/upload_weight_image` |
| ORS Photo | `service_delivery/ors_group/ors_photo` |
| MUAC Photo | `service_delivery/muac_group/muac_display_1/muac_photo` |

---

## Workflow State Shape

Stored in `run.data.state` (PATCH-merged via `onUpdateState`):

```json
{
  "phase": "config | creating | reviewing | completed",
  "session_id": "<audit session id, set after creation>",
  "config": {
    "opportunity_ids": [1, 2],
    "opportunity_names": {"1": "MBW KMC", "2": "..."},
    "image_type": "scale_photo | ors_photo | muac_photo",
    "image_path": "anthropometric/upload_weight_image",
    "audit_mode": "date_range | last_n_per_opp",
    "start_date": "2026-02-01",
    "end_date": "2026-02-14",
    "count_per_opp": 10,
    "sample_percentage": 100,
    "threshold": 80
  },
  "active_job": {
    "job_id": "<celery task id>",
    "status": "running | completed | failed | cancelled",
    "started_at": "<iso>",
    "completed_at": "<iso>"
  },
  "completion": {
    "notes": "...",
    "completed_at": "<iso>"
  }
}
```

The `phase` field drives which UI section renders when a user navigates to the run.

---

## Phase 1 — Config Form

Shown when `phase === "config"` (or when no phase is set yet).

### Opportunity Selector
- Multi-select search, same UX as audit creation wizard
- Calls `/audit/api/opportunities/search/?q=<query>`
- Selected opps shown as removable pills
- Defaults to current `opportunity_id` from URL params

### Image Type
Three toggle buttons:
- **Scale Photo** → `anthropometric/upload_weight_image`
- **ORS Photo** → `service_delivery/ors_group/ors_photo`
- **MUAC Photo** → `service_delivery/muac_group/muac_display_1/muac_photo`

### Visit Selection
Copied from `audit_with_ai_review.py` — two modes:
- **Date Range**: preset pills (Last Week, Last 7/14/30 Days, This Month, Last Month, Custom) + date inputs
- **Last N Visits per Opportunity**: numeric input

### Sampling
- Percentage input (1–100, default 100)
- Label: "Sample X% of matching visits"

### Passing Threshold
- Percentage input (1–100, default 80)
- Label: "Mark FLW as passing if ≥ X% of their photos pass"

### Submit
- **"Create Review"** button — disabled until at least one opp and one image type are selected
- Calls `/audit/api/audit/create-async/` with:
  ```json
  {
    "opportunities": [{"id": 1, "name": "MBW KMC"}],
    "criteria": {
      "audit_type": "date_range | last_n_per_opp",
      "start_date": "...",
      "end_date": "...",
      "count_per_opp": 10,
      "sample_percentage": 100,
      "related_fields": [
        {"image_path": "<selected path>", "filter_by_image": true}
      ]
    },
    "workflow_run_id": "<instance.id>"
  }
  ```
  Note: no `ai_agent_id` — AI review is not triggered on creation.

---

## Phase 2 — Creating (Progress)

Shown when `phase === "creating"`. Identical to `audit_with_ai_review.py` progress UI:
- Spinner with stage name
- Progress bar (processed / total visits)
- Cancel button (calls `actions.cancelAudit(task_id)`)
- Reconnects to SSE stream on page refresh (reads `state.active_job`)

On completion: sets `state.phase = "reviewing"`, `state.session_id = <id>`.
On error: sets `state.phase = "config"` (allows retry), shows error banner.

---

## Phase 3 — Photo Review (Active, Editable)

Shown when `phase === "reviewing"`. Fetches `/audit/api/<session_id>/bulk-data/` on mount.

### Stats Bar
Four summary cards: Total Photos, Pending, Passed, Failed.

### Photo List
Each photo rendered as a card:
- Thumbnail (loaded lazily from `/audit/image/<opp_id>/<blob_id>/`)
- Visit date, entity name, FLW username
- Pass / Fail toggle buttons
- Notes text input
- **"Run AI Agent" button**:
  - Enabled only when `image_type === "scale_photo"`
  - Greyed out and disabled for ORS and MUAC (with tooltip: "AI review is only available for Scale Photos")
  - Calls `/audit/api/<session_id>/ai-review/` when enabled

Progress is auto-saved incrementally via `/audit/api/<session_id>/save/`.

### FLW Summary Table
Computed client-side from the assessments array. One row per unique `username`.

Columns:
| Opp Name | FLW Name | Passed / Assessments | % Passed | Result |
|---|---|---|---|---|

- **Result icon**: ✅ if `% Passed >= threshold`, ❌ if below
- All 5 columns **sortable** (click header toggles asc/desc)
- Filter controls above table:
  - FLW name text filter
  - Opp name text filter
  - Result dropdown (All / Pass / Fail)

No Visit-Level Summary table (removed from this template).

### Complete Image Review
Button at the bottom of the page:

- **If any photos are still pending**: shows warning and blocks completion:
  `"X photos still pending review. All photos must be reviewed before completing."`
- **If all reviewed**: shows inline completion form:
  - Notes textarea (no KPI Notes field)
  - **Save** button
  - On save: POSTs to `/audit/api/<session_id>/complete/` with:
    - `overall_result`: `"pass"` if all FLWs meet threshold, else `"fail"`
    - `notes`: from textarea
    - `kpi_notes`: `""` (empty)
  - Sets `state.phase = "completed"`, persists `state.completion`

---

## Phase 4 — Completed (Read-Only Summary)

Shown when `phase === "completed"`. Navigating to a completed run from the workflow list renders this phase.

- **Completion banner**: green header — "Image Review Completed", completed date, notes
- **Overall result badge**: ✅ Pass or ❌ Fail based on threshold
- **FLW Summary table**: same table as Phase 3 but fully read-only, no action buttons
- No edit controls, no uncomplete option

---

## API Endpoints Used (all pre-existing)

| Endpoint | Purpose |
|---|---|
| `GET /audit/api/opportunities/search/` | Opp search in config form |
| `POST /audit/api/audit/create-async/` | Start async audit creation |
| `GET /audit/api/audit/progress/<task_id>/stream/` | SSE progress stream |
| `POST /audit/api/audit/cancel/<task_id>/` | Cancel creation |
| `GET /audit/api/<session_id>/bulk-data/` | Load photo assessments |
| `POST /audit/api/<session_id>/save/` | Save progress incrementally |
| `POST /audit/api/<session_id>/complete/` | Complete review |
| `POST /audit/api/<session_id>/ai-review/` | Run AI agent on a photo |

---

## Key Differences from "Weekly KMC Audit with AI Review"

| Feature | Weekly KMC Audit | Bulk Image Audit |
|---|---|---|
| Opportunities | Single (from URL param) | Multi-select search |
| Image type | Fixed (scale only) | User selects: Scale / ORS / MUAC |
| AI review | Triggered on creation | Manual per-photo, Scale only |
| Post-creation UX | Links to separate audit pages | Inline photo review (no navigation) |
| Summary table | Per-session links | Per-FLW pass/fail vs threshold |
| Visit-Level Summary | Shown | Removed |
| Completion | Per session in audit app | Inline in workflow run |
