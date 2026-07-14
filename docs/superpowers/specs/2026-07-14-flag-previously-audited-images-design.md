# Flag & optionally exclude previously-audited images

**Status:** Approved design (2026-07-14)
**Area:** `connect_labs/audit/` (bulk image audit)
**Feedback item:** "Labs Bulk Image Audit Feedback" → Must-have #5.

## Problem

When a reviewer runs a new bulk image audit, images that were already audited in a
previous session are re-presented with no indication they were seen before. This
wastes reviewer effort and gives no signal that a decision already exists. Two
asks:

- **A. Flag previously-audited images** in the review grid, showing the prior verdict.
- **B. At creation time**, let the user choose to include previously-audited images
  (today's behavior) or exclude them and audit only never-audited images.

## Decisions (locked)

- **"Audited" means**: the image has a non-empty human verdict (`pass` / `fail` /
  `duplicate_fake`) in a prior **completed** audit session. Pending images and
  in-progress sessions do not count.
- **Scope**: all opportunities. Available in both creation entry points — the
  workflow config phase and the standalone creation wizard.
- **Creation default**: include all (preserves current behavior). Excluding
  already-audited images is an explicit opt-in.
- **Badge**: shows the prior verdict — "Audited: Passed / Failed / Duplicate·Fake"
  — with the prior session's completion date on hover.

## Architecture

### 1. Shared core — prior-audit index

A single new helper in `connect_labs/audit/data_access.py`, used by both features:

```
AuditDataAccess.get_prior_audited_images(opportunity_id, exclude_session_id=None)
  -> { "<visit_id>:<blob_id>": {
         "result": "pass"|"fail"|"duplicate_fake",
         "session_id": <id>,
         "session_title": <str>,
         "completed_at": <iso str>,
       } }
```

- Reuses `get_audit_sessions(...)` to fetch sessions in the opportunity scope, then
  keeps only those with status `completed` (filtered in Python, since status is a
  model property derived from `data`).
- Walks each session's `visit_results[visit_id]["assessments"][blob_id]` and records
  every image whose `result` is a non-empty human verdict.
- Key is **`f"{visit_id}:{blob_id}"`** — `blob_id` is only unique within a visit, so
  the composite key avoids cross-visit collisions.
- If an image was audited in more than one completed session, keep the entry with the
  latest `completed_at`.
- `exclude_session_id`, when given, skips that session (so a session never flags its
  own images — matters for the reopened-session case).

Performance note: this reads the full JSON of all completed sessions for the
opportunity. Acceptable at labs scale. If it becomes slow for opportunities with many
past audits, add a short-lived cache keyed by `opportunity_id` — out of scope for the
first cut, but the single-helper design makes it a localized change.

### 2. Feature A — "Audited" badge in the review grid

- `ExperimentBulkAssessmentDataView` (`connect_labs/audit/views.py`) builds the index
  **once per request**, passing `exclude_session_id = <current session id>`.
- Each per-image dict (both the primary and fallback assembly blocks) gains:
  - `prior_audited`: bool
  - `prior_result`: `"pass"|"fail"|"duplicate_fake"|None`
  - `prior_session_date`: display string (or empty)
- Template `connect_labs/templates/audit/bulk_assessment.html`: a new overlay badge in
  the tile image container, shown when `assessment.prior_audited`. Label derived from
  `prior_result` ("Audited: Passed / Failed / Duplicate·Fake"); `title` attribute =
  `prior_session_date`. Uses a distinct color (slate/indigo) so it reads differently
  from the current session's own pass/fail badge. Mirrored in the lightbox header for
  parity.

### 3. Feature B — include / exclude at creation

- New field on the `AuditCriteria` dataclass (`data_access.py`):
  `exclude_prior_audited: bool = False`, parsed in `AuditCriteria.from_dict`.
- **UI (both entry points):** an opt-in checkbox labeled
  *"Exclude images already audited in a completed session"* (default off), threaded
  into the `criteria` object that each UI POSTs:
  - workflow config phase in `connect_labs/workflow/templates/bulk_image_audit.py`
  - standalone wizard `connect_labs/templates/audit/audit_creation_wizard.html`
  Both already POST to `/audit/api/audit/create-async/`, so only one backend path
  changes.
- **Filtering:** in `run_audit_creation` (`connect_labs/audit/tasks.py`), immediately
  after `extract_images_for_visits` returns, when `criteria.exclude_prior_audited` is
  set: build the index for that opportunity (no `exclude_session_id` — no current
  session exists yet) and drop images whose `f"{visit_id}:{blob_id}"` is present.
  Multi-opp safe (index built per opportunity id). Visits left with zero images are
  omitted from `visit_results`.
- **No silent dropping:** log the excluded image count via the task's progress
  channel. If all images are excluded, the session is still created (empty); the
  excluded count is surfaced so the "0 assessments" state is explained rather than
  mysterious.

## Edge cases

- **Reopened session**: excluded from its own index via `exclude_session_id`, so its
  images are not self-flagged.
- **In-progress prior sessions**: ignored; only completed sessions contribute.
- **Same image across two visits**: distinct composite keys, handled independently.
- **Multi-opportunity audits**: index computed per opportunity id.

## Testing

- pytest units for `get_prior_audited_images`: completed-only filtering; excludes the
  given `exclude_session_id`; keeps the most-recent verdict when an image was audited
  twice; composite-key correctness.
- `ExperimentBulkAssessmentDataView`: image dicts carry correct
  `prior_audited`/`prior_result` for indexed images and not for others.
- `run_audit_creation`: with `exclude_prior_audited=True`, already-audited images are
  dropped; with it false (default), all images are kept (regression guard).
- Frontend badge + creation checkbox verified in a browser (standalone Alpine harness
  and/or local server against real data).

## Files touched

| File | Change |
| --- | --- |
| `connect_labs/audit/data_access.py` | `get_prior_audited_images` helper; `exclude_prior_audited` on `AuditCriteria` + `from_dict` |
| `connect_labs/audit/tasks.py` | creation-time exclusion filter + excluded-count logging |
| `connect_labs/audit/views.py` | badge fields on the bulk-assessment data view |
| `connect_labs/templates/audit/bulk_assessment.html` | "Audited" tile + lightbox badge |
| `connect_labs/workflow/templates/bulk_image_audit.py` | exclude checkbox → criteria |
| `connect_labs/templates/audit/audit_creation_wizard.html` | exclude checkbox → criteria |
| `connect_labs/audit/tests/` | unit tests for the helper, data view, and filter |

## Out of scope

- Caching the prior-audit index (add later if needed).
- Reconciling the "Duplicate/Fake" result button with the reason taxonomy (separate).
- Cross-opportunity image de-duplication (index is per opportunity).
