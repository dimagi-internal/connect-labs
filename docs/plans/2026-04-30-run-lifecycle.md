# Workflow Run Lifecycle: active | frozen

**Date:** 2026-04-30
**Status:** SUPERSEDED by `2026-05-04-run-state-final.md`. The active|frozen vocabulary was reverted to in_progress|completed; the rest of the design (no auto-create, atomic completion, snapshot-on-complete, server-enforced read-only) survives.

## Goal

Replace the muddled `in_progress | completed | preview` status with a clean two-state lifecycle: `active | frozen`. Make the data flow into a run unambiguous: an active run reads live pipelines + workers + saved state; a frozen run reads its snapshot only. Stop auto-creating runs on every URL visit.

Failed runs aren't a status — they're just runs the user discards. Keeping the lifecycle to two states means render code never has to ask "is this run usable?" — it's either active (work in progress) or frozen (read-only artifact).

## Data shape

```python
run.data = {
    "definition_id":  int,
    "opportunity_id": int,
    "period_start":   str,            # ISO date
    "period_end":     str,            # ISO date
    "status":         "active" | "frozen",
    "created_at":     str,
    "frozen_at":      str | None,     # set on the active→frozen transition
    "state":          {...},          # FE-written, only meaningful when active
    "snapshot":       {...} | None,   # BE-written at freeze; only read when frozen
}
```

Rules:
- `state` is the user's working area while the run is active. Render code reads + writes it.
- `snapshot` is the BE-written frozen artifact. Render code reads it when `status == "frozen"`.
- A run never reads both. Either it's active (state + live pipelines) or frozen (snapshot).
- `period_start` / `period_end` stay top-level (pre-existing convention; no point churning).

## Lifecycle

```
       Start Run                 Freeze (build_snapshot + atomic transition)
           │                                       │
           ▼                                       ▼
        ┌────────┐                              ┌────────┐
        │ active │  ────────────────────────▶   │ frozen │
        └────────┘                              └────────┘
            │                                       │
            │ Delete                                │ Re-run = create new active run
            ▼                                       ▼
        (gone)                                  (frozen run is preserved as history)
```

- **active** is the only writable state. `state` is mutable; `snapshot` is null.
- **frozen** is read-only. `snapshot` is populated; `frozen_at` is set; `state` is preserved (historical record of what was there at freeze) but rendering ignores it.
- No `failed`. If `build_snapshot` raises during a freeze attempt, the transition fails and the run stays `active`. The error is shown to the user; they can retry or fix the data.

## URL / UI changes

- `/labs/workflow/<def>/run/` (no `run_id`) — **shows a list of past runs**, plus a "Start Run" button. Stops auto-creating. (Today's biggest footgun: every URL visit creates a new run record.)
- `/labs/workflow/<def>/run/?run_id=<id>` — opens that run. Render code reads `instance.status` to know which mode.
- New endpoints:
  - `POST /api/<def_id>/run/start/` — create a new active run, return id.
  - `POST /api/run/<id>/freeze/` — atomic: build_snapshot → persist snapshot → status=active→frozen → set frozen_at. Returns 4xx if the build fails (run stays active).
  - `POST /api/run/<id>/delete/` — already exists; works for both states.
- Removed:
  - `POST /api/run/<id>/complete/` — `complete_run` becomes deprecated. We keep the URL+method as an alias for `freeze` for backwards compat with existing render code, but it now triggers the freeze flow (which is what most callers actually wanted anyway).

## Render code contract

Templates can read `instance.status`. When the framework matures we'll substitute snapshot data into the live prop shape automatically (so render code is unchanged), but for v1 templates can branch:

```jsx
const dataSource = instance.status === "frozen" ? instance.snapshot : null;
const workers   = dataSource?.workers ?? liveWorkers;
const state     = dataSource?.state   ?? instance.state;
```

`build_snapshot` should return data shaped to match what render reads. For `performance_review` that's `{workers, state, summary, opportunity_ids}`.

## Migration

Existing runs in production:

| Old `status`        | New `status` | Notes                                  |
| ------------------- | ------------ | -------------------------------------- |
| `in_progress`       | `active`     | Most common case                       |
| `completed`         | `frozen`     | Mark `legacy=true` if no snapshot exists. Render falls back to "snapshot unavailable; this run predates the snapshot framework" |
| `preview`           | (unchanged)  | Edit mode is a UI-only flag, not a real status — leave it alone |

Migration runs as a one-shot Django management command: `python manage.py migrate_run_statuses`. Reads each `workflow_run` record, mutates the status field, persists.

## Out of scope (future PRs)

- Snapshot-as-prop-substitution: framework auto-replaces `workers`/`pipelines`/`state` when frozen so render is identical. Today templates still branch.
- Versioned snapshot history per run.
- Backfill snapshots for legacy runs (pipeline cache may still be warm).
- MBW V2 migration to generic snapshot endpoint (waiting on V3).

## Test plan

- Unit: `freeze_run` writes snapshot + transitions status atomically; build_snapshot raise leaves run active.
- Unit: `complete_run` is now an alias for freeze.
- Unit: status-mapping migration happy path + edge cases (missing status, preview, unknown values).
- Integration: `RunView.get` with no run_id renders list, doesn't create a run.
- Integration: `Start Run` endpoint creates active run, returns id.
- E2E (manual): create run → make decisions → freeze → confirm status=frozen, frozen_at set, snapshot populated. Re-open: snapshot rendered.
