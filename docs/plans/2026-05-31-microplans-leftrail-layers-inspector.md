# Microplans left-rail: accordion + map Layers/Inspector panel

> **Status:** PLAN for review. Two parallel efforts are in flight — (1) combining
> the setup (create) and review (edit) screens, and (2) a broad code refactor.
> This plan is written **contract-first** so it maps cleanly onto whatever the
> refactor produces. File paths below are *current* best-known targets, marked
> ⚠️REBASE where the combine/refactor will move them. Do not start until the
> user confirms those commits have landed; the first task is an explicit reconcile.

**Goal:** Reorganize the microplans create/edit screen so the left rail is a
collapsible **accordion** of creation/edit steps, and everything *spatial* —
admin boundaries, building footprints, service-delivery GPS, and the per-work-area
details that currently pop up on hover — lives in one **map-docked Layers/Inspector
panel**. Service delivery stays a secondary, sampling-gated layer.

**Architecture:** One shared, mode-parameterized opportunity/context **picker core**
(extracted from the labs context selector; single-select for the navbar,
multi-select for the service-delivery layer). One **accordion rail** primitive for
the steps. One **Layers/Inspector panel** primitive that hosts N toggleable map
layers + a pinned selection inspector. Layers and the inspector are thin adapters
over already-shipped endpoints; almost no new backend.

**Tech stack:** Django templates, Alpine.js (the context selector is already Alpine;
the create/edit map JS is currently a vanilla IIFE — see Task 2 for the bridge),
Mapbox GL JS, Tailwind. Geometry/data backends already exist.

---

## Design reference

High-fidelity mockup: `/tmp/microplans-leftrail/v2.html` (create + edit states,
with the three layers and the inspector). Captures the agreed direction:
- **Accordion rail** (one step open at a time) — B's design language.
- **Map Layers/Inspector panel** docked top-right, two tabs: **Layers** / **Inspect**.
- **Layers:** Admin boundaries · Building footprints · Service delivery `[sampling]`.
- **Inspect:** the clicked work area's details (replaces the hover popup).

---

## The parallel-work reality (read first)

Two other agents are editing the same surface:
- **Combine setup+review:** `setup.html` (create) and `review.html` (edit) merge
  into one screen/flow. My rail + panel must serve **both modes** — the mockup
  already shows a Create/Edit flip. Build the rail/panel as mode-aware components,
  not per-page copies.
- **Broad refactor:** file layout, the vanilla map IIFE, and template structure
  may change substantially. Therefore this plan commits to **contracts** (component
  APIs + behaviors + endpoints), and defers line-level TDD steps to Task 1's
  reconcile, once the post-refactor structure is known.

**Reconcile rule:** if the refactor already extracted a shared map controller or a
component system, adopt it — these tasks describe *what* must exist, the refactor
decides *where*. Re-derive exact paths in Task 1 before writing any code.

### ✅ Reconcile result (after #336 unify + #338 shared FE module)

- **The unified page is `templates/microplans/review.html`** — `ProgramSetupView`
  (create), `ProgramReviewView` + `ReviewView` (edit) all render it. **This is the
  build target.** `setup.html` is now the **legacy opp-scoped** create page
  (`SetupView` only); we build on `review.html` and leave `setup.html` alone
  (deprecate later, out of scope).
- **Foundation = `static/microplans/shared.js` → `window.Microplans`** (vanilla, no
  build): `apiCall`, `post`, `esc`, `getCsrf`, `colorFor`, **`oppColorFor` /
  `OPP_COLORS`** (palette now centralized — drop the local copy in C1/C5),
  `boundsOf` / `fitTo`, `upsertSource`, **`removeSourceAndLayers`** (frees map
  resources on layer toggle-off — exactly what C3 layer toggles need), `debounce`,
  `chip`. Build all layers/panel/picker on these.
- **No map-controller / layer-registry / Alpine map component exists** → C2/C3/C6
  are net-new (no collision). **D1 settled:** map JS stays **vanilla** per page;
  `x-data` is on `review.html`'s root, so the **C1 picker stays an Alpine island**
  mounted inside the SD layer; the panel + inspector stay vanilla using
  `window.Microplans`.
- **The hover popup is still there** (`cellTooltipHTML` + `hoverPopup` +
  `mousemove` wiring in `review.html`) → C6 replaces it. Confirmed target.
- **The Draw/Admin/Pin area input is still there** (`#area-admin`, `#area-pin`) →
  C4's admin layer subsumes the Admin part.
- **Service delivery is NOT in the unified page yet** (it lives only in legacy
  `setup.html`). So **C5 also *introduces* SD to the unified flow**, not merely
  relocates it — re-home `service_delivery_layer.js` (already shared-ified by #338)
  into `review.html` as a Layers-panel layer.
- **Verdict: plan holds, and is cleaner** — every contract maps onto
  `review.html` + `window.Microplans`; the refactor *helps* (centralized colors,
  `removeSourceAndLayers`, `apiCall`). No contract obsoleted; no rework.

---

## Component contracts (the durable spec)

### C1 — `labsContextPicker` (shared opportunity/context picker core)
Extracted from `labs/context_selector.html`'s inline `labsContextSelector()`.
A single Alpine factory + a reusable markup partial, parameterized:

```
labsContextPicker({
  mode: 'single' | 'multi',          // single = navbar; multi = SD layer
  scope: ['org','program','opp'],    // which columns/levels are pickable
  source: { orgs, programs, opps },  // json_script ids, defaults to the global ones
  preselect: <ids>,                  // single: current context; multi: [currentOppId]
  onApply: (selection) => {},        // single default: POST context + reload
                                     // multi default (SD): emit selected opp_ids
})
```
- **Single mode** = today's behavior, byte-for-byte: org→program→opp filtering,
  `applySelection()` URL/param + `/labs/clear-context/`, scout `setTenant`, cache
  tolerance. **No regression** — this is the navbar context selector.
- **Multi mode** = new UX: array selection, removable color-swatched chips
  (color indexes match `points.py` `OPP_COLORS`), search/typeahead, no auto-apply
  on click; an explicit "Show / apply" emits the `opp_ids` list.
- Search, `matchesSearch`, `filteredPrograms/Opps`, `getCookie` are shared.
- **Multi-select is added to the CORE**, so the navbar context selector can later
  opt into multi-select for free (the user's "improvements help both" requirement).

### C2 — Accordion rail (`mp-accordion`)
A collapsible-section primitive for the rail (B's `<details class="acc">` styling):
- One section open at a time (optional `exclusive`); each header shows a title +
  a one-line **summary** of its current value + a chevron.
- Mode-aware section sets:
  - **Create:** Plan type · Area · Sample design (sampling) / Work-area size
    (coverage) · Name & generate.
  - **Edit:** Area & regenerate · Grouping · Assignment · Save/push.
- The rail always ends in **one** primary footer action (Preview / Save).

### C3 — Map Layers/Inspector panel (`mp-map-panel`)
A card docked on the map (top-right), two tabs:
- **Layers tab:** a registry of layer rows, each `{ id, label, color, badge?,
  enabled, onToggle, meta }`, rendered as a swatch + name + toggle. Registering a
  layer is one call so future overlays cost nothing (Principle: scales freely).
- **Inspect tab:** shows the currently-selected work area (C6). Empty state when
  nothing is selected ("Click a work area to inspect").
- The panel is the single home for everything spatial; the rail holds no overlays.

### C4 — Admin boundaries layer (NEW; interactive — subsumes the "Admin" area input)
The admin layer is **three things at once**, driven by the on/off toggle:
1. **Reference:** ON shows **all admin levels we have** for the opp's region as
   outlines (purple `#a855f7`), rendered together. Hierarchies are **country-
   variable** (different depth/labels per country) — the layer is generic over an
   ordered level list, never a hardcoded State/LGA/Ward. Scoped to viewport/region
   for performance. [user: "each level multi-select clickable… other countries have
   different hierarchies."]
2. **Smallest-wins resolution:** hover/click **hit-tests to the smallest (most
   granular) boundary containing the cursor** — that's the active feature, even
   where levels nest. [user: "'selected' boundary is always the smallest thing you
   are hovering/clicking into."] (Selecting a coarser parent instead is an Inspect-
   panel affordance — a "select parent: <name>" action — not the default click.)
3. **Inspect:** the resolved boundary populates the **Inspect** tab — name, admin
   level, parent chain, country, area, population if we have it. (Generic Inspect —
   see C6.)
4. **Area selection (create, area phase only):** in the area-definition phase,
   **Shift/⌘-click toggles** the resolved (smallest) boundary into/out of the plan's
   selected area set — multi-select boundaries directly on the map. Selected
   boundaries fill in; the **Area** accordion live-summarizes ("3 wards · 412 km²").
   This **replaces** today's Country/Level/Search dropdown Admin input. Outside the
   area phase (edit/review), click only inspects — it never mutates the area.
- Data: existing `CountriesView` (`/boundaries/countries/`), `AdminAreasView`
  (`/boundaries/areas/`), `AdminAreaGeometryView` (`/boundaries/geometry/`),
  `core/admin_boundaries.py`. Likely add a small **viewport/level-scoped outline
  batch** endpoint so we fetch all boundaries at a level in one call instead of
  per-area (see Backend touchpoints + Q3).

### C5 — Service-delivery layer (re-home existing #324/#327 work)
- Reuse `PreviewServiceDeliveryView`, `ServiceDeliveryPipelinesView`,
  `DeriveBoundaryView`, and `microplans/service_delivery/` (schema/points/hull) —
  **unchanged backend.**
- Re-home the FE from the standalone "Service delivery" tab into a **Layers panel
  layer**: toggle on → reveal the **multi-select opp picker (C1 multi mode)** +
  pipeline select + "Boundary from points". Carries a soft `[sampling]` hint (it's
  most useful for sampling) but is **available in both Coverage and Sampling** — no
  hard gate, no disable. Just a normal secondary layer.
- Delete the top-level `Plan | Service delivery` tab and the bespoke typeahead in
  `service_delivery_layer.js` (superseded by C1 + C3).

### C6 — Inspector (generic; hover ≠ click; supports multi-select)
The Inspect tab reflects **whatever you interact with on the map, across any
layer** — a work area, an admin boundary, (optionally) a service-delivery point.
Replaces `review.html`'s cursor-following `mapboxgl.Popup` (`cellTooltipHTML`,
`ensurePopup`, the `mousemove`→popup wiring).

- **Hover ≠ click — but both feed the panel, never the map.** Hover **highlights**
  the feature on the map and shows its info **in the Inspect panel as a transient
  preview**; on mouse-out the panel reverts to the pinned selection. **Click pins**
  it. There is **no floating label/popup over the map** — the panel is the single
  text surface. [user: "hover information being in the inspect window not over the
  map."] This is the direct fix for today's cursor-following popup.
- **Work area click → work area AND its group.** The pinned inspector shows the
  work-area details (id, worker, group, buildings, expected visits, status,
  excluded reason) **and** a group block (group name, member count, totals across
  the group, worker(s) in it). One click, two scopes.
- **Multi-select work areas → bulk panel.** **Shift/⌘-click** adds/removes work
  areas [user]. With >1 selected, the Inspect tab switches to a bulk view: "N
  selected", aggregates (total buildings/visits, groups & workers spanned), and
  **bulk actions** — Reassign → worker, Move → group, Exclude. Single-select shows
  the detail+group view; multi-select shows the bulk view.
- **Admin boundary click → boundary info** (C4.2) when the admin layer is on.
- Selection state drives the map (outline/fill) and the panel together; no floating
  text box.

---

## Backend touchpoints

Almost entirely reuse. New/changed:
- **None required** for the happy path — admin/footprints/SD/edit endpoints all
  exist. If admin-boundary outline rendering needs all areas at a level in one
  call (vs per-area geometry), add an optional `level`-scoped batch to
  `AdminAreasView`/a new `admin_area_outlines` endpoint (small, additive). Decide
  in Task 5 after checking the existing payloads.

---

## Sequence (each task ships independently, behind the combined screen)

> Full per-step TDD (failing test → impl → commit) is finalized **inside each task
> during Task 1's reconcile**, against the real post-refactor files. Pure-logic
> pieces (hull, points, picker selection/search, layer registry) get unit tests;
> the live map flows are validated with the WebGL-patched `browse`
> (`/canopy:patch-gstack-browse`) and, as acceptance, the DDD walkthrough.

- **Task 1 — Reconcile + scaffold.** Re-derive exact files against the landed
  combine+refactor. Confirm: where the create/edit template(s) live, whether a
  shared map controller now exists, whether Alpine is mounted on the map page.
  Produce the concrete file map. Output: an updated "Files" block for Tasks 2-7.
- **Task 2 — Picker core (C1), single mode.** Extract `labsContextSelector` → a
  shared `labsContextPicker` factory + markup partial; rewire the **navbar**
  context selector onto it. Acceptance: navbar context selection unchanged
  (org/program/opp, clear, cache tolerance, scout). *(Pure selection/search logic
  unit-tested; behavior verified live.)*
- **Task 3 — Picker core, multi mode.** Add array selection + chips + explicit
  apply to the core. Unit-test multi-select add/remove/chip-color-index.
- **Task 4 — Accordion rail (C2).** Convert the create rail to `mp-accordion`;
  wire mode-aware section sets; keep all existing field bindings + the single
  footer action.
- **Task 5 — Map Layers/Inspector panel (C3) + admin boundaries layer (C4).**
  Build the panel primitive + layer registry; implement the admin-boundaries layer
  (outline render + "Use as area") as the first registered layer. Move the
  footprints toggle into it.
- **Task 6 — Re-home service delivery (C5).** Register SD as a Layers-panel layer
  using the C1 multi picker; sampling-gate it; remove the old tab + bespoke
  typeahead. Re-validate the #324/#327 data path live (opp 1237 → 1001 pts →
  derive boundary) through the new surface.
- **Task 7 — Inspector (C6).** Replace the hover popup with the click-to-pin
  Inspect tab; wire Reassign/Regroup/Exclude to existing endpoints; verify the map
  stays readable while inspecting.
- **Task 8 — DDD acceptance.** Re-run the DDD loop **as designed** (evidence →
  why → spec → coherence → actionability → narrative gate → render + dual-judge)
  on the new surface. Update `docs/walkthroughs/microplans-service-delivery.yaml`
  to the re-homed flow (layer toggle instead of tab; add an admin-boundary scene).
  Ship the deck/video.

---

## Test strategy

- **Pure logic (unit, fast):** `service_delivery/hull.py` + `points.py` (existing
  tests stay green); picker selection/search/chip-color; layer-registry
  enable/toggle. No mocks of the data layer beyond the pipeline boundary.
- **Live (acceptance):** drive the real combined screen on labs via WebGL-patched
  `browse` — navbar context unchanged; accordion open/close + summaries; each layer
  toggles + renders; SD multi-opp + derive boundary; admin outline + use-as-area;
  inspector pins on click and survives neighbour hovers. Watch CloudWatch.
- **DDD walkthrough = the human-facing acceptance artifact** (Task 8).
- **Regression guard:** the navbar context selector is load-bearing across labs —
  Task 2 must prove no behavior change before anything else merges.

---

## Decisions

**Resolved (all [user]):**
- **D1 — Alpine on the map page:** decide after the refactor (Task 1).
- **D4 — Coverage + service delivery:** NO hard gate — SD toggles in both modes;
  soft "sampling" hint only.
- **Q2a — Hover:** highlight on map + info into the **Inspect panel** as a transient
  preview (reverts on mouse-out); click pins. No label over the map.
- **Q2b — Multi-select:** **Shift/⌘-click** to add/remove. (No marquee, no mode.)
- **Q3a — Admin levels:** render **all levels** (generic, country-variable depth);
  hover/click resolves to the **smallest** boundary under the cursor; Shift/⌘-click
  multi-selects to build the area.

**Open (my call unless you object):**
- **Q3b — Admin outline data:** I'll add a small **viewport-scoped outline batch**
  endpoint (all boundaries intersecting the current map bounds, all levels), since
  "all levels for the region" is too heavy to fetch per-area. Additive to the
  existing `/boundaries/*` views; decided in Task 5.
