# Map-based multi-boundary bulk-create into a study

Status: in build (2026-06-05). Drives the `microplans-study-groups` DDD demo.

## Why

The study-groups creation flow lets a planner add wards to a study two ways
today: **by name** (`bulk_create.html` — paste ward names → `resolve_many` → one
plan per ward) and **one at a time in the editor** (`review.html` → `create_plan`
→ redirect to that plan's review). Neither lets the planner work the way she
actually reasons about a controlled study: *look at where the program delivered,
then pick the covered ward and a comparable neighbour straight off a map.*

This adds a third path — **add wards from a map** — that shows one or more
opportunities' service-delivery points as visual context and lets the planner
**multi-select admin boundaries**, creating one boundary-only ward-plan per
selected boundary, filed into the study. It keeps the study-groups model intact
(one plan per ward, arm assigned on the group, sampling done later in bulk), so
blinding stays structural.

## Decisions (locked)

- **New full-page surface**, reached from the study page via a new
  **"+ Add wards from map"** action. On create it redirects **back to the study
  page** (`…/group/<gid>/manage/`) — the "one action → back to planning" hand-off.
- **Built as a reusable, embeddable controller** (`BoundaryBulkPicker`) mirroring
  the existing `ServiceDeliveryLayer` / `AdminBoundariesLayer` host-contract style,
  so the same surface can be embedded in a tabbed UI later without a rewrite.
- **Service-delivery points are visual context only** (multi-opp display via the
  existing `ServiceDeliveryLayer`); boundary selection is manual multi-select.
- **Created plans are boundary-only** (`phase: "boundary"` — `input_areas` set,
  `work_areas` empty). Sampling happens later via the study page's bulk
  **Generate samples**. Arms are assigned later on the study page. This surface
  never touches arms or sampling.
- **DRY**: reuse `da.create_plan` (the existing per-plan create core) in a loop —
  no new per-plan factoring. Extract only the shared *map+layers glue* (init
  `ConnectMap`, mount the boundary + service-delivery layers, expose the selected
  set) into one helper that both `review.html` and `BoundaryBulkPicker` call, if
  and only if it removes real duplication; scope-limited, no unrelated rewrite of
  the 1900-line `review.html`.

## Components

### ① New JS controller — `static/microplans/boundary_bulk_picker.js`

Self-contained, host-mounted, mirrors the existing layer contracts:

```
BoundaryBulkPicker.create({
  map,            // a mapboxgl.Map (host-owned)
  mount,          // element to render the picker panel into
  csrf,           // CSRF token string
  opps,           // [{id, name, program_name?, visit_count?}] for ServiceDeliveryLayer
  currentOppId,   // pre-selected opp chip (optional)
  urls,           // { boundaries_viewport, sd_preview, sd_pipelines, sd_derive, bulk_create }
  onCreated,      // (plan_ids) => {}  — host navigates back to the study on success
}) -> controller { destroy() }
```

Responsibilities:
- Mount `AdminBoundariesLayer` (multi-select; its existing `selected` Map is the
  source of truth) + `ServiceDeliveryLayer` (multi-opp delivery points, visual)
  into the passed `map`.
- Render a footer: **"N wards selected"** + a **"Create N plans"** button
  (disabled at 0).
- On click: POST the selected boundaries to `urls.bulk_create`, then call
  `onCreated(plan_ids)`.
- Own its DOM/listeners; `destroy()` cleans up (so it can be torn down when a
  future tab switches away).

For each selected boundary it sends `{ name, lga, state, boundary_id, geometry }`,
derived from the `AdminBoundariesLayer` feature (name/lga/state via the same
`autofillFromBoundary` logic `review.html` uses — extracted to the shared helper).

### ② Shared map glue (DRY) — `static/microplans/boundary_map.js` (new, small)

Extracts the map+layers setup currently inlined in `review.html`:
`createBoundaryMap({ container, token, center, onAreaAdd, onAreaRemove, opps, … })`
→ returns `{ map, adminLayer, sdLayer, selectedBoundaries() }`. `review.html` is
refactored to call it (removing its duplicated init), and `BoundaryBulkPicker`
calls it too. **Only extracted if the duplication is real after a close read of
`review.html`'s map init; otherwise `BoundaryBulkPicker` consumes the existing
standalone modules directly and this file is skipped.**

### ③ New page — `microplans/add_from_map.html` + `ProgramGroupAddFromMapView`

- Route: `program/<int:program_id>/group/<int:group_id>/add-from-map/`.
- View (`TemplateView`, `_LabsContextSyncMixin`, `LoginRequiredMixin`): supplies
  the program's opportunities (for the ServiceDeliveryLayer chips), `currentOppId`,
  the Mapbox token, the study name, and the URLs above.
- Template: full-screen `ConnectMap` + a mount point; a thin script that calls
  `BoundaryBulkPicker.create(...)` with `onCreated` → `window.location =
  …/group/<gid>/manage/`.

### ④ New endpoint — `ProgramGroupBulkCreateFromBoundariesView`

- Route: `program/<int:program_id>/group/<int:group_id>/bulk_create_from_boundaries/`
  (POST).
- Body: `{ "boundaries": [{name, lga, state, boundary_id, geometry}, …] }`.
- For each item: `da.create_plan(region=name, name=name, mode="sampling",
  pins=empty_fc, hulls=empty_fc, input_areas=[geometry], lga=lga, state=state)`
  → boundary-only plan (no work areas) → `da.add_plan_to_group(group_id, plan.id)`.
- Returns `{ "status": "ok", "plan_ids": [...] }`. Partial-failure handling
  mirrors `ProgramCreatePlanView` (a plan that creates but fails to file into the
  group is reported, not lost).

### ⑤ Study page action — `group.html`

Add **"+ Add wards from map"** next to "+ Add wards by name" / "+ Add a plan in
the editor", linking to the new page. (Empty-state copy updated to mention the
map path.)

## Flow end-to-end

empty study → **+ Add wards from map** → see 1+ opps' delivery points +
multi-select two boundaries (covered ward + neighbour) → **Create 2 plans** →
back on the study with both wards (boundary-only) → assign **intervention/control**
→ **Generate samples** (bulk) → per-plan review + group overlay map →
**comparability** within tolerance → **complete**.

## Reused vs new

| Reused as-is | New |
|---|---|
| `AdminBoundariesLayer` (multi-select), `ServiceDeliveryLayer` (multi-opp), `ConnectMap`, `da.create_plan`, `da.add_plan_to_group`, the study page's arm `<select>` + bulk Generate samples + comparability + blinded CSV | `BoundaryBulkPicker` controller; `add_from_map.html` page + view; `bulk_create_from_boundaries` endpoint; "+ Add wards from map" action; (conditionally) extracted `boundary_map.js` glue + `review.html` refactor to use it |

## Testing

- **pytest** `ProgramGroupBulkCreateFromBoundariesView`: N boundaries → N plans,
  each boundary-only (`phase == "boundary"`, `work_areas == []`, no `arm` on the
  plan), all filed into the group (`group.plan_ids` grows by N); empty list → 400;
  partial-failure path reported.
- **pytest** contract: a plan created via the bulk endpoint is structurally
  identical to one from `ProgramCreatePlanView` for the same geometry (shared
  `create_plan` core).
- **DDD render** exercises the JS end-to-end against labs prog 133 (the demo's
  scene 2–4 drive the map surface → Create plans → back to study).

## Out of scope (future)

- Auto-suggesting which boundaries contain delivery points (point-in-polygon
  tally) — visual context only for now; this seeds the future arm auto-suggest.
- Embedding the surface in a tabbed UI with the editor — the controller is built
  embeddable so this is a later, additive step.
- Any change to arm assignment or sampling (both stay on the study page).
