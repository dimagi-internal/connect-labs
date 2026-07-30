# Connect Work-Area CSV Import Contract

What the labs "Download Connect import CSV" must produce so the file is accepted
by **Connect's** work-area importer with no hand-editing.

This is the authoritative cross-reference for `core/workarea.py` (`CSV_HEADERS`,
`to_csv_rows`) and `views.ProgramPlanCSVView`. The source of truth on the Connect
side is **`dimagi/commcare-connect`**:

- `commcare_connect/microplanning/tasks.py` → `WorkAreaCSVImporter`, `ImplementationAreaCSVImporter`, `WorkAreaCSVExporter`
- `commcare_connect/microplanning/views.py` → `WorkAreaImport` (the upload view)
- `commcare_connect/microplanning/models.py` → `WorkArea`, `WorkAreaGroup`, `ImplementationArea` (SRID = 4326)

Verified live on 2026-05-31 by importing a labs export into a real opportunity
(`ai-demo-space` / `d99e4422-e11a-476e-b885-fdf92026ea54`). See "Live verification"
below — the first attempt was **rejected** for blank LGA/State; the corrected
export imported cleanly ("Successfully created 5 work area(s)").

**2026-07-30 update:** read `tasks.py` + `models.py` directly off GitHub
(`dimagi/commcare-connect`, commit `b8f92039` on `main`, merged 2026-07-27 —
branch `pkv/ccct-2585-ward-layer-models-import`, which lines up with product's
updated import template). `Implementation Area` and `Work Area Group Name` are
now real, importer-recognized columns — confirmed against source below (not
yet re-verified with a live upload; see the caveats in each section).

---

## ⚠ Nigeria-hardcoded vocabulary (tech debt)

The column names **`Ward`**, **`LGA`**, and **`State`** are **Nigeria's**
administrative tiers (ADM3 / ADM2 / ADM1). They are hardcoded on **Connect's**
side (`WorkAreaCSVImporter.HEADERS`), and labs mirrors them verbatim
(`core/workarea.py:CSV_HEADERS`, `PlanRecord.lga/.state`,
`plan.derive_lga_state`) **only** so the export matches.

Labs itself is **already country-generic**: the admin-boundary resolver speaks
canonical levels (1 = region/state, 2 = county/district/LGA, 3 = locality/ward;
`core/admin_boundaries.py`). So a Kenyan **County** or an Indian **District** is
the same canonical level-2 area we currently push into the **`LGA`** column. The
import still succeeds (Connect only checks the values are non-empty, not that
they're really LGAs), but **the column names lie** for any non-Nigeria program.

**TODO — generalize once Connect generalizes.** When Connect's work-area
importer moves to canonical admin levels (or per-country vocabulary), update:
`core/workarea.py:CSV_HEADERS`, `core/plan.derive_lga_state`,
`core/models.PlanRecord.lga/.state`, `views.ProgramCreatePlanView` /
`ProgramPlanCSVView`, and the `review.html` "State" field — to drop the
Nigeria-specific names. Track the Connect-side change as the trigger.

---

## Upload mechanics

- **Endpoint:** `POST /a/<org>/microplanning/<opp_id>/upload_work_areas/`
  (`WorkAreaImport`), multipart field **`csv_file`**, extension must be `.csv`.
  It is `@org_admin_required` (session/web auth) — **not reachable with the labs
  OAuth `export` scope**. There is no JSON/API write endpoint for work areas.
- **Async:** the view saves the file and enqueues `import_work_areas_task` (Celery);
  the page polls `import_status/`. Result is `{"created": N}` or `{"errors": {...}}`.
- **Opportunity must be EMPTY.** `import_work_areas_task` aborts with
  _"Work Areas already exist for this opportunity"_ if **any** `WorkArea` already
  exists for the opp. Import is **all-or-nothing for a fresh opp** — no append,
  no re-import, no upsert. (This is why a failed-validation upload is safe: it
  creates 0 rows, so you can fix and retry.)
- **Atomic validation.** `WorkAreaCSVImporter.run()` does **two passes**: it
  validates _every_ row first and, if **any** row has an error, creates **nothing**.
  Only a fully-clean file inserts.

---

## Required columns (header row — exact labels, 9 required)

`_validate_headers` fails the whole file if **any** of `WorkAreaCSVImporter.HEADERS`
are missing (`missing = set(HEADERS.values()) - headers`). This set is still
**exactly 9** — unchanged by the 2026-07 update. Unrecognized extra columns are
ignored by header validation, but two specific extras below are NOT just
ignored — the importer actively reads them if present.

| #   | Header label           | labs `WorkAreaPayload` field  | Notes                                                   |
| --- | ---------------------- | ----------------------------- | ------------------------------------------------------- |
| 1   | `Area Slug`            | `slug`                        | **required, unique** (see below)                        |
| 2   | `Ward`                 | `ward`                        | **required, non-empty**; stored in a Django `SlugField` |
| 3   | `Centroid`             | `centroid_lon`/`centroid_lat` | `"<lon> <lat>"` space-separated → `POINT(lon lat)`      |
| 4   | `Boundary`             | `boundary_wkt`                | WKT; **must be a `Polygon`**                            |
| 5   | `Building Count`       | `building_count`              | integer ≥ 0 (blank → 0)                                 |
| 6   | `Expected Visit Count` | `expected_visit_count`        | integer ≥ 0 (blank → 0)                                 |
| 7   | `Target Population`    | `target_population`           | integer ≥ 0 (blank → 0)                                 |
| 8   | `LGA`                  | `case_properties["lga"]`      | **required, non-empty** ⚠                               |
| 9   | `State`                | `case_properties["state"]`    | **required, non-empty** ⚠                               |

The labs header set (`core/workarea.py:CSV_HEADERS`) matches these labels
**field-for-field and in order**. The export's structure has always been correct;
the historical failure mode is **values**, not columns (see the gap below).

### `Implementation Area` — OPTIONAL, name-matched against a separate model

`WorkAreaCSVImporter.OPTIONAL_HEADERS = {"implementation_area": "Implementation Area"}`
— NOT in `HEADERS`, so the column may be **omitted entirely** with no error.
When present, the raw string is **not free text on the WorkArea alone** — it's
looked up by exact name against `ImplementationArea` rows _already existing for
that opportunity_ (`ImplementationArea.objects.filter(opportunity_id=...).values_list("name","id")`):

- **Match found:** `WorkArea.implementation_area` (FK) is set to that
  `ImplementationArea`.
- **No match** (including "no ImplementationArea has been created yet" — labs
  never creates them today): `implementation_area` FK stays `null`, but the raw
  string is still stored verbatim on `WorkArea.implementation_area_name`
  (`CharField`, blank-ok). **No error either way** — this column can never fail
  validation.
- **Auto-relink:** if `ImplementationArea` rows matching that name are imported
  _afterward_ (via the separate `ImplementationAreaCSVImporter` /
  `import_implementation_areas_task`, headers `Implementation Area Name`,
  `Centroid`, `Boundary` — a different upload entirely, its own endpoint), its
  `_after_insert` back-fills `implementation_area` on any `WorkArea` whose
  `implementation_area_name` matches and whose FK is still null.

**Implication for labs:** setting `Implementation Area = ward` (current
behavior, per product 2026-07-30) is safe and will always import without
error, but it only becomes a _real linked_ Implementation Area in Connect if a
same-named `ImplementationArea` (with its own centroid + boundary) already
exists, or is uploaded later, for that opportunity. Labs does not currently
export/create `ImplementationArea` rows — if product wants the link to
actually resolve, that would need a separate feature (generate + upload an
Implementation Area file per plan/ward). Until then this column is decorative
text on import.

### `Work Area Group Name` — OPTIONAL, but all-or-nothing across the file

`GROUP_NAME_HEADER = "Work Area Group Name"` — explicitly commented in
Connect's source as "optional column, not part of `HEADERS`... a sheet is
allowed to omit it entirely." But if it's present at all, **every row must have
a non-empty value, or every row must be blank** — a partial mix is a **hard
file-level rejection**:

> `_after_rows`: `has_group_names = total_rows > 0 and rows_with_group ==
total_rows`; if `0 < rows_with_group < total_rows` →
> _"Work Area Group Name is required for all Work Areas, or must be omitted for
> all."_ — **this fails the ENTIRE file**, not just the offending rows.

When all rows have it: for each distinct group name, Connect looks up (or
creates, `bulk_create`) a `WorkAreaGroup(opportunity, name, ward=<ward of the
first row seen with that name>)`, then sets `WorkArea.work_area_group` (FK) to
it. Group centroids are recomputed after insert.

**Labs-side check (done 2026-07-30):** every code path that builds a work area
(`core/plan.py` — coverage-mode creation, sampling-mode creation, and
`core/grouping.group_work_areas`, including its `"group-no-geometry"` fallback)
always stamps a non-empty `work_area_group` string — confirmed by grep, no
path leaves it blank for a subset of a plan's active work areas. So the
all-or-nothing rule should always be satisfied by labs' export in practice, but
this has not been exercised end-to-end against a real upload.

---

## Per-row validation rules (`_process_row`)

A row is rejected if **any** of these fail. Errors are grouped by message with the
offending 1-based line numbers (header is line 1; data starts at line 2).

1. **`Area Slug`** (`_validate_slug`)
   - Required (non-empty after `strip_tags`). → _"Area slug is required and it should be unique."_
   - Unique **within the file**. → _"Duplicate Area slug in file"_
   - Not already present for the opp. → _"Area slug already exists for this opportunity"_
   - Stored in a `SlugField` + DB constraint `unique_slug_per_opportunity`.
2. **`Ward`** (`_validate_ward`) — required, non-empty after strip. → _"Ward is required."_
   The model field is a `SlugField(max_length=255)`, so keep ward slug-safe
   (lowercase, digits, `-`/`_`; avoid spaces/punctuation). labs sets
   `ward = str(work_area_group or "")` — an **empty group fails this rule**.
3. **`Centroid`** (`_validate_centroid`) — must parse as two space-separated floats
   `"lon lat"` → `POINT(lon lat)`, SRID 4326. → _"Centroid must be in 'lon lat' format"_
4. **`Boundary`** (`_validate_boundary`) — valid WKT **and** `geom_type == "Polygon"`,
   SRID 4326. → _"Invalid WKT format for Boundary(Polygon)."_
5. **Numbers** (`_validate_numbers`) — `Building Count`, `Expected Visit Count`,
   `Target Population` each parse to an integer **≥ 0**. Blank → 0 (allowed). The
   error text says "positive" but the check is `>= 0`, so **0 is valid** (labs
   exports `Target Population = 0` and that passes). Non-integer → reject.
   → _"Building count, Expected visit count, and Target population must be positive integers"_
6. **Extra properties** (`_validate_extra_properties`) — **both `LGA` and `State`
   must be truthy (non-empty)**. → _"Missing values for properties: lga, state"_
   These land in `WorkArea.case_properties = {"lga": ..., "state": ...}`.

Coordinate system is **SRID 4326 (WGS84)** for both centroid and boundary; lon
precedes lat everywhere.

---

## ⚠ The LGA / State gap (labs export → Connect import)

**Connect requires `LGA` and `State` to be non-empty on every row** (rule 6). The
labs export leaves them blank unless the caller supplies them:

- `views.ProgramPlanCSVView.post` reads them only from the request body:
  `to_workarea_payloads(plan.work_areas, lga=payload.get("lga", ""), state=payload.get("state", ""))`.
- The **"Download Connect import CSV"** button (`templates/microplans/review.html`,
  `#btn-export`) POSTs an **empty body `{}`** → `lga=""`, `state=""`.
- `to_workarea_payloads` writes those into `case_properties`, and `to_csv_rows`
  emits empty `LGA`/`State` cells.
- Connect rejects **every** row: _"Missing values for properties: lga, state"_.

**A naive download-then-upload therefore fails on the first try.** It only
succeeds if `lga` + `state` are passed to the export endpoint
(`POST .../work_areas.csv` with `{"lga": "...", "state": "..."}`).

### Where the values should come from (labs-side fix — no Connect change)

The plan record stores **`region`** (e.g. `"Kano North LGA"`) but **no `state`**
and no structured admin hierarchy (`data_access.create_plan` persists only
`region`). So:

- **`LGA`** is directly available — default it to `plan.region`.
- **`State`** is **not** persisted today. It must be either:
  - **(a)** derived from the admin-boundary resolver — an LGA is canonical level 2;
    its parent (level 1 = region/state) is resolvable via `core/admin_boundaries.py`
    — and persisted on the plan at creation; or
  - **(b)** captured at plan-creation time (the admin-area picker already knows the
    selected area's parent chain) and stored on the plan; or
  - **(c)** prompted for on the "Download Connect import CSV" click and passed in
    the POST body.

Recommended: **persist `lga` + `state` on the plan at creation** (from the
admin-area pick / resolver parent chain), then have `ProgramPlanCSVView` default
`lga`/`state` from the plan so the download button needs no params and the file
imports cleanly every time. Keep the body params as an override.

Until that ships, the working manual recipe is:

```
POST /microplans/program/<program_id>/plan/<plan_id>/work_areas.csv
body: {"lga": "<LGA name>", "state": "<State name>"}
```

---

## No work-area write API

There is no work-area **write API** on the labs-reachable `export` scope
(`WorkAreaDataView` is read-only). The CSV web import is the only path in —
see the `Implementation Area` / `Work Area Group Name` sections above for how
those two columns are actually consumed by the importer (confirmed against
`dimagi/commcare-connect` source 2026-07-30).

---

## Live verification (2026-05-31)

Plan 3321 ("Kano North — coverage v2", 6 areas − 1 excluded = 5 rows) exported
from labs and uploaded into opp `d99e4422` (`ai-demo-space`):

1. **First upload (blank LGA/State):** rejected — _"Missing values for properties:
   lga, state"_ on rows 2–6. 0 work areas created.
2. **Corrected export** (`{"lga": "Kano North LGA", "state": "Kano"}`): accepted —
   _"Successfully created 5 work area(s)"_; the opp microplanning map populated
   with the 5 cells (`workareas_group_geojson` bounds
   `[8.5021, 11.999, 8.546, 12.021]`).

Everything else in the labs export (column set/order, centroid `lon lat`, WKT
polygons, integer counts, `Target Population = 0`) was accepted as-is.
