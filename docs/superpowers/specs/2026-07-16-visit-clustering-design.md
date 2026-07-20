# Visit Clustering — Weekly Dual-Track Image Audit

**Date:** 2026-07-16
**Status:** Approved (per-section), pending spec review

## Purpose

The Weekly Dual-Track Image Audit workflow (`connect_labs/workflow/templates/weekly_dual_track_audit.py`) currently has two filters that determine which images get audited, per FLW per track (MUAC / Other):

1. **Audit window** — date range.
2. **% Sampling rates** — share of matching images to audit.

This adds an optional third filter, **Visit Clustering**, which does *not* change which images get audited. Instead, when enabled, it produces a side-channel artifact: groupings of consecutive visits (by the same FLW, within the same track) that are close together in time and/or GPS location — candidates for a future duplicate-detection classifier (not built yet). Each grouping can be downloaded as a CSV containing everything that classifier will eventually need.

**Hard constraint:** this must not alter the existing visit-selection/sampling behavior in any way. The set of visits and images that end up in a track's audit session — and everything `labs_audit_breakdown.js` already shows about it (images, pass/fail/pending, AI stats, the full review link) — is identical whether or not Visit Clustering is enabled. Clustering only adds data alongside it.

## Config schema

New key in `DEFINITION["config"]["audit_batch"]`, alongside the existing `track_a`/`track_b`:

```python
"visit_clustering": {
    "enable_time_gap": False,
    "time_gap_minutes": 10,
    "enable_distance": False,
    "distance_meters": 10,
}
```

Per-run overridable in the render, same pattern as `muacSample`/`otherSample`: React state defaults from this pinned config, editable before creation, sent in the `startJob` payload (`enable_time_gap`, `time_gap_minutes`, `enable_distance`, `distance_meters`), persisted onto run state in `weekly_dual_track_audit_create`'s `wda.update_run_state(...)` call alongside `pass_threshold`/`deliver_unit_types`/`visit_statuses`.

## UI: "Visit Clustering" config card

New card in `RENDER_CODE`, between "Sampling rates" and "Opportunities & pinned image types," shown only pre-creation (`!viewOnly`):

- ☐ Group visits within `[10]` minutes of each other (by visit date)
- ☐ Group visits within `[10]` meters of each other (by GPS location)
- Explainer: "Optional — groups consecutive visits by the same field worker that are close in time and/or location, for duplicate-detection review. Leave both unchecked to skip this entirely."
- Each number input is only meaningful when its checkbox is checked. If a box is checked, its number must be a positive number or the "Create audits" button is disabled (same guard style as the existing `!startDate || !endDate` check).

## Clustering algorithm

Runs inside `run_audit_creation` (`connect_labs/audit/tasks.py`), in the per-FLW session-creation loop (around line 732, where `flw_visit_list` — the final resolved, already-sampled visit IDs for that FLW+track — is available), **after** `flw_images`/the session's visit set is finalized. Only runs when at least one checkbox is enabled; otherwise skipped entirely (zero cost, zero output, matching "nothing changes").

**Data needed per visit:** `visit_date` and `location` (the standard `"lat lon alt precision"` string already on every visit dict, in `ALL_VISIT_KEYS`). Neither is currently threaded into `all_visit_images` (which only carries `username`/`entity_name`/`entity_id`/`visit_date` per image, not `location`). Fetch these via one lightweight bulk lookup before the per-FLW loop — the same established pattern already used twice elsewhere in this codebase for exactly this kind of gap (the `entity_id` backfill and the `user_id`/`user_visit_id` shareable-link backfill, both in `ExperimentBulkAssessmentDataView.get`):

```python
link_visits = data_access.pipeline.fetch_raw_visits(
    opportunity_id=opp_id, skip_form_json=True, filter_visit_ids=set(visit_ids),
)
visit_meta_by_id = {str(v["id"]): v for v in link_visits}  # visit_date, location, user_id, user_visit_id
```

One fetch covers all FLWs for this track (cheap — same visits were just fetched moments ago for image extraction, so it's a SQL-cache hit), then sliced per FLW inside the loop.

**Per FLW, per track:**

1. Sort that FLW's final `flw_visit_list` by `visit_date`. If `visit_date` is missing for a visit, sort it last and treat every pair touching it as non-qualifying (never clusters — same fail-safe stance as missing `location`).
2. Walk consecutive pairs. Two consecutive visits join the same group when **every enabled** checkbox's condition holds (AND semantics — confirmed: both time and distance must hold when both are enabled):
   - Time: both visits have a `visit_date` and `abs(visit[i+1].visit_date - visit[i].visit_date) <= time_gap_minutes`
   - Distance: parse `location` as `"lat lon alt precision"` (same format Connect production's own nearby-visits feature in `user_visit_details` already parses) and compute meters via `geopy.distance.distance` (already a labs dependency, `geopy==2.4.1` in `requirements/base.txt` — confirmed installed). If either visit's `location` is missing/unparseable while the distance checkbox is enabled, that pair does **not** satisfy distance (fail-safe: don't cluster on uncertain data).
3. Chain transitively — if A pairs with B and B pairs with C, all three land in one group (matches "consecutive": a run of close-together visits).
4. **Drop singletons** — a visit with no qualifying neighbor produces no grouping entry. Only clusters of 2+ visits are stored.

**Stored on the session**, `AuditSessionRecord.data["visit_clusters"]`, written once at creation (via `create_audit_session`, alongside `visit_images`/`related_fields` etc. — not recomputed later). `group_id` is a simple sequential id scoped to the session (`"g1"`, `"g2"`, …, 1-indexed in the order groups are formed while walking the sorted visit list):

```python
[
  {"group_id": "g1", "visit_ids": [111, 112], "image_count": 4},
  {"group_id": "g2", "visit_ids": [130, 131, 132], "image_count": 7},
]
```

`image_count` = total images across those visit_ids for this track, from the same `visit_images` already being stored on the session (`sum(len(flw_images.get(str(vid), [])) for vid in group_visit_ids)`).

## Shared UI: `connect_labs/static/js/labs_audit_breakdown.js`

This file backs all 3 surfaces (opp run page, program-creator expandable rows, pages card) — changes here are intentionally global.

**Structural change (needed regardless of clustering):** `auditLine`'s current markup is one big `<a href=bulkUrl(...)>` — the entire row navigates to the bulk-review page on any click. To let a "Duplicate Groupings" button live inside the row without triggering navigation, restructure the wrapper to a `<div>`, moving the "open in Connect" behavior to a small explicit icon-button at the end of the row (same arrow icon already there today) instead of making the whole row clickable. All existing row content (status, images, pass/fail/duplicates/pending, AI stats) is unchanged.

**New element:** when `s.visit_clusters` is non-empty, render a button: **"N Duplicate Groupings"** (N = `s.visit_clusters.length`) next to the existing pass/fail/duplicates/pending line. Clicking toggles a local `expanded` boolean (component-local state, no new fetch — the data's already in `s.visit_clusters`). Expanded panel lists each group: `"Group 1 — 4 images"` with a download link.

## Sessions API: expose `visit_clusters`

`WorkflowSessionsAPIView.get` (`connect_labs/audit/views.py`) already returns per-session `assessment_stats`, `image_count`, etc. Add one field to that same response dict:

```python
"visit_clusters": session.data.get("visit_clusters", []),
```

No other fields change; this is purely additive to the existing response shape.

## CSV export endpoint

New view, `connect_labs/audit/views.py`, mirroring the existing `ExperimentBulkAssessmentExportCSVView` pattern (same session-lookup, same `get_visits_batch`-based link resolution built earlier for shareable Connect visit links):

- `GET /audit/api/<session_id>/visit-clusters/<group_id>/export.csv`
- Look up the group's `visit_ids` from `session.data["visit_clusters"]` (404 if session or group not found).
- Expand to that group's images via the session's existing `visit_images` data (already stored — no new fetch for image-level fields).
- For each image, resolve the Connect visit URL exactly as already built for the per-image "#" link (`user_id`/`user_visit_id` UUIDs → `{connect_url}/a/{org_slug}/opportunity/{opp_id}/user_visits/?user=...&visit_id=...`) — reuses the shareable-visit-link work already shipped this session, no new lookup logic.
- CSV columns: **Visit ID, Filename, Visit Date, GPS Location (raw `location` string), Connect Visit URL**. (Beneficiary name intentionally omitted from the export; it's still shown in the in-page expand list.)

## Explicitly out of scope

- The actual duplicate-detection classifier that will eventually consume these CSVs — not being built now ("isn't wired up yet," per the request).
- Any change to which visits/images are selected, sampled, or shown for review — confirmed unchanged in every scenario.
- Recomputing clusters after creation (e.g., if criteria change) — clusters are frozen at creation time, same as the rest of a completed session's data.
