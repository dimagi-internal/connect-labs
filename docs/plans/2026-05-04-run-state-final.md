# Workflow run state — final design

**Date:** 2026-05-04
**Status:** Implemented
**Supersedes:** `2026-04-29-workflow-run-snapshots-design.md`, `2026-04-30-run-lifecycle.md`

## Summary

A workflow run has two states — `in_progress` and `completed` — with one terminal transition. The user explicitly marks a run completed; that transition atomically builds a snapshot, persists it, flips status, and stamps `completed_at` in a single LabsRecord write. After that, the run is immutable both on the UI and on the server. There is no `failed` or `abandoned` state — abandoned runs are indistinguishable from in_progress, so we don't model them.

This supersedes the brief `active | frozen` vocabulary detour from 04-30. The two states are the same; the names are not. "Completed" is what users say; "frozen" is plumbing they don't see.

## Why this shape

Three forces:

1. **Historical fidelity.** Reopening a "weekly performance review" run from three weeks ago should show the workers and decisions that were on screen when it was finished — not whatever the live FLW list now looks like.
2. **Indistinguishable from abandoned.** Adding a third state for "the user walked away" buys nothing: we can't tell that apart from "still working on it." Two states is sufficient.
3. **Single vocabulary across templates.** Existing templates (`audit_with_ai_review`, `bulk_image_audit`, `ocs_outreach`) already use `'in_progress'` and `'completed'` for *session* status. The run-level rename to `active|frozen` was making the codebase use two different vocabularies for the same idea.

## Lifecycle

```
   Start Run                     view.complete()
       │                                │
       ▼                                ▼
  ┌───────────┐                  ┌───────────┐
  │in_progress│ ───────────────▶ │ completed │
  └───────────┘                  └───────────┘
       │                                │
       │ delete                         │ Re-run = new in_progress run
       ▼                                ▼
     (gone)                       (completed run preserved as history)
```

- `in_progress` — mutable. State writes go through `POST /api/run/<id>/state/`. No snapshot exists.
- `completed` — immutable. `POST /api/run/<id>/complete/` is the only verb that writes the snapshot + flips status. Other mutation endpoints return `409` for completed runs.

## Storage shape

```python
run.data = {
    "definition_id":  int,
    "opportunity_id": int,
    "period_start":   str,            # ISO date
    "period_end":     str,            # ISO date
    "status":         "in_progress" | "completed",
    "created_at":     str,
    "completed_at":   str | None,     # set on completion
    "state":          {...},          # FE-written; user inputs while in_progress
    "snapshot":       {...} | None,   # BE-written at completion; only read when completed
}
```

Defensive: the proxy `WorkflowRunRecord.status` also maps the brief 04-30 vocabulary (`active`→`in_progress`, `frozen`→`completed`) so any rows touched by the now-deleted migration still read correctly. `completed_at` falls back to the legacy `frozen_at` for the same reason.

## Per-template opt-in

Templates declare `supports_saved_runs: True` to enable the completion lifecycle. Action-shaped templates (audits, OCS, etc.) omit it — their value lives in artifacts that persist in their own models, and they have no "moment of completion" worth freezing.

Run-shaped templates additionally declare:

- **`snapshot_inputs`** (optional, declarative manifest): what the framework's default hook should capture. `{"pipelines": [aliases], "workers": bool, "state_keys": [keys]}`. Anything not listed is not captured.
- **`build_snapshot(*, pipelines, state, opportunity_id, **context) -> dict`** (optional, module-level function): overrides the default hook entirely. Use when the snapshot shape is computed (summaries, KPIs) rather than a verbatim capture.
- **`snapshot_schema`** (optional, documentation): a manifest of what render code reads from `instance.snapshot` — used in the completion-confirm copy and for evolution tracking via `version`.

If a template declares `supports_saved_runs` but neither `snapshot_inputs` nor `build_snapshot`, the framework logs a warning and dumps everything (pipelines + workers + state). That fallback works but the template should declare its inputs for clarity and size discipline.

## Render contract — the `view` helper

Render code never reads `instance.snapshot` directly, never branches on `instance.status`, and never reads bare `workers`/`pipelines`/`state` props. It reads `view`:

```jsx
const view = useRunView(instance, { workers, pipelines, state });
view.workers          // live or snapshot, same shape
view.pipelines.X      // ditto
view.state            // ditto
view.isCompleted      // boolean
view.asOf             // completed_at, or null
view.complete({ confirm? })   // mark this run complete; reloads on success
```

When `instance.status === 'in_progress'`, `view` returns the live props. When `'completed'`, it returns whatever was captured under `instance.snapshot.workers`, `instance.snapshot.pipelines`, `instance.snapshot.state`. The template's snapshot shape (via `snapshot_inputs` or the `build_snapshot` hook) must match what `view` reads — that's the contract.

## Server contract — completion API

`POST /api/run/<id>/complete/`:
- 404 if run/definition missing.
- 409 if already completed.
- 400 if the workflow's template does not declare `supports_saved_runs`.
- Calls `build_snapshot_for_template(...)`. If the hook raises, the run stays in_progress.
- Atomic single-write: status, completed_at, snapshot.
- Returns `{success, status, completed_at, snapshot}` on 200.

`GET /api/run/<id>/snapshot/`: read-only inspection (debug/admin). Render code does not call this — it reads from props via `view`.

There is no `/freeze/` or `/snapshot/build/` endpoint; those URLs from the 04-30 design are removed.

## Size budget

Snapshots live inside `LabsRecord.data`. The framework logs a warning at 1 MB and an error at 5 MB. Templates exceeding the cap should declare `snapshot_inputs` to trim, or move to a custom `build_snapshot` hook that produces a compact derived shape.

## Migration

There is no migration command for this change. Prod data is still in canonical `in_progress`/`completed` form (the 04-30 migration was never run); the proxy maps any 04-30-touched rows defensively. The `migrate_run_statuses` management command has been deleted.

## Out of scope

- **Versioned snapshot history per run.** Re-run creates a new run; the previous completed run is preserved. Add per-run versioning later only if a use case appears.
- **Reproducing analysis from raw inputs.** Raw rows are not on the run. If render code or pipeline schema changes, old snapshots render against the shape they captured; new runs use the new shape.
- **Entity-stage pipeline lift** (lifting JS aggregators in KMC templates to a Python `ComputedEntityCache`). Tracked separately — KMC templates can adopt saved runs with the default hook before the entity stage lands.
- **Celery handoff for completion.** The endpoint is synchronous today. If a template's snapshot build is slow enough to time out, the right move is to move that template's hook into `run_workflow_job` and return 202.

## Reference implementation

`connect_labs/workflow/templates/performance_review.py`:
- `supports_saved_runs: True`.
- Module-level `build_snapshot` returning `{workers, state, summary, opportunity_ids}`.
- `SNAPSHOT_SCHEMA` documenting the shape.
- Render code that reads `view.workers` / `view.state.worker_states` and calls `view.complete(...)` from a "Mark Run Complete" button.

`connect_labs/static/js/workflow-runner.tsx` builds the `view` helper and passes it as a `WorkflowProps` field. `components/workflow/types.ts` defines `RunView` and the related types.
