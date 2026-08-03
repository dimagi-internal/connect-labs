# Dual-Track Image Audit: per-path AI classifiers + Duplicate Detection

Status: approved, implementation in progress (2026-07-30). Ships to `main` for a
Monday 2026-08-03 PR — see "External dependency timing" below.

**2026-08-03 update — superseded module names.** PR #1070 ("Add duplication
check to audits") merged to `main` the same day this spec was written,
implementing an independent, differently-designed Duplicate Detection feature
(day/FLW/photo-type-bucketed, for `bulk_image_audit.py`/`muac_picture_audit.py`)
under the exact module path this spec assumed for the dual-track feature
(`connect_labs/audit/duplicate_detection.py`). The dual-track implementation
below was consolidated onto #1070's code rather than duplicating it: the
module described here now lives at
`connect_labs/audit/visit_cluster_duplicate_detection.py`, its API client is
`connect_labs.audit.duplicate_detection.DuplicateDetectionClient` (method
`.detect()`, not `.detect_duplicates()`), and the standalone
`connect_labs/labs/integrations/duplicate_detection/api_client.py` module
this spec describes was deleted (never shipped). The design/behavior sections
below are otherwise accurate; only the file/module names in the "Backend"
subsections are stale.

## Problem

`weekly_dual_track_audit.py` (the "Weekly Dual-Track Image Audit" workflow
template) hardcodes its AI review wiring: any selected image path whose name
contains `"muac"` automatically gets both the MUAC OverZoom and MUAC Match
reviewers attached (`_reviewers_for_path`, `weekly_dual_track_audit.py:39-49`).
There's no UI control for this and no way to opt out per path, and no support
for other classifiers (e.g. the KMC scale-comparison agent already used by
`audit_with_ai_review.py`).

Separately, Visit Clustering (`docs/superpowers/specs/2026-07-16-visit-clustering-design.md`)
computes groupings of visits close in time/location, purely for display — the
groupings were explicitly scoped as future input to "a duplicate-detection
classifier (not built yet)". That classifier is now specified
(see the Google Doc linked in the originating conversation) and ready to wire in.

## Scope

1. Turn the 3 image-only classifiers (Hyperzoom, MUAC Mismatch, KMC Scale
   Comparison) into per-path checkboxes in the "Opportunities & image types"
   tile, each greyed out unless applicable to that path.
2. Add a new Duplicate Detection classifier, wired through Visit Clustering
   instead of the per-path tile: a master checkbox that, when on, sends every
   already-computed grouping to the external Duplicate Detection API and
   writes back confirmed duplicates as an AI flag + auto-tag.

## 1. Per-path classifier checkboxes

### Config shape

`DEFINITION.config.audit_batch.per_opp[<opp_id>]` gains a new `classifiers` key:

```python
"per_opp": {
    "<opp_id>": {
        "muac_image_paths": [...],
        "rest_image_paths": [...],
        "classifiers": {"<path>": ["hyperzoom", "muac_mismatch", "kmc_scale"]},  # NEW, sparse
    }
}
```

Only paths with at least one classifier checked need an entry. Applies
regardless of which track (A/B) the path is pinned under — same as today's
substring rule, which also ignores track.

### Classifier keys, gating rules, and reviewer specs

| Key | Applies when | Reviewer spec |
|---|---|---|
| `hyperzoom` | `"muac" in path.lower()` | `MUAC_OVERZOOM_REVIEWER` (existing) |
| `muac_mismatch` | `"muac" in path.lower()` | `MUAC_MATCH_REVIEWER` (existing) |
| `kmc_scale` | `path == "anthropometric/upload_weight_image"` (exact, case-sensitive — verbatim from `audit_with_ai_review.py`) | `{"agent_id": "scale_validation", "config": {"comparison_field": "child_weight_visit"}, "auto_apply_actions": ["fail_unmatched"]}` |

Gating is enforced **server-side** (in `_reviewers_for_path`), not just in the
UI — a stale/malformed saved config can never attach a reviewer to a path it
doesn't apply to, regardless of what the frontend sent.

### Backend changes (`connect_labs/workflow/templates/weekly_dual_track_audit.py`)

- Add `KMC_WEIGHT_READING_FIELD = "child_weight_visit"` and
  `KMC_SCALE_REVIEWER` next to the existing `MUAC_*_REVIEWER` constants.
- Add `CLASSIFIER_SPECS = {"hyperzoom": MUAC_OVERZOOM_REVIEWER, "muac_mismatch": MUAC_MATCH_REVIEWER, "kmc_scale": KMC_SCALE_REVIEWER}` and `CLASSIFIER_KEYS = frozenset(CLASSIFIER_SPECS)`.
- Replace `_reviewers_for_path(path)` with `_reviewers_for_path(path, classifiers)`:
  iterates the path's saved classifier keys, applies the gating rule per key,
  and returns the matching reviewer specs (silently drops keys that don't
  apply or aren't recognized).
- `_image_audits(paths, classifiers)` passes `classifiers.get(path, [])` through
  to `_reviewers_for_path` for each path.
- `build_track_audit_calls` reads `cfg.get("classifiers", {})` per opp and
  passes it to `_image_audits` for both Track A and Track B path lists.
- Extend the `per_opp` comment in `DEFINITION["config"]["audit_batch"]` to
  document the new `classifiers` key.

### `UpdateAuditBatchConfigView` (`connect_labs/workflow/views.py`)

- Validate `cfg["classifiers"]` (if present) is a `dict[str, list[str]]`, and
  that every value is a subset of `CLASSIFIER_KEYS` (imported from the
  template module) — reject with 400 otherwise, matching the existing
  `muac_image_paths`/`rest_image_paths` list-type validation.
- No other change — `per_opp[key]` is already a full-replace write, so the
  frontend just needs to include `classifiers` in the object it POSTs per opp.

### Render code changes

- Client-side classifier defs mirror the gating table above (`appliesTo(path)`
  predicates for `hyperzoom`/`muac_mismatch`/`kmc_scale`).
- New state `classifiersByOpp: {[oppKey]: {[path]: string[]}}`, initialized
  from `perOpp[key].classifiers` where present.
- **Default-on preserved for backward compatibility**: for a path with no
  explicit saved entry yet, its *effective* classifier list defaults to
  whichever of `hyperzoom`/`muac_mismatch` apply to it (i.e., pre-checked on
  muac paths, exactly today's automatic behavior) — computed via a
  `defaultClassifiersForPath(path)` helper, `kmc_scale` never defaults on.
  Once a path's checkboxes are touched by the user, its list becomes explicit
  (including "checked nothing"), and stays explicit across saves.
- For each path currently selected in Track A or Track B (for that opp), render
  a row below the path checklists: the path name + 3 checkboxes (Hyperzoom,
  MUAC Mismatch, KMC Scale Comparison). A checkbox is `disabled` (and visually
  greyed) when `!classifier.appliesTo(path)`; disabled checkboxes are always
  unchecked and non-interactive.
- `handleSaveConfig` computes, for every currently-selected path per opp, its
  effective classifier list (explicit-if-touched, else the default), and
  includes `classifiers: {...}` in that opp's POST payload alongside the
  existing `muac_image_paths`/`rest_image_paths`.
- Update the explanatory copy above the tile (currently: "Any selected path
  containing 'muac' is automatically reviewed...") to describe the checkboxes
  instead.

## 2. Duplicate Detection (via Visit Clustering)

### External API (confirmed via the design doc + `dimagi/commcare-connect#1415`)

- `POST https://image-pipeline-scale-gw-4pc8jsfa.uc.gateway.dev/detect_duplicates`
  — same gateway, same `x-api-key` auth, same `SCALE_VALIDATION_API_KEY` /
  `SCALE_VALIDATION_API_URL` settings as `scale_validation`/`muac_overzoom`.
  - Request: `{"images": [{"id": "<blob_id>", "url": "<world-readable url>"}, ...]}`
  - Response: `{"groups": [[id, id, ...], ...]}` — ids not in any group had no
    detected duplication; an id can appear in more than one group.
- `GET https://connect.dimagi.com/export/opportunity/<opp_id>/attachment_signed_url/?blob_id=<blob_id>`
  → `{"attachment_signed_url": "<url>"}`. Same auth/opportunity-scoping as the
  existing `/export/opportunity/<id>/image/` byte-download endpoint. 10-minute
  expiry, S3-backed only.

**External dependency timing:** PR #1415 (the `attachment_signed_url`
endpoint) is not yet merged/deployed to `connect.dimagi.com` — expected
Monday 2026-08-03. All code here is built against its confirmed, tested
contract (unit-tested in the PR itself against a mocked signed-URL call), but
end-to-end verification against the live gateway has to wait for that deploy.
Local tests mock both the signed-URL call and the `detect_duplicates` call.

### Config + trigger

Mirrors the existing `enable_time_gap`/`enable_distance` pattern exactly —
this is a **per-run** toggle (not saved via "Save configuration"), defaulting
from the pinned `DEFINITION.config.audit_batch.visit_clustering.enable_duplicate_detection`
(new key, default `False`), overridable per run, and persisted onto run state
by the job handler after each batch (`wda.update_run_state`).

**No per-path/classifier filtering.** Per the approved design: Duplicate
Detection applies to whatever images are already selected across both
tracks, and the *only* filter is whichever groupings the existing
time-gap/distance parameters in the same Visit Clustering tile already
produced. If a grouping has fewer than 2 images (rare — a 2-visit grouping
where one visit contributed 0 qualifying images), it's skipped; otherwise the
grouping's full image set is sent as one API call.

### Backend (new module `connect_labs/audit/duplicate_detection.py`)

`run_duplicate_detection(targets, *, get_signed_url, client=None, progress_callback=None, cancel_key=None) -> dict`

- `targets`: one entry per FLW session that had clusters computed:
  `{"session": AuditSessionRecord, "data_access": AuditDataAccess, "opp_id": int, "clusters": [...], "blob_meta_by_id": {blob_id: {"visit_id": int, "question_id": str}}}`.
- For each target, for each of its clusters with `len(image_ids) >= 2`:
  resolve every blob_id to a signed URL via `get_signed_url(blob_id, opp_id)`
  (skip blobs that fail), call `client.detect_duplicates(images)` once per
  grouping (matches the chosen "one call per grouping" batching), and for
  every blob_id in any returned group, merge a duplicate verdict into that
  blob's assessment via a **read-modify-write** helper (`set_assessment`
  can't be reused directly — it overwrites the whole entry, and by this point
  the standard AI reviewers may have already written `ai_result`/`ai_notes`
  for the same blob):
  - Append `"Duplicate Detected"` to `ai_notes` (joined with the existing
    `AI_NOTES_JOIN_SEP = "; "`, so it shows up in the existing "AI: N ... /
    M reviewed" summary and `ai_flags_by_label` — zero changes needed to
    `get_assessment_stats()` or `labs_audit_breakdown.js`).
  - Set `ai_result = "no_match"` unless it's already `"error"`.
  - Set `result = "duplicate_fake"` **only if the assessment is currently
    untouched** (no existing human `result`) — never overwrite a manual
    verdict. This is the "real" version of the disabled
    `tagDuplicateGroupingAssessments` JS in `bulk_assessment.html` (which
    blindly tagged every image in a time/distance grouping and was disabled
    for being too blunt) — here, only images the ML API actually confirmed
    get auto-tagged.
- API/network failures for one grouping are logged and skipped (`errors`
  counter) — never fail the whole audit creation, same posture as the
  existing AI reviewers.
- Returns `{"groupings_checked", "groupings_skipped", "images_flagged", "errors", "cancelled"}`.

### New API client (`connect_labs/labs/integrations/duplicate_detection/api_client.py`)

`DuplicateDetectionClient` — same shape as `ScaleValidationClient`
(`connect_labs/labs/integrations/scale_validation/api_client.py`): lazy
`httpx.Client`, `x-api-key` header from `SCALE_VALIDATION_API_KEY`, base URL
from `SCALE_VALIDATION_API_URL`. One method: `detect_duplicates(images: list[dict]) -> dict`.

### `AuditDataAccess.get_attachment_signed_url` (`connect_labs/audit/data_access.py`)

New method next to `download_image_from_connect`: `GET
{production_url}/export/opportunity/{opportunity_id}/attachment_signed_url/?blob_id=...`,
same `self.http_client` (already authenticated), returns
`response.json().get("attachment_signed_url")` or `None` on any HTTP error
(logged, not raised) — callers skip that blob rather than fail the batch.

### Wiring into `connect_labs/audit/tasks.py::run_audit_creation`

- Read `enable_duplicate_detection = bool(criteria.get("enable_duplicate_detection"))`
  right after `audit_criteria = AuditCriteria.from_dict(criteria)` (same place
  `enable_time_gap`/`enable_distance` are conceptually parsed); add to
  `total_stages` the same way `has_ai_agent` does.
- In the per-FLW loop, immediately after `create_audit_session(...)`, if
  `enable_duplicate_detection and flw_clusters`: build `blob_meta_by_id` from
  the already-in-scope `flw_images` dict (`{blob_id: {"visit_id", "question_id"}}`)
  and append a target dict (defined above) to a new `dup_detection_targets`
  list (initialized before the per-FLW loop).
- After the existing Stage 4 (AI review) block, add a new optional stage that
  calls `run_duplicate_detection(dup_detection_targets, get_signed_url=lambda blob_id, oid: _data_access_for_opp(oid).get_attachment_signed_url(blob_id, oid), progress_callback=..., cancel_key=cancel_key)`,
  wrapped in the same try/except-log-and-continue pattern as the AI review
  call. Include `result["duplicate_detection"] = dup_detection_results` in the
  final result dict when it ran.
- Only wired into the `is_per_flw` branch — the `combined` branch doesn't
  compute visit clusters at all today and is out of scope here (same as
  Visit Clustering itself).

### `weekly_dual_track_audit.py` / job handler plumbing

- `build_track_audit_calls` gains an `enable_duplicate_detection=None` kwarg,
  threaded into `criteria` exactly like `enable_time_gap` (only set when not
  `None`).
- `weekly_dual_track_audit_create` job handler reads
  `job_config.get("enable_duplicate_detection", state.get("enable_duplicate_detection"))`
  and threads it through to `build_track_audit_calls` and into
  `wda.update_run_state(...)`, alongside the existing clustering fields.
- `DEFINITION["config"]["audit_batch"]["visit_clustering"]` gains
  `"enable_duplicate_detection": False`.

### Render code changes

- New state `enableDuplicateDetection`, initialized from
  `runState.enable_duplicate_detection ?? clustering.enable_duplicate_detection ?? false`
  — same pattern as `enableTimeGap`.
- New checkbox in the Visit Clustering tile: **"Send groupings to the
  Duplicate Detection API"**, with helper text clarifying it checks whatever
  groupings the time/distance settings above already produced, across every
  selected image path/track — not gated by anything in the image-types tile.
- Included in the `actions.startJob(...)` payload as `enable_duplicate_detection`.
- View-only summary line (currently "Visit clustering: within X min and Y m /
  not applied") gets `+ Duplicate Detection` appended when enabled.

## Testing plan

Local `pytest` coverage (mocking the new HTTP client and signed-URL calls —
the real gateway/endpoint aren't reachable in dev, and the signed-URL
endpoint isn't deployed to prod yet):

- `_reviewers_for_path` / `_image_audits`: gating rules for all 3 classifier
  keys (muac substring, exact KMC path match, unknown/inapplicable keys
  dropped) — pure-function tests, no mocking needed.
- `UpdateAuditBatchConfigView`: `classifiers` round-trips correctly; rejects
  non-dict, non-list-of-str, and unknown classifier keys.
- `run_duplicate_detection`: given fake targets + a mocked client returning a
  known `groups` response — assert flagged blobs get `ai_notes`/`ai_result`
  merged (not clobbering pre-existing reviewer results), untouched-only
  `result` auto-tagging, groupings under 2 images skipped, and one bad
  grouping's API error doesn't stop the rest.
- `DuplicateDetectionClient`: request shape, 429/error handling — same style
  as existing `ScaleValidationClient` tests if any exist, otherwise net new.
- `AuditDataAccess.get_attachment_signed_url`: happy path + graceful `None`
  on HTTP error.
- `build_track_audit_calls` / job handler: `enable_duplicate_detection` flows
  through to `criteria` and to `wda.update_run_state`.

No live E2E against the real `detect_duplicates` gateway or the
`attachment_signed_url` endpoint is possible before Monday's deploy — that
verification happens after this PR, against `main`, once the endpoint is
live.
