# Microplans — design north-star (agent reference)

> Self-note, not a review artifact. Captures the agreed direction for evolving
> the current `rooftop_surveys` app into a general **microplan generator**.
> jjackson's calls (2026-05-28): rename to `microplans`; shared core + thin
> sampling/coverage layers + thin UI; admin boundaries need per-country bespoke
> overrides on top of Overture. Plan only — nothing renamed/built yet.

## Thesis

connect-gis and rooftop are two flavors of one job: **footprints → cluster →
WorkAreas → assign to FLWs → push to Connect microplanning → monitor.** They
differ only at the select/cover step and the monitoring emphasis. So: one
`microplans` app, two modes.

- **Coverage microplan** — visit *every* household in an area, **balanced FLW
  workloads**, contiguous assignment. Optimizes completeness + even effort.
  Connect microplanning is natively coverage-shaped (`expected_visit_count`,
  `WorkAreaGrouper`, status → "expected reached"). This is connect-gis's job.
- **Sampling microplan** — visit a **statistically-selected subset** (PPS +
  strata + 8 primary/8 alternate + design weights) for unbiased inference /
  verification. Monitoring emphasizes the 15m GPS gate + substitution flow
  (must hit the *specific* sampled house). This is rooftop's job (built).

Don't port connect-gis's *code* (Flask + GEE 5k-cap + whole-country PostGIS) —
we've superseded all three with the Django labs app + lazy DuckDB→Overture.
Port its *algorithms* onto our substrate.

## Target architecture

```
connect_labs/microplans/
├── core/
│   ├── footprints.py      # Overture buildings via DuckDB (built; from sampling/)
│   ├── boundaries.py      # admin-area RESOLVER (Overture default + per-country override)
│   ├── overture.py        # shared DuckDB/S3 helper (built)
│   ├── geo.py             # projection helpers (built)
│   ├── clustering/        # strategy layer (see below)
│   ├── workarea.py        # pins/areas → Connect WorkArea payloads (built)
│   └── data_access.py     # persist area/frame as LabsRecords (built)
├── sampling/              # MODE: PPS + strata + 8+8 + design weights (built)
├── coverage/              # MODE: assign-all, balanced workloads (NEW)
├── monitoring/            # mode-aware analytics (sampling built; coverage = % expected reached)
├── qc/                    # validation cascade (built)
├── views.py               # thin: mode toggle → core + mode layer
└── templates/microplans/  # thin UI: shared map + mode-specific config panel
```

`clustering/` strategies (selectable in UI):
- `kmeans_merge` — current (sampling-tuned: k-means + merge<16). **built**
- `balanced_kmeans` — connect-gis `KMeansConstrained`, equal buildings/cluster → even FLW workloads (coverage). **port**
- `grid` — connect-gis grid overlay + balanced cells, systematic coverage. **port**

Post-cluster step is mode-specific:
- sampling → `select_psus` (PPS) + strata + `sample_pins` (8+8) + weights. **built**
- coverage → assign *all* buildings (or all in chosen clusters) to FLWs balanced;
  cluster → WorkArea with `expected_visit_count = building_count`. **NEW**

Area input options (UI): draw polygon (built), pick admin area (built),
**"buildings around a pin"** (connect-gis expanding-box → target count). **port**

## Admin boundaries — resolver with per-country overrides (important)

Overture's `divisions` theme is the global *default*, but its quality varies and
we will often need better. So `core/boundaries.py` is a **resolver**, not a hard
Overture call:

```
resolve(country, level) →
  1. registered bespoke source for `country`?  use it.   (override; country-level)
  2. else → Overture divisions.                          (default)
```

- Bespoke sources are registered per country (GeoJSON/dataset). The existing
  `labs.admin_boundaries` app (curated geoBoundaries/OSM/GRID3 for ~14 countries,
  incl. the Nigeria ward work) becomes *one* bespoke source the resolver can use
  — don't discard it.
- Override granularity = **country** (e.g. "for NG use GRID3 wards; everyone else
  Overture"). A registry (`country_sources` style) maps country → source.
- Until we audit Overture vs bespoke per geo, **assume bespoke wins where it
  exists.** New geos: start on Overture, add a bespoke override when needed.
- Open: a small ingest path to register a new country's bespoke boundary set
  (upload GeoJSON → store → resolver picks it up). Mirror connect-gis's tiled
  ingest pattern only if a dataset is too big for a single file.

## Plan lifecycle — `boundary` → `sampled` (the bit that confuses)

A plan's **`phase`** is *derived* from whether it has work areas yet
(`PlanRecord.phase`): `"boundary"` (area defined in `input_areas`, **no work
areas**) → `"sampled"` (work areas exist). The two modes reach `sampled`
differently — this is the single most confusing thing about plan creation, so:

- **Coverage** is **sampled at creation**. Gridding a ward into cells is cheap and
  deterministic, so `create_plan` materialises the grid in one step (work areas =
  the cells).
- **Sampling** is **two-step**. A plan is created **boundary-only** (a deliberate
  placeholder — *not* a broken/empty plan), then the PSU sample (PPS →
  primary/alternate) is drawn as a separate, config-driven pass. The split exists
  because sampling is tunable (PSU count, sources, confidence) and, for a two-arm
  study, **every arm must be sampled with one shared config** for comparability.

Creation entry points (all converge on `data_access.create_plan` →
`plan.materialize_work_areas`):

| Surface | Path | Produces |
| --- | --- | --- |
| Single-plan editor | "Generate sample" → "Create sampling plan" | a sampled plan (one step) |
| Study "add wards from map" | `ProgramGroupBulkCreateFromBoundariesView` → `tasks.create_boundary_plan` | boundary-only sampling plans, filed into the study |
| Bulk-create page | `ProgramBulkCreatePlansView` → `bulk_create_plans_task` → `tasks.create_boundary_plan` | coverage = gridded now; sampling = boundary-only |
| Study "Generate" | `ProgramGroupGenerateView` → `generate_group_samples_task` | draws the PSU sample for every boundary-only member (→ `sampled`) |

Both bulk paths share `tasks.create_boundary_plan` (one consistent `input_areas`
shape). Per-work-area provenance (`sample_type` = primary/alternate, `cluster`,
`weight`, …) rides in the work area's `properties` k-v bag (typed by
`workarea.WorkAreaProperties`) — labs-side now, Connect `case_properties` later.

## Monitoring — one dashboard, two lenses

- coverage: % of expected visits reached per area/cluster, workload balance,
  remaining buildings. Connect already tracks expected-vs-approved — mostly a
  read.
- sampling: the GPS-15m / believed-reached / fallback / completion analytics
  (built).

## Migration / PR sequence (for me)

1. **Rename** `rooftop_surveys` → `microplans` (app dir, `INSTALLED_APPS`, URL
   namespace, imports, tests). Keep a `/rooftop-surveys/` → `/microplans/`
   redirect or dual-mount briefly — it's deployed + a real opp may reference it.
2. **Extract `core/`** (move footprints/overture/geo/boundaries/workarea/
   data_access; sampling imports from core). Pure move + import fixups; tests stay green.
3. **`clustering/` strategy layer** — wrap current k-means as `kmeans_merge`,
   add `balanced_kmeans` (port `KMeansConstrained`). One PR, property-tested.
4. **`coverage/` mode** — assign-all balanced; cluster→WorkArea w/ expected_visit_count.
5. **boundaries resolver + country override registry** (+ wire `labs.admin_boundaries`
   as a bespoke source). One PR.
6. **`grid` clustering** + **around-a-pin** area input.
7. **UI mode toggle** (sampling | coverage) swapping the config panel; map shared.
8. **coverage monitoring** lens.

Each step is its own PR, reuses ~80% of the sampling plumbing, tests green throughout.

## Don't port from connect-gis
GEE; whole-country PostGIS bulk ingest; the Flask app/UI; health-facility/ward
PostGIS tables (Overture divisions + the sampling reference-point cover those).
