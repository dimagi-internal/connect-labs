# Study groups: plan groups as first-class plan management

Status: in build (2026-06). Owner: Maya-flow rooftop study.

## Why

A controlled rooftop-survey impact study needs **two comparable arms** — an
intervention area (where the program ran) and a control area (a comparable
neighbour where it did not). We originally modelled this as a *single plan with
two arms* baked in. That conflates two execution units into one object and makes
blinding a thing we enforce defensively (strip `arm` on export).

The right unit is: **a plan = one area's microplan = one Connect opportunity**, and
**a study = a named *group* of those plans**, with the arm assignment living only on
the group. This makes blinding **structural** (the executable plan has no `arm` to
leak), generalises past exactly-two-arms (matched pairs, stepped-wedge), and reuses
most of what's already built (plan groups, bulk-create, the sampling engine, the
compare page, the review-map surface).

The plan group therefore becomes a **first-class plan-management hub**, not a
one-off bulk-create page: you add/remove plans, sample them together, view them
overlaid on one map, compare them, and export each to its own opportunity.

## Decisions (locked)

- **One group per plan.** A plan belongs to exactly one group (keeps arm
  assignment unambiguous).
- **One opportunity per plan.** Each plan exports to its own Connect opportunity —
  cleanest blinding (a surveyor only ever sees one ward) and reuses the existing
  plan→CSV→opp path unchanged.
- **Arm is labs-side only**, on the group. Plans and their work areas carry no
  `arm`. Blinding is by construction (nothing to strip).
- **Arms auto-suggested from the delivery overlay** (the ward the program's
  delivery fills = intervention), planner-overridable.

## Mental model

- **Plan** — one area's (one ward's) microplan. Has a boundary; *optionally* a
  generated sample; exports to one opportunity. New explicit state: a plan may be
  **boundary-only** (`phase: "boundary"`) before the algorithm runs.
- **Group** — a named, managed set of plans + a multi-plan map + (for studies)
  arm assignments and a shared sampling config. The management hub.
- **Bulk-create** — promoted from a standalone page to the primary "add plans"
  action inside a group.

## Data model

### `PlanRecord` (extend)

- **`phase: "boundary" | "sampled"`** — a plan can exist boundary-only
  (`input_areas` set, `work_areas` empty) before sampling. Inferred from
  `work_areas` presence today; make it explicit so the UI and group ops can branch
  on it cleanly.

### `PlanGroupRecord` (extend — today: `name`, `plan_ids[]`, `offered_to`, `shared`, `created_at`)

```jsonc
{
  "name": "Madobi CHC rooftop study",
  "plan_ids": [501, 502],
  "kind": "bundle | study",                              // study ⇒ arms assigned
  "arms": { "501": "intervention", "502": "control" },   // study only, labs-side
  "sampling_config": { /* sources, confidence, target_clusters … */ },
  "status": "defining | sampled | reviewed | exported"
}
```

## Surfaces

### ① Group landing page (NEW) — `/program/<id>/group/<gid>/`

The holistic management page. Lists member plans (name · ward · mode · phase ·
KPIs · arm). Actions:

- **Add plans** (two paths, below) · **Remove plan**
- **Assign arms** (study) — auto-suggested from delivery, planner-overridable
- **Generate samples** across the group (bulk, shared `sampling_config`)
- **View on map** → the multi-plan overlay
- **Compare** → the existing compare page (+ area/density + arm pairing)
- **Export** → per-plan to Connect, blinded

### ② Multi-plan map (NEW) — `/program/<id>/group/<gid>/map/`

Reuses `review.html`'s Mapbox + `map_panel.js` layer registry wholesale — each
plan is one `registerLayer` (its boundary + work areas), coloured by plan (or by
arm for studies). **View-only**: no draw, no edit accordions, no regenerate. The
Inspector tab already exists → click a work-area, see its plan/arm.

## Adding plans — two paths (both reuse existing flows)

1. **By ward name (bulk)** — the existing `resolve_many` + bulk-create, surfaced
   as "Add wards" on the group page. Paste names → confirm matched boundaries →
   create plans *into this group*. Toggle: boundary-only vs sample-now.
2. **By the plan editor (one at a time)** — open the existing editor, define one
   boundary (admin pick or draw), **Save boundary** → creates a `phase:boundary`
   plan → added to the group. Only new bit: a "save boundary, don't generate"
   exit in the editor.

## Apply the algorithm across the group

A single group action **"Generate samples"** runs `generate_frame` over every
`phase:boundary` member plan using `group.sampling_config` (so every arm samples
identically). Reuses the bulk Celery pattern coverage bulk-create already uses.

## Study layer (thin, on top)

- A group becomes a **study** when `arms` are set.
- **Comparability** = the compare page, pairing intervention vs control on
  building count / **area km²** / **density** / matched-or-not. The math already
  exists in `ArmComparabilityView`; lift it across two plans and add the two
  missing columns. No new engine.

## Connect export

Per-plan → one opportunity's work-area CSV (existing path), no arm anywhere.

## Reused vs new

| Reused as-is | New |
|---|---|
| Plan groups; `resolve_many` + bulk-create; sampling engine (per-area); compare page; `review.html` map + `map_panel.js` layers; arm-labs-side blinding | Group landing page; multi-plan view-only map; boundary-only plan save; `arms` + `sampling_config` on the group; comparability across two plans (area/density + pairing) |

## Build order (each step independently usable, TDD all the way through)

1. **Boundary-only plan save + group landing page** — add/remove plans via both
   paths. Bulk-create becomes group management.
2. **Multi-plan overlay map** (view-only) — "go look at them."
3. **Group "Generate samples"** (bulk-apply across members).
4. **Study layer**: arm assignment + comparability (compare-page pairing +
   area/density).
5. **Per-plan blinded export.**

## Acceptance criteria (the why-brief, per step)

- **S1** — A group landing page lists its member plans and supports add (by ward
  name *and* by boundary-only editor save) + remove; a plan can be saved
  boundary-only (`phase:boundary`, no work areas) and appears in its group.
- **S2** — From the group page, "View on map" renders >1 plan's boundaries +
  work areas on one read-only map, coloured per plan/arm, with click-to-inspect.
- **S3** — "Generate samples" samples all boundary-only member plans on one
  shared config; they flip to `phase:sampled`.
- **S4** — A study group shows intervention vs control side by side on building
  count / area / density with a matched / not-matched flag; `arm` never appears
  on any plan or work area.
- **S5** — Each member plan exports to its own opportunity's work-area CSV with
  no `arm` in any column or case property.

## DDD comes after

We are at a design breakthrough reached in conversation, not via a demo loop —
DDD's front-half (concept) is already done. The remaining uncertainty is the
*build*, which only building reveals. So: build steps 1–5 with superpowers/TDD,
deploy, then run the DDD render→judge loop against the **real** surfaces to write
and polish the demo narrative. This doc is the build spec + acceptance story.
