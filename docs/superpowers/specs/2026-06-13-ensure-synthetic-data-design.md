# `ensure_synthetic_data` — a composite-manifest synthetic-environment dispatcher

**Date:** 2026-06-13
**Status:** Design approved; ready for implementation plan.

## Problem

Walkthrough/DDD demos need a specific synthetic environment to exist on labs prod
before the recorder drives the scenes. Today that environment is created two
incompatible ways:

- **Declarative (recent):** `connect_labs/labs/synthetic/generator/` — a
  single-opportunity `Manifest` (`from_yaml`) that produces visit fixtures + the
  survey signal: `flw_personas` (with `display_name`), `beneficiary_cohorts` +
  `field_distributions`, `anomalies` (flags), `coaching_arcs`, `tasks`,
  `image_config`, `kpi_config`. Validated, reproducible. Used by study-design,
  verified-monitoring, self-service.
- **Imperative (older):** per-demo seeders like
  `connect_labs/labs/synthetic/program_admin_demo.py` — hand-written code that
  builds the *workflow* layer the manifest doesn't: weekly `chc_nutrition` saved
  runs, run-linked `AuditSession`s, tasks, flags, and a cross-opp
  `program_admin_report` rollup, across multiple opps.

The Program Admin Report (PAR) walkthrough uses **only** the imperative path. As a
result, things the manifest already models declaratively are hand-rolled in PAR
and drift from the scenes:

| Hand-rolled in `program_admin_demo.py` | Manifest models it natively |
| --- | --- |
| `DISPLAY_NAMES` map | `flw_personas[].display_name` |
| `_auto_flags_for_row` seeding | `anomalies[]` |
| canned `synthetic-muac-coaching` bot + `loadBots` wiring | `coaching_arcs[]` |
| audit MUAC photos wired by hand | `image_config` |

Because the imperative seed and the walkthrough scenes are coupled only by
`.run_ids.json` id-substitution — with **no declarative guarantee that the
demo-relevant states match what the scenes expect** — every render surfaces a new
state/scene mismatch (missing real names, missing coaching bot, an audit that
isn't completable on its run, etc.). The render becomes whack-a-mole, and the
setup command additionally resolves its working directory to the wrong git
worktree when several checkouts exist on the machine.

## Goals

1. One declarative **composite environment manifest** per demo: the single source
   of truth for *what environment must exist*.
2. A single idempotent entry point — `ensure_synthetic_data(env_yaml)` — that
   realizes the declared environment: **reuse what exists (by stable key), create
   what's missing, rebuild only what's explicitly marked `reset`**.
3. Per-opp **data** expressed as standard `Manifest` files, so display names,
   flags, and coaching become declarative (deleting the hand-rolled equivalents).
4. Migrate PAR onto this as the first adopter; design so study /
   verified-monitoring can adopt later without core changes.
5. Walkthrough `setup:` becomes a server-side **module run**, eliminating the
   cross-worktree `cwd` quirk.

## Non-goals

- Migrating verified-monitoring / study-groups / self-service now (they keep
  working on their current paths; they adopt later).
- Changing the `Manifest` schema's data/signal model (it already suffices for
  per-opp data). New behavior lives in the *ensure* layer, not the data layer.
- A plugin/registry abstraction. Dispatch is a **plain dict** `{kind: ensurer}`
  in one module; promote to a registry only if a future demo needs a novel kind.

## Architecture

### Composite env manifest

A new YAML per demo, e.g.
`connect_labs/labs/synthetic/envs/program-admin-report.yaml`:

```yaml
env: program-admin-report
timeline:
  completed_weeks: 4          # trailing complete Mondays — the PAR window
  include_current_week: true  # the in-progress manager-flow week, outside the window
resources:
  - kind: opp_data
    opportunity_id: 10000
    manifest: manifests/par-northern.yaml
  - kind: opp_data
    opportunity_id: 10001
    manifest: manifests/par-southern.yaml
  - kind: weekly_runs
    opportunity_ids: [10000, 10001]
    template: chc_nutrition_analysis
    missed_week_idxs: { 10001: [2] }   # Southern misses week 2 -> reads BELOW
    current_week: { reset: true }      # live-recorded week rebuilt fresh each render
  - kind: run_audits
    source: anomalies                  # flagged FLWs from the manifests' anomalies + image_config
  - kind: tasks
    source: coaching_arcs              # coaching tasks from the manifests' coaching_arcs
  - kind: rollup
    opportunity_ids: [10000, 10001]
    template: program_admin_report
```

The composite manifest is validated by a pydantic model (`EnvManifest`) mirroring
the existing `Manifest` validation style. `timeline.completed_weeks` is resolved to
concrete ISO Mondays **at ensure-time** (the dynamic-window logic moves out of
`regenerate.py` into the engine), so the demo is always current-dated.

### Ensure engine

`connect_labs/labs/synthetic/ensure.py`:

- `ensure_synthetic_data(env_path) -> Realized` — loads + validates the env
  manifest, resolves the timeline, then walks `resources` **in order**, dispatching
  each by `kind` through a plain dict to its ensurer. A mutable `EnsureContext`
  threads created ids forward (opp → weekly runs → audits/tasks → rollup).
- Each ensurer implements one verb: `ensure(resource, ctx) -> dict` and:
  - computes a **deterministic stable key** for each record it owns
    (e.g. `weekly_run = (opportunity_id, week_start_iso)`;
    `audit = (workflow_run_id, flw_id)`),
  - looks up the existing labs-only record by that key,
  - **reuses on match, creates on miss**,
  - honors `reset: true` (on the resource or a sub-part like `current_week`) by
    deleting matching records first, then creating.
- Output: a `Realized` object serialized to `realized.json` in the run dir —
  the id map (par_run_id, good_audit_id, …) the walkthrough's `${...}`
  substitution consumes. **Replaces `.run_ids.json`.**

CLI / module entry: `python -m connect_labs.labs.synthetic.ensure <env.yaml>
[--out realized.json]`. Runs in-app (server-side local backend per
`docs/SYNTHETIC_OPPS.md`), so it reaches labs-only opps without HTTP and without a
worktree-relative `cwd`.

### The five PAR ensurers (`connect_labs/labs/synthetic/ensurers/`)

Each is a clean port of logic currently in `program_admin_demo.py` — no callbacks
into the old module.

1. **`opp_data`** — loads the referenced `Manifest`, runs the existing generator
   to produce/refresh the opp's visit fixtures + the per-FLW signal. Key:
   `opportunity_id`. Establishes personas (with real `display_name`), per-FLW
   approval/SAM/MAM distributions, anomalies, coaching arcs, image config.
2. **`weekly_runs`** — for each opp × resolved week, ensures a `chc_nutrition`
   saved run whose pipeline snapshot is derived from the manifest's personas +
   timeline (completed weeks → `completed`; current week → `in_progress`, no
   seeded audits/tasks). Key: `(opportunity_id, week_start)`.
3. **`run_audits`** — for each flagged FLW (from manifest `anomalies`,
   `reviewer_visible_in: [audit]`), ensures a run-linked `AuditSession`
   (`labs_record_id = workflow_run_id`) with MUAC images from `image_config`,
   carrying the real `flw_name`. Key: `(workflow_run_id, flw_id)`.
4. **`tasks`** — ensures coaching tasks from manifest `coaching_arcs` (transcript,
   real `flw_name`, real creator name). Key: `(workflow_run_id, flw_id, archetype)`.
5. **`rollup`** — ensures the cross-opp `program_admin_report` run watching the
   weekly runs, with the window/state the report reads. Key: `program + opp set`.

### PAR per-opp manifests

`par-northern.yaml` / `par-southern.yaml`: port the archetype roster to
`flw_personas` (every persona gets a real `display_name`), flags to `anomalies`,
the coaching transcripts to `coaching_arcs`, MUAC photos to `image_config`. Reuse
the existing `Manifest` validator.

### Walkthrough integration

The PAR spec's `setup:` block changes from
`command: python scripts/walkthroughs/program-admin-report/regenerate.py`
(`outputs: .run_ids.json`) to
`command: python -m connect_labs.labs.synthetic.ensure connect_labs/labs/synthetic/envs/program-admin-report.yaml`
(`outputs: realized.json`). Scene `${...}` vars are unchanged in spirit (same
names: `par_url`, `good_audit_id`, …) — they now resolve from `realized.json`.

Because the env is now a declared, guaranteed contract, the scenes run against a
known-good state: real names present, flags present, the coaching conversation
defined, and the live-week audit in a **completable** state (so the
"Complete Image Review" → workflow-list redirect the recorder waits on actually
fires).

## What this fixes (traceable to today's failures)

- Real worker names → from `flw_personas[].display_name` (no `DISPLAY_NAMES`).
- Coaching bot scene → coaching is declared in `coaching_arcs`; the task carries a
  real transcript, removing reliance on the canned-bot/`loadBots` race.
- Audit-complete → workflow-list redirect → `run_audits` guarantees the audit is
  run-linked and completable, so the redirect condition (`workflowRunId &&
  opportunityId`, all photos decidable) holds.
- Cross-worktree `cwd` → setup is a module run, server-side.

## Testing

- Unit test per ensurer: stable-key computation, reuse-vs-create, `reset`.
- Engine test: resource ordering + context threading + `realized.json` shape,
  against a fake/local labs backend.
- Golden env-manifest test: a small `EnvManifest` validates + realizes end-to-end.
- PAR manifest structural test: every persona has a `display_name`; every
  `anomaly`/`coaching_arc`/`task` references a real `flw_id` (reuse `Manifest`
  validation).
- Real-postgres view coverage for the created records where applicable (don't
  trust mocked DA — prior labs bugs shipped green behind mocks).

## Rollout

1. Build engine + `EnvManifest` + the five ensurers + tests.
2. Author PAR manifests + the env manifest.
3. Point the PAR spec's `setup:` at the env manifest; confirm a clean DDD render.
4. Retire `program_admin_demo_seed` (or leave a thin shim) once the render is green.
5. Later, separate efforts: study / verified-monitoring author env manifests +
   any ensurers unique to them.

## Risks

- **`weekly_runs` snapshot fidelity.** Deriving the chc pipeline snapshot from the
  manifest must match what the live chc render expects (the current imperative
  path builds rows directly). Mitigation: port the existing row-builder into the
  ensurer verbatim first, refactor second; assert the rendered table against a
  known manifest.
- **Window logic.** Moving dynamic-window resolution from `regenerate.py` into the
  engine must preserve "trailing N complete Mondays + current week." Mitigation:
  port `compute_week_window` directly + unit-test it.
- **Scope creep into a registry/platform.** Held off by decision: plain dict
  dispatch until a second demo forces the issue.
