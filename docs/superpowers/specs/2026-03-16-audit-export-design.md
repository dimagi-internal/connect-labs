# Audit Export to S3 — Design Spec

**Date:** 2026-03-16
**Scope:** Export `WorkflowRunRecord` and `AuditSessionRecord` data to S3 for durable archival outside of Labs.

---

## Problem

Labs data is not permanent. WorkflowRunRecord and AuditSessionRecord data lives on the production Connect server and is accessed read-only via API. If that data is deleted or becomes inaccessible, there is no backup. This design creates a durable, automatically maintained archive in AWS S3.

---

## Goals

- Archive workflow run and audit session records outside of Labs so they survive data loss
- No manual steps, no service account token management
- Accessible to Dimagi staff without touching the AWS console

## Non-goals

- Operational analytics or live reporting from the archive
- Real-time sync or webhooks
- Row-level access control

---

## Architecture

### Two CSV files in S3

```
s3://labs-jj-exports/
  audit_of_audits/
    workflow_runs.csv       # one row per WorkflowRunRecord
    audit_sessions.csv      # one row per AuditSessionRecord
```

Each file is a flat CSV with a header row. Rows are **upserted in-place** by primary key (`run_id` / `session_id`) — no date partitioning, no separate snapshot files. The files grow over time and always reflect the latest known state of each record.

S3 bucket config:
- Private (no public access)
- **Versioning enabled** — protects against bad writes; prior versions recoverable via AWS console
- IAM: the existing ECS task role gains `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on the `audit_of_audits/` prefix

### Event-driven export (no scheduled task, no service token)

Export fires from within the normal Labs request cycle, using the **logged-in user's active OAuth token**. No dedicated service account or stored credentials needed.

**Two trigger points:**

1. **Workflow run created** — when a user clicks "Run" on a workflow definition, the new `WorkflowRunRecord` is written to S3 immediately after the API call succeeds. Row status will be `in_progress`.

2. **Workflow run completed** — when a run's status is updated to `completed`, the existing row is overwritten (upsert by `run_id`) with the final state including session aggregates.

3. **Audit session created or completed** — when an `AuditSessionRecord` is created or its status changes, the row is upserted by `session_id`.

**Upsert mechanism:** read current CSV from S3 → find row by ID (insert if missing, replace if present) → write back to S3. Volume is low and writes are not concurrent, so no locking is required.

---

## Data Schema

### workflow_runs.csv

| Column | Source |
|---|---|
| `run_id` | `WorkflowRunRecord.id` |
| `definition_id` | `WorkflowRunRecord.definition_id` |
| `definition_name` | Definition name or `Workflow #{id}` |
| `template_type` | Definition template type |
| `opportunity_id` | `WorkflowRunRecord.opportunity_id` |
| `opportunity_name` | Resolved from org data |
| `created_at` | `WorkflowRunRecord.created_at` |
| `period_start` | Normalized YYYY-MM-DD |
| `period_end` | Normalized YYYY-MM-DD |
| `status` | `completed`, `in_progress`, `unknown` |
| `selected_count` | Number of selected FLWs |
| `username` | Run creator username |
| `session_count` | Total linked audit sessions |
| `completed_session_count` | Sessions with pass/fail result |
| `avg_pct_passed` | Pre-computed or calculated |
| `pct_passing` | % of completed sessions that passed |
| `tasks_created` | CommCare tasks created (from state) |
| `images_reviewed` | Total images reviewed (from state) |
| `pct_sampled` | % of submissions sampled (from state) |

### audit_sessions.csv

| Column | Source |
|---|---|
| `session_id` | `AuditSessionRecord.id` |
| `workflow_run_id` | `AuditSessionRecord.workflow_run_id` |
| `opportunity_id` | `AuditSessionRecord.opportunity_id` |
| `opportunity_name` | `AuditSessionRecord.opportunity_name` |
| `organization_id` | `AuditSessionRecord.organization_id` |
| `flw_username` | `AuditSessionRecord.flw_username` |
| `status` | `AuditSessionRecord.status` |
| `overall_result` | `pass`, `fail`, or blank |
| `title` | `AuditSessionRecord.title` |
| `tag` | `AuditSessionRecord.tag` |
| `notes` | `AuditSessionRecord.notes` |
| `kpi_notes` | `AuditSessionRecord.kpi_notes` |
| `visit_count` | `len(AuditSessionRecord.visit_ids)` |
| `created_at` | `AuditSessionRecord.data.get("created_at")` |

---

## Labs Download Page

**URL:** `/custom_analysis/exports/`
**Access:** `DimagiUserRequiredMixin` — Dimagi staff only. No overview tile.

The page lists both CSV files with metadata stamped at write time (no CSV parsing needed):

| File | Last Updated | Size | Rows | Action |
|---|---|---|---|---|
| workflow_runs.csv | 2026-03-16 14:32 UTC | 48 KB | 312 | Download |
| audit_sessions.csv | 2026-03-16 14:32 UTC | 124 KB | 1,840 | Download |

**Download flow:** clicking Download calls a view that generates a **pre-signed S3 URL** (15-minute expiry) and redirects the browser to it. The file downloads directly from S3 — no AWS console or credentials needed by the user.

File metadata (last updated, size, rows) is stored as S3 object metadata (`x-amz-meta-*`) when the file is written, and read back at page load via `s3.head_object()`.

---

## New Files

| File | Purpose |
|---|---|
| `commcare_connect/custom_analysis/exports/views.py` | Download page view, pre-signed URL generation |
| `commcare_connect/custom_analysis/exports/urls.py` | URL config for `/custom_analysis/exports/` |
| `commcare_connect/custom_analysis/exports/s3_export.py` | S3 upsert logic for both record types |
| `commcare_connect/templates/custom_analysis/exports/index.html` | Download page template |

Export hooks added to existing files:
- `commcare_connect/workflow/views.py` — call export after run create/complete
- `commcare_connect/audit/views.py` or signal — call export after session create/complete

---

## AWS Setup (one-time)

1. Create S3 bucket `labs-jj-exports` (or prefix within existing bucket) with versioning enabled
2. Add IAM policy to the ECS task role: `s3:GetObject`, `s3:PutObject`, `s3:ListBucket` on `arn:aws:s3:::labs-jj-exports/audit_of_audits/*`
3. Add `LABS_EXPORTS_BUCKET` env var to ECS task definition via `aws-env-update` skill

---

## Error Handling

- S3 write failures are **logged and silenced** — a failed export never breaks the user-facing action that triggered it
- If the CSV doesn't exist yet (first write), it is created with a header row
- If S3 is unavailable, the failure is logged at `ERROR` level for monitoring; the workflow/audit action still succeeds
