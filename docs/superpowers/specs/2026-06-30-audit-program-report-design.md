# Audit Program Report — two-template design (program 176)

**Date:** 2026-06-30
**Status:** Approved (design)
**Author:** Jonathan Jackson (with Claude)

## Goal

A weekly, cross-opportunity ("PAR-style") view of audit quality for **program
176's 4 opportunities**. Each week, for each FLW, create **two** image audits:

- **Track A (MUAC census):** *all* MUAC images, run through the `muac_overzoom`
  AI agent with its fail verdict auto-applied.
- **Track B (rest, sampled):** the *remaining* image types, sampled at **10%**,
  reviewed by humans.

Then roll up the results of those audits across all 4 opps and all weeks into a
drillable week × opp grid.

## The template-vs-instance split

The *behavior* lives in two repo templates; the *program-176 specifics* live in
two live workflow instances created from them. This mirrors the shipped
`llo_weekly_review → program_admin_report` split, specialized to audits.

| | Creator | Viewer |
|---|---|---|
| **Template** (repo `.py`) | `weekly_dual_track_audit.py` — *behavior*: per FLW, create Track A (census of a chosen image type + AI agent) and Track B (sampled remainder). `multi_opp: True`, action-shaped (no saved runs). | `audit_par.py` — *behavior*: roll audit results into a week × opp grid, drill to FLW. `multi_opp: True`, `supports_saved_runs: True`. |
| **Instance** (live) | `opportunity_ids` = the 4 opps; per-opp **pinned** MUAC image-type id(s); Track A = `muac_overzoom` + auto-tag `fail_overzoomed`, sample 100%; Track B = all-other discovered images, sample 10%; default window = last week. | watched creator definition id; same 4 opps; reporting window (start/end). |

## Template 1 — `weekly_dual_track_audit` (creator)

**Flags:** `multi_opp: True`. Action-shaped — its artifacts are audit sessions in
their own model, so it does **not** declare `supports_saved_runs`. Each weekly
batch is one run; the run stores `period_start`/`period_end` (the week window) in
state, like `bulk_image_audit`.

### Server-side orchestration (the schedulable core)

New module `commcare_connect/workflow/job_handlers/weekly_dual_track_audit.py`
registers a `weekly_dual_track_audit_create` handler via `@register_job_handler`
(imported in `job_handlers/__init__.py` so it registers on startup, alongside
`program_admin_rollup`).

Inputs: `(run_id, opportunity_ids, window_start, window_end, per_opp_config,
track_a, track_b)`. For each opp it fires **two** audit-creation flows, reusing
the existing `audit/tasks.py` creation path + `AuditDataAccess.create_audit_session`:

- **Track A:** `criteria.related_fields` = pinned MUAC image ids
  (`{image_path, filter_by_image: true}`), `sample_percentage: 100`,
  `criteria.tag: 'muac'`, `ai_agent_id: 'muac_overzoom'`,
  `ai_auto_apply_actions: ['fail_overzoomed']`.
- **Track B:** `criteria.related_fields` = all other discovered image types,
  `sample_percentage: 10`, `criteria.tag: 'rest'`, no AI agent.

Each flow produces **one session per FLW**, persisted with
`labs_record_id = run_id`, the session's `opportunity_id`, and `tag` (`muac` /
`rest`). `criteria.tag` flows to the session via `audit/tasks.py`
(`session_tag = criteria.get("tag", "")`). So "two audits per FLW" = each FLW
gets a `muac` session and a `rest` session, each tagged for the PAR.

Putting the 8-call loop (4 opps × 2 tracks) in a server-side job — not in
browser `actions.createAudit` calls — keeps it atomic, gives one progress
stream, and means a **future cron can call the same handler** (satisfies "wire
scheduling later").

### "Rest" set rule

Per opp, Track B's image set = **all image types discovered for that opp**
(`/audit/api/opportunity/<id>/image-questions/`) **minus** the pinned MUAC ids.
The render preview shows the resolved split so it can be eyeballed before
creating.

### Render code

1. Window picker (date presets; defaults to "last week", reusing
   `bulk_image_audit`'s `calculateDateRange`).
2. Per-opp resolved-config preview: pinned MUAC id(s) + derived "rest" set, per
   opp, so the operator confirms the split.
3. "Create this week's audits" button → `actions.startJob({job_type:
   'weekly_dual_track_audit_create', run_id, opportunity_ids, window, ...})` with
   `streamJobProgress` for progress.
4. Created-session list grouped by opp/track, each linking to the existing
   `/audit/<session_id>/bulk/?opportunity_id=<id>&workflow_run_id=<run_id>`
   review page (MUAC sessions arrive pre-tagged by the AI; rest sessions await
   human review).

## Template 2 — `audit_par` (viewer)

**Flags:** `multi_opp: True`, `supports_saved_runs: True`. A new PAR specialized
to audits (distinct from `program_admin_report`, which is generic
flags/audits/tasks). Each PAR run carries a reporting window.

### Rollup (server-side, run while live)

New `audit_par_rollup` job handler. For each **creator run** whose
`period_start`/`period_end` falls in the PAR window (= one week) × each opp:
read sessions via `AuditDataAccess.get_sessions_by_workflow_run(run_id)` scoped
per opp, split by `tag`, and aggregate per opp-week:

- MUAC: total / pass / fail / pending, AI-flagged-fail count.
- Rest: total / pass / fail / pending.

Reads use a **per-opp scoped `AuditDataAccess`** (the labs API enforces opp scope
on every request — a single primary-opp DAO returns 0 for non-primary opps; see
`program_admin_report.compute_program_admin_rollup`).

Result is written into run **state** (`watched_summary`, window, watched source);
the declarative `snapshot_inputs` manifest freezes it at completion. No
`build_snapshot` hook — same pattern as `program_admin_report`.

### Render code

- **Grid:** rows = the 4 opps, columns = weeks (one creator run each). The "did
  the batch run this week?" signal is per-column (one multi-opp creator run
  covers all opps). Cell = MUAC vs rest mini pass/fail/pending bars + an
  AI-flag badge.
- **Drill-in:** click a cell → FLW table; each FLW row shows its MUAC audit
  (pass/fail/AI-flag + link) and its rest audit (pass/fail + link), carrying
  `?opportunity_id=<source_opp>` on every link (cross-opp scoping, per the
  `program_admin_report` lesson).
- Live "Refresh data" button (runs `audit_par_rollup`); `📌 Snapshot` vs `● Live`
  badge; "Mark Run Complete" via `view.complete(...)`.

### Snapshot contract

```python
"snapshot_inputs": {
    "pipelines": [],
    "workers": False,
    "state_keys": ["watched_summary", "window_start", "window_end",
                   "watched_source", "expected_weeks"],
}
```

## Data flow

```
Weekly: open creator run → "Create" → job loops 4 opps × 2 tracks
        → per-FLW sessions tagged {opportunity_id, labs_record_id=run_id, muac|rest}
        → muac_overzoom AI auto-tags MUAC fails; humans review rest in /audit/bulk/
PAR:    open PAR run (window) → audit_par_rollup reads creator runs × opps × sessions by tag
        → week × opp grid → drill to per-FLW audit results
```

## Decisions

- **MUAC AI:** existing `muac_overzoom` agent, auto-apply `fail_overzoomed`.
  (A richer MUAC measurement-reading agent is explicitly out of scope.)
- **Image-type identification:** pinned exact image-type id(s) per opp in the
  creator instance config (not regex auto-detect).
- **Cadence:** manual weekly run now; creation logic lives in a server-side job
  handler so an external scheduler can call it later.
- **Track-B review:** humans review the 10% sample in the existing bulk UI; the
  PAR shows them as `pending` until reviewed.
- **Reuse vs new PAR:** dedicated `audit_par` template (not
  `program_admin_report`).

## Confirmed behaviors / reused infrastructure

- **One audit session per FLW per track.** `audit/tasks.py` (`is_per_flw`
  branch, ~L597) creates exactly one session per FLW group, each linked via
  `workflow_run_id`. So each FLW gets a `muac` session and a `rest` session
  per weekly run.
- **Deletion / cleanup uses existing infrastructure — no new work.** The shipped
  cascade is sufficient for this feature (the creator only produces audit
  sessions):
  - Per-run: `WorkflowDataAccess.delete_run(run_id, delete_linked=True)` deletes
    the run + its linked audit sessions (queried by `labs_record_id=run_id`).
    Exposed via `api/run/<id>/delete/` (`delete_run_api`) and the `deleteRun()`
    control on the workflow list page.
  - Per-workflow: `delete_definition(definition_id, delete_linked)` cascades all
    runs + their audit sessions; the list page already offers
    "Workflow Only" vs "Workflow + Linked Data".
  - Generalizing the cascade to tasks/flags/jobs and adding a preview-count
    confirm UI were considered and **deferred** — not needed here.

## Out of scope (YAGNI)

- A cron/scheduler itself (only the schedulable handler).
- MUAC measurement-reading AI.
- Reusing the generic `program_admin_report`.
- Auto-passing or AI-reviewing Track B.

## Key references

- `commcare_connect/workflow/templates/bulk_image_audit.py` — single-opp
  audit-creation render + `createAudit` criteria shape, image-type discovery,
  AI-agent selection (`muac_overzoom`).
- `commcare_connect/workflow/templates/program_admin_report.py` — multi-opp +
  saved-runs PAR pattern, per-opp scoped rollup, drill-in grid, cross-opp link
  scoping.
- `commcare_connect/workflow/job_handlers/program_admin_report.py` +
  `job_handlers/__init__.py` — `@register_job_handler` pattern.
- `commcare_connect/audit/data_access.py` — `create_audit_session` (tag,
  `labs_record_id=workflow_run_id`), `get_sessions_by_workflow_run`.
- `commcare_connect/audit/tasks.py` — `session_tag = criteria.get("tag")`,
  `ai_agent_id` post-creation review.
- `commcare_connect/labs/ai_review_agents/agents/muac_overzoom.py` — agent id /
  `result_actions`.
- `WORKFLOW_REFERENCE.md` §8 (multi-opp), §9 (saved runs), §Audit Creation.
