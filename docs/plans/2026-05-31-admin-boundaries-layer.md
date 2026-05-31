# Admin-boundaries map layer (issue #347)

> **Status:** DESIGN, approved for planning. Implements contract **C4** of
> `docs/plans/2026-05-31-microplans-leftrail-layers-inspector.md`. The map-panel
> foundation (#342) and work-area inspector (#344) it builds on are already merged.

## Goal

Add a **Boundaries** layer to the microplans map panel on `review.html` (the unified
create/edit page) that:

1. **Reference** — toggle on → render the admin boundaries we have for the opp's
   region as purple (`#a855f7`) outlines, **all levels together**.
2. **Inspect** — hover/click a boundary → its info (name, admin level, parent chain,
   country, area, population) into the panel's **Inspect** tab via the existing
   `panel.setInspect(html)` API. No floating popup.
3. **Area selection (area-definition phase only)** — Shift/⌘-click (or a search-result
   click) toggles the resolved boundary into/out of the plan's selected area set; the
   **Area** rail section live-summarizes ("3 wards · 412 km²"). This **replaces** the
   current Country/Level/Search **Admin** area-input dropdown. Outside the area phase
   (edit/review), click only inspects.

## Locked decisions

These were settled with the user and must not be relitigated:

- **All levels rendered together**, generic over a country's hierarchy (variable
  depth/labels per country — NOT hardcoded State/LGA/Ward).
- **Smallest-wins resolution** — hover/click hit-tests to the **smallest (most
  granular)** boundary containing the cursor. Selecting a coarser parent is an
  Inspect-panel affordance ("select parent: <name>"), not the default click.
- Multi-select gesture is **Shift/⌘-click** (matches the work-area bulk-select pattern).
- **User-facing label is "Boundaries"** — both the map-panel layer label and the
  area-input segment button. ("Admin" is internal jargon; the internal layer id stays
  `admin` in code.)
- **Viewport endpoint is a pure bbox query** — no country-resolution step. `iso`/`level`
  are optional narrowing filters only. Naturally multi-country.
- **Simplify rendered outlines, fetch exact geometry on select** — the viewport payload
  is simplified (tolerance scaled by zoom) for a responsive map; when a boundary is
  selected into the plan area, its full-resolution geometry is fetched separately so the
  area is accurate.
- **Keep a direct search/input path** — the layer's panel body has a name-search box;
  both it and map Shift/⌘-click feed the *same* selected-area set. The old
  Country/Level/Search *dropdown mechanics* are replaced, but "find a boundary by typing
  its name" is preserved.

## Backend

### New endpoint: `BoundaryViewportAPIView`

- **URL:** `api/viewport/` in `commcare_connect/labs/admin_boundaries/urls.py`
  (name `viewport_api`), alongside the existing `api/map/`.
- **Input** (query params):
  - `bbox=minLng,minLat,maxLng,maxLat` — **required**. Parsed into a `Polygon` (SRID 4326).
  - `zoom` — optional float; drives simplification tolerance.
  - `iso`, `level` — optional narrowing filters.
- **Query:** `AdminBoundary.objects.filter(geometry__intersects=bbox_polygon)` (the
  docstring-blessed GeoDjango pattern; GEOS/GDAL are wired per `config/settings/base.py`).
  Apply `iso_code=` / `admin_level=` when provided.
- **Geometry:** served via `.simplify(tolerance, preserve_topology=True)` where
  `tolerance` is derived from `zoom` (coarser when zoomed out). A small zoom→tolerance
  table; default tolerance when `zoom` absent.
- **Payload:** GeoJSON `FeatureCollection`. Per-feature properties:
  `boundary_id`, `name`, `name_local`, `admin_level`, `iso_code`, `area_km2`,
  `population`, and parent info (`parent_boundary_id`, parent name from
  `extra.parent_names` when present) for the Inspect parent chain.
- **Feature cap:** hard limit (default **1500**). If the intersect set exceeds it, return
  the largest-`area_km2`-first slice and a top-level `truncated: true` flag. The FE
  surfaces "zoom in to see all boundaries" — **no silent truncation** (logged).

### Full-resolution geometry on select

When a boundary is selected into the area, fetch its **exact** polygon (not the simplified
viewport copy) by `boundary_id`. Reuse the existing resolver path
(`core/admin_boundaries.py` `BoundaryResolver.geometry(area)` / `AdminAreaGeometryView`)
rather than adding a new exact-geometry endpoint. (Confirm payload shape in Task 1; add a
thin `boundary_id`-keyed lookup only if the existing endpoint can't serve it directly.)

### Reuse / unchanged

`BoundaryGeoJSONView` (whole-country, too heavy for all-levels), `BoundaryMapAPIView`,
`BoundaryStatsAPIView`, `AvailableCountriesAPIView` are unchanged.

## Frontend

### New module: `static/microplans/admin_boundaries_layer.js`

Mirrors `service_delivery_layer.js` (the worked panel-layer example). Built on
`window.Microplans` helpers: `apiGet({signal})`, `debounce`, `upsertSource`,
`removeSourceAndLayers`, `esc`, `fitTo`.

- **Registration:**
  `MicroplansMapPanel.registerLayer({ id:'admin', label:'Boundaries', color:'#a855f7', onToggle, body })`.
- **Toggle on:** fetch viewport endpoint for current `map.getBounds()` + `getZoom()`;
  add one GeoJSON source + an outline **line-layer per admin level** (finer level =
  thinner line, all purple). Re-fetch on `moveend`, debounced via
  `Microplans.debounce`, cancelling the in-flight request via an `AbortController`
  passed to `apiGet`. **Toggle off:** `removeSourceAndLayers` to free map resources.
- **Smallest-wins inspect:** on hover/click,
  `map.queryRenderedFeatures(point, {layers:[...admin level layer ids]})` → pick the
  feature with the **highest `admin_level`** → build HTML → `panel.setInspect(html)`.
  Hover = transient preview; mouse-out reverts to the pinned selection; click = pin
  (mirrors the `inspectWA(id, pin)` / `pinnedInspectHTML` pattern in `review.html`).
  Inspect HTML shows name, admin level, parent chain, country, area, population, and a
  **"select parent: <name>"** action that escalates the resolved boundary to its parent.
- **No floating popup** — all info via `setInspect`.
- **Panel body:** name-search box (debounced; queries the same viewport/areas data) that
  lists matching boundaries → clicking one selects it (same path as map Shift/⌘-click) +
  a live "**N wards · X km²**" selection summary + an `[area phase only]` hint line.

### Area selection (area-definition phase only)

- **Gate:** the existing `areaInput` state in `review.html` (`"draw" | "admin" | "pin"`).
  Boundary area-select is active only while in the area-definition phase (new plan, or an
  edit plan with the "Edit area" section open / `areaInput === "admin"`).
- **Gesture:** Shift/⌘-click the resolved (smallest) boundary on the map, **or** click a
  search result → toggle it in/out of a JS-tracked selected set → fetch its
  **full-resolution** geometry → `draw.add(...)` (feeds the existing
  apply/preview/regenerate path) → `refreshAreaStats()` and update the layer's selection
  summary. Selected boundaries fill in (a fill layer keyed on selected `boundary_id`s).
- **Replaces** the `#area-admin` block (`review.html` lines ~135–151) and its
  `loadCountries` / `searchAreas` / `useArea` JS (lines ~1225–1283). The **"Admin"
  segment button** (`btn-area-admin`) is **relabeled "Boundaries"** and repurposed:
  selecting it turns the Boundaries layer **on** and reveals the name-search +
  "Shift-click the map" helper (keeps a discoverable rail entry point). Outside the area
  phase, click only inspects — it never mutates the area.

## Validation

- **Unit tests:** the viewport query + zoom→tolerance simplification + feature-cap/
  truncation logic. Requires `GDAL_LIBRARY_PATH` / `GEOS_LIBRARY_PATH` env (Homebrew
  paths on macOS) to run pytest.
- **Live (WebGL-patched `gstack browse`, `/canopy:patch-gstack-browse`):** on a real plan
  — toggle shows outlines; hover/click inspects the **smallest** boundary; Shift/⌘-click
  in the area phase builds the plan area (replacing the old dropdown); confirm across
  **≥2 countries** with different hierarchy depths; verify no floating popup.
- **DDD:** add an admin-boundary scene to
  `docs/walkthroughs/microplans-service-delivery.yaml` for the DDD pass.

## Out of scope

- Building-footprints layer (separate).
- Overture per-area fetch (not part of this bbox-listable layer).
- Changes to the work-area inspector or service-delivery layer beyond sharing the panel.
