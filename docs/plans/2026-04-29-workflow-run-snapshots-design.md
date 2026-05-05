# Workflow Runs & Snapshots — Framework Design

**Date:** 2026-04-29
**Status:** SUPERSEDED by `2026-05-04-run-state-final.md`. The architecture (three layers, snapshot at completion, action vs run-shaped templates) survives; the names changed (`supports_snapshots` → `supports_saved_runs`, `frozen` → `completed`) and the entity-stage lift was deferred.

## Problem

A "run" today does not mean "what I saw at the time I ran it." Re-opening usually recomputes against today's data, against the current pipeline schema, against today's worker list. The historical-fidelity guarantee a run *should* carry is missing.

This document defines the run/snapshot framework end-to-end: lifecycle, per-template opt-in, layering against the pipeline cache, and how we collapse the duplicated JS aggregation helpers into a real layer.

## Architecture: three layers

The current code blurs three concerns. Splitting them is what makes the framework work.

```
┌─────────────────────────────────────────────────────────┐
│ 3. RENDER (FE, JS in template)                          │
│    Pure presentation. Reads the snapshot. Filters,      │
│    sorts, draws charts. No data shaping.                │
└─────────────────────────────────────────────────────────┘
                          ▲ reads
┌─────────────────────────────────────────────────────────┐
│ 2. SNAPSHOT (BE, Python/SQL — `build_snapshot`)         │
│    Per-template hook that turns pipeline rows into the  │
│    dashboard shape (entity rows, KPIs, weekly bins).    │
│    Frozen onto run.data["snapshot"] at run save.        │
└─────────────────────────────────────────────────────────┘
                          ▲ reads
┌─────────────────────────────────────────────────────────┐
│ 1. PIPELINE (SQL — existing)                            │
│    Form submissions → typed columns. Cached in          │
│    RawVisitCache → ComputedVisitCache → ComputedFLW-    │
│    Cache. Keyed by (opportunity, pipeline_config_hash). │
└─────────────────────────────────────────────────────────┘
```

### Where the JS helpers go

The four KMC/SAM templates (`kmc_longitudinal`, `kmc_project_metrics`, `kmc_flw_flags`, `sam_followup`) currently do **layer-2 work in the browser**. Each has its own copy of `groupVisitsByChild`, `computeKPIs`, `computeWeightMetrics`, etc. This was not designed; it's a workaround for the pipeline framework only supporting two terminal stages (`VISIT_LEVEL`, `AGGREGATED`/FLW). The schema already declares `linking_field: "beneficiary_case_id"` but no stage acts on it.

**Two-step lift:**

1. **Extend the pipeline framework with an entity-level stage** keyed by `linking_field`. Add a `ComputedEntityCache` model paralleling `ComputedFLWCache`, fed by SQL aggregation over `ComputedVisitCache`. Pipelines that declare a `linking_field` and ask for it become entity-level. Aggregation rules (`first`, `last`, `min`, `max`, `sum`, `count`) reuse the same expression vocabulary already used for FLW-level.
2. **`build_snapshot` becomes a thin Python step** over entity rows: KPI counts, weekly bin objects, sort-stable orderings. No shaping work, just summary.

After this, the three duplicate copies of `groupVisitsByChild` are deleted. The JS render code reads `snapshot.children`, `snapshot.kpis`, `snapshot.weekly_data` — never `pipelines.visits.rows`.

### Layer-1 (pipeline) is the recompute accelerator, not the fidelity store

The SQL cache (`labs/analysis/backends/sql/`) keeps doing what it does: caches raw + computed rows for fast recompute on the **next live run**. It is keyed by `(opp, pipeline_config_hash)`, which is exactly why it cannot serve historical fidelity — it gets invalidated when the schema changes. The snapshot, frozen onto the run, is the fidelity store. We do not put raw or computed pipeline rows on the run; the snapshot is the post-aggregation shape only. (Storage explosion is a real risk; we already saw it.)

## Run lifecycle

Two clean states, plus a transient and an error:

```
        Start Run
   (explicit user action,
    or "Re-run" creates new)
            │
            ▼
        ┌────────┐   build_snapshot succeeds   ┌────────┐
        │pending │──────────────────────────▶ │ frozen │
        └────────┘                             └────────┘
            │
            │ build_snapshot raises
            ▼
        ┌────────┐
        │ failed │   (Re-run = create new run)
        └────────┘
```

- **`pending`** — run created, snapshot not yet written. UI shows progress, not a dashboard.
- **`frozen`** — snapshot written. UI renders from snapshot. **Never recomputes on open.**
- **`failed`** — `build_snapshot` raised. UI shows error + a "Re-run = new run" action.

Two design points worth being explicit about:

1. **Runs are created explicitly, not on page mount.** Today `RunView.get` (`workflow/views.py:203`) creates a fresh run record on every URL hit without `?run_id=`. That's the source of the "every visit makes a new run" footgun. Replace with: list page shows existing runs + a single "Start Run" button. Visiting the workflow URL with no `run_id` shows the list, not a brand-new run.

2. **Re-run = new run.** A run is an immutable artifact. The "Re-run Analysis" button creates a new `pending` run, navigates to it, builds the snapshot, and freezes. The previous run stays visible in the run list. This is consistent with §1 and matches what people actually want when they say "compare to last week."

### When does freeze happen?

`build_snapshot` runs in the existing Celery `run_workflow_job` task. The trigger differs by template family — see the next section.

## Per-template opt-in: `supports_snapshots`

Not every template is run-shaped. Add a boolean to the `TEMPLATE` export:

```python
TEMPLATE = {
    "key": "…",
    "supports_snapshots": True,   # default False
    …
}
```

Two distinct template patterns map cleanly:

### Run-shaped (`supports_snapshots: True`)

A run produces a frozen view of state at a point in time. Snapshot framework applies. Re-run = new run.

| Template               | Layer-2 work today | Lift effort |
|------------------------|--------------------|-------------|
| `mbw_monitoring_v2`    | FE-driven dashData blob (already half-snapshotted) | Already partial |
| `performance_review`   | Trivial worker-state counts | 0.5d |
| `ocs_outreach`         | Worker iteration + task IDs | 1d |
| `kmc_flw_flags`        | Two pipelines + per-FLW JS compute | 2d (after entity-stage) |
| `kmc_longitudinal`     | Visits → children → KPIs in JS | 2d |
| `kmc_project_metrics`  | Visits → children → metrics + weekly in JS | 2d |

For these, the **freeze trigger** depends on whether the template uses a Celery job:
- **Job-driven** (`mbw_monitoring_v2`, `kmc_flw_flags` once it's lifted to BE compute): freeze at end of `run_workflow_job`. No new mechanism.
- **Page-driven** (`performance_review`, `ocs_outreach`, KMC dashboards today): user clicks "Start Run" → BE schedules a `run_workflow_job` whose only job is to read pipeline cache + call `build_snapshot` + freeze. This is the new path. It also gives KMC dashboards a clean transition: today they have no Celery presence; after the lift they do.

### Action-shaped (`supports_snapshots: False`)

The template is an orchestration tool. Its value is in the artifacts it creates (audit sessions, tasks, OCS outreach), which already have their own persistence and historical fidelity in their own models. There is no dashboard worth freezing.

| Template               | Why no snapshots | What we keep |
|------------------------|------------------|--------------|
| `audit_with_ai_review` | Output = audit sessions in audit app | `state` for resumption; linked session IDs |
| `bulk_image_audit`     | Output = audit sessions | Same |
| `sam_followup`         | Output = follow-up audits + image references | Same — audit IDs, photo refs |

Action-shaped templates retain `state` for resumption (so a half-finished session survives a refresh) but no snapshot. The run list shows them differently — "working session" rather than "frozen run" — and the page does not show "as of <date>" framing. Re-opening behaves like resuming a draft, not viewing a historical artifact.

`sam_followup` is the awkward case: it does have a dashboard *and* image-heavy actions. v1 marks it action-shaped; if the dashboard half is valuable on its own we can split it later.

## Storage shape

```
run.data = {
    "definition_id":   …,
    "period_start":    …,
    "period_end":      …,
    "status":          "pending" | "frozen" | "failed",  # snapshot lifecycle only
    "supports_snapshots": True | False,                  # mirrored from TEMPLATE
    "created_at":      …,
    "frozen_at":       …,                                # set when snapshot lands
    "build_error":     …,                                # set on failed
    "state":           {…},                              # FE-written, user inputs
    "snapshot":        {…} | null,                       # BE-written, dashboard shape
}
```

Rules:
- **`state`** is FE-written. Selections, filters, status flags, notes, threshold settings. Anything the user *changes* during the run.
- **`snapshot`** is BE-written. Aggregations, computed metrics. Anything *derived* from pipeline data. The FE only reads it.
- Anything currently in both (e.g. `monitoring_session`, `flw_results` in MBW V2) gets one canonical home — and overlap is a code smell from the FE-driven-snapshot era.
- Inline JSON storage. Snapshots stay reasonable in size because they're entity-rows + summary, not raw rows. A soft size guard (configurable per-template, default ~5 MB) logs and rejects oversize snapshots — that's a signal the template is shaping wrong.

## What this design explicitly does not do

- **Reproduce analysis from raw inputs.** Raw rows are not on the run. If render code or pipeline schema changes, old runs render the snapshot we froze; new runs use the new shape. Frozen is frozen.
- **Repurpose the SQL cache as a fidelity store.** It's keyed to current pipeline config and shared across runs. It stays a recompute accelerator.
- **Versioned snapshot history per run.** A run captures one snapshot. Re-run creates a new run. Add per-run versioning later only if a use case appears.
- **Snapshot action-shaped templates.** They opt out. Their artifacts live in their own models with their own historical fidelity.

## Migration

Existing runs:
- **Has snapshot** → `status = frozen`, `frozen_at = snapshot.timestamp` (else `created_at`), `supports_snapshots = True`.
- **No snapshot, run-shaped template** → `status = frozen`, `snapshot = null`, `legacy = true`. UI shows "this run predates snapshots; data not available." No live recompute.
- **Action-shaped template** → `supports_snapshots = false`, `status` is irrelevant; render as a working session.

MBW V2's existing FE-driven save endpoint stays in place during the cutover, gets removed once the Python `build_snapshot` is verified equivalent.

## Deliverables (in dependency order)

1. **Pipeline-framework: entity-level stage** (`labs/analysis/`)
   - `ComputedEntityCache` model + SQL aggregation keyed by `linking_field`.
   - Schema honors `linking_field` and a new `entity_aggregation_rules` block.
   - Cache invalidation parallels FLW-level.
2. **Run framework: lifecycle + storage** (`workflow/`)
   - `supports_snapshots` flag on `TEMPLATE`.
   - Status enum `pending | frozen | failed`; `frozen_at`, `build_error`, `legacy`.
   - `RunView.get` no longer auto-creates runs; list-first navigation.
   - Explicit "Start Run" / "Re-run = new run" UI.
   - Generic `build_snapshot` hook + `run_workflow_job` integration.
   - Generic `GET /workflow/api/run/<id>/snapshot/` replaces per-template snapshot views.
   - Size guard.
3. **Per-template lifts** (parallel, after 1+2):
   - **Tier A (no entity stage needed):** `performance_review`, `ocs_outreach`. Trivial snapshot builders.
   - **Tier B (entity stage consumer):** `kmc_longitudinal`, `kmc_project_metrics`, `kmc_flw_flags`. Delete the JS helpers; pipeline returns child-level rows; `build_snapshot` produces KPIs + weekly. Render code becomes presentation-only.
   - **Tier C (already partially snapshotted):** `mbw_monitoring_v2`. Port `dashData` build into Python, retire `MBWSaveSnapshotView` and the FE save callback.
   - **Action-shaped (no lift):** `audit_with_ai_review`, `bulk_image_audit`, `sam_followup`. Mark `supports_snapshots = false`, validate the working-session UI.
4. **Migration script** — backfill `status` / `supports_snapshots` / `legacy` flags on existing runs.
5. **Docs** — `WORKFLOW_REFERENCE.md` gets the contract for `supports_snapshots`, `build_snapshot`, the entity-stage rules, and the layer separation. `CLAUDE.md` workflow section updated.

Estimated total: ~3 working weeks if linear; less with the per-template lifts in parallel after the framework lands.

## Open questions

1. **Entity-stage naming.** Is "entity" the right name when we already have `linking_field`? Could call it `linked` or `grouped`. Bikeshed before merging.
2. **Multi-opp + entity stage.** `performance_review` is `multi_opp`. The entity stage needs to be coherent across opps; we'll need to decide whether `linking_field` is opp-scoped or globally unique. Probably opp-scoped with the snapshot tagging each row by `opportunity_id` (already the multi-opp contract per `WORKFLOW_REFERENCE.md §8`).
3. **Pipeline-cache invalidation during freeze.** If the SQL cache is invalidated mid-build, `build_snapshot` could read partial data. Wrap the build in a "cache stable for duration of task" guard, or accept that `failed` runs can result and the user re-runs.
4. **S3 export.** `s3_export.upsert_workflow_run` mirrors run state to S3. Should it mirror the snapshot too? Probably yes for `frozen` runs — gives external consumers the frozen view without touching the LabsRecord API.
5. **Entity stage as a separate PR.** Lifting the JS helpers to Python *without* the entity stage is possible (port the JS shaping into Python verbatim), but doesn't deliver the SQL performance win. Worth deciding whether to ship the snapshot framework first with Python-side shaping, then follow with the entity stage as an optimization, or to bundle them.
