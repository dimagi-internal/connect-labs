# Synthetic Sample Data for Labs Opportunities — Design

**Date:** 2026-04-20
**Author:** jjackson + Claude
**Status:** Design approved; implementation plan pending

## Problem

Labs visualizations (funder dashboard, custom_analysis dashboards, audit UIs, workflow
pipelines, analysis explorer) are driven by real FLW submission data pulled from Connect
via `/export/opportunity/<id>/...` endpoints. For new opportunities that haven't started
collecting data yet — e.g. demos, grant-stage prototyping (Baobab), visualization
iteration before an opp goes live — there's nothing to visualize.

We want to let devs and demo authors mark an opportunity as "synthetic" so that
visualization code paths still run end-to-end, but the read-side export data comes
from fixtures we control instead of prod Connect.

## Scope

**In scope:**
- Intercepting reads from `/export/opportunity/<id>/...` endpoints: `user_visits`,
  `user_data`, `completed_works`, `completed_module`, and the opportunity detail
  endpoint (`/export/opportunity/<id>/`).
- A DB-backed registry of synthetic opportunities with a CRUD UI.
- A per-opp fixture store sourced from Google Drive via a service account.

**Out of scope (explicitly):**
- Writes. If a reviewer flags a visit or a workflow transitions state on a synthetic
  opp, those writes flow normally to prod `LabsRecord`. Cleanup is manual (delete the
  demo opp's records when done).
- Intercepting `/export/opportunity/<id>/image/` or any other endpoint that bypasses
  `ExportAPIClient` and uses raw `httpx`. Those calls still hit prod; for a synthetic
  opp they will typically 404 (phantom image IDs), which is an acceptable v1 limitation.
- Replicating Connect's pagination, keyset cursors, or query-param filtering. Fixtures
  load whole; a single "page" returns all rows.
- Any mocking of `LabsRecordAPIClient`, `CommCareDataAccess`, or `OCSDataAccess`.
- Faker-driven generation (deferred follow-up: "Option D").

## Non-negotiable constraint

**Real API calls must not be measurably slowed** by the existence of the synthetic
system. The registry check happens on every export call, so it has to be essentially
free on the hot path for real opps.

## Architecture

```
┌──────────────────────────┐
│  Caller (views, tasks,   │
│  workflow, analysis)     │
└───────────┬──────────────┘
            │ get_export_client(opp_id, token)
            ▼
┌──────────────────────────┐       ┌──────────────────────────┐
│   Factory                │──────▶│ get_synthetic_opp(opp_id)│
│  (labs/integrations/     │       │  per-worker in-mem cache │
│   connect/factory.py)    │       │  DB: SyntheticOpportunity│
└─────┬─────────────┬──────┘       └──────────────────────────┘
      │             │
  real│             │synthetic
      ▼             ▼
┌──────────────┐  ┌────────────────────────┐
│ ExportAPI    │  │ SyntheticExportClient  │
│ Client       │  │  paginate / fetch_all  │
│ (unchanged)  │  │  (same signature)      │
└──────────────┘  └──────────┬─────────────┘
                             │
                             ▼
                  ┌────────────────────────┐
                  │  FixtureStore          │
                  │  - GDrive (svc acct)   │
                  │  - In-process cache    │
                  │  - Manual reload       │
                  └────────────────────────┘
```

One interception point: a factory function replaces every direct `ExportAPIClient(...)`
constructor call. Real opps go through the unchanged `ExportAPIClient`. Synthetic opps
return a drop-in `SyntheticExportClient` that reads from a `FixtureStore`.

## Components

### 1. Registry model

New Django model in `connect_labs/labs/synthetic/models.py`:

```python
class SyntheticOpportunity(models.Model):
    opportunity_id = models.IntegerField(unique=True, db_index=True)
    label = models.CharField(max_length=200, blank=True)   # human name
    gdrive_folder_id = models.CharField(max_length=200)    # Drive folder ID
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey("users.User", null=True, on_delete=models.SET_NULL)
```

Adding a new local model is fine — labs already has app-local tables (analysis cache,
admin boundaries). This is labs-internal state, not prod Connect data.

### 2. Registry lookup

`connect_labs/labs/synthetic/registry.py`:

```python
_CACHE = {"loaded_at": 0.0, "opps_by_id": {}}
_TTL_SECONDS = 60

def get_synthetic_opp(opportunity_id: int) -> SyntheticOpportunity | None:
    now = time.monotonic()
    if now - _CACHE["loaded_at"] > _TTL_SECONDS:
        rows = SyntheticOpportunity.objects.filter(enabled=True)
        _CACHE["opps_by_id"] = {r.opportunity_id: r for r in rows}
        _CACHE["loaded_at"] = now
    return _CACHE["opps_by_id"].get(opportunity_id)

def invalidate_cache() -> None:
    _CACHE["loaded_at"] = 0.0
```

Per-worker in-memory dict, refreshed every 60s. One DB query per worker per minute;
every API call does a pure dict lookup (no Redis, no DB hit). Hot path cost ≈ zero.

Tradeoff: up to 60s staleness when an opp is added/removed via the UI. A "Refresh
now" button on the list page calls `invalidate_cache()` locally, and a model `save`/
`delete` signal invalidates the current worker's cache. Other workers pick up the
change within 60s. Acceptable for a demo-tier feature.

### 3. CRUD UI

Templates and views in `connect_labs/labs/synthetic/`. Routed under
`/labs/synthetic/` and styled with the existing labs Bootstrap layout (matches the
configurable_ui and explorer admin pages; consistent with user expectations that the
bare Django admin isn't used).

- `GET /labs/synthetic/` — list table: opp_id, label, GDrive folder, enabled toggle,
  last-loaded-at, actions (edit, reload, delete). "Refresh cache" button at top.
- `GET/POST /labs/synthetic/new/` — create form. Fields: opp_id, label, GDrive folder
  ID, enabled, notes. "Test GDrive access" button that lists the endpoint files found
  in the folder without saving.
- `GET/POST /labs/synthetic/<id>/edit/` — edit.
- `POST /labs/synthetic/<id>/reload/` — purge fixture cache for this opp; next call
  re-pulls from GDrive.
- `POST /labs/synthetic/<id>/delete/` — delete row. Does not touch GDrive contents.

Permission: any labs-authenticated user. No org scoping — labs-internal admin tool.

### 4. Fixture store

`connect_labs/labs/synthetic/fixture_store.py`.

**Folder layout per opp:**

```
<parent-folder>/
  opp-<id>-<slug>/          ← gdrive_folder_id points here
    opportunity.json         ← opportunity detail payload (single object)
    user_visits.json         ← list[dict], all rows
    user_data.json           ← list[dict]
    completed_works.json     ← list[dict]
    completed_module.json    ← list[dict]
```

**File shape:** the raw `results` array from an `/export/...` endpoint. Simplest way
to author: call real prod once (`curl` with a dev OAuth token), save the `results`
array into the corresponding filename, commit to GDrive, hand-edit as needed. This is
the "Approach C" loop; fast to get demo-able data.

**Loader:**

```python
ENDPOINT_FILES = {
    "": "opportunity.json",
    "user_visits": "user_visits.json",
    "user_data": "user_data.json",
    "completed_works": "completed_works.json",
    "completed_module": "completed_module.json",
}

class FixtureStore:
    def load_endpoint(self, opp_id: int, endpoint_key: str) -> list[dict] | dict: ...
    def reload(self, opp_id: int) -> None: ...
```

Caching: a per-worker in-memory dict keyed by `(opp_id, endpoint_key)`. Fetched from
GDrive on miss, retained until worker restart or explicit `reload()`. Dataset sizes
are small by definition (demo-scale), so this is fine; simpler than sharing via Redis.

**Unknown endpoint** → log warning, return `[]`. **Missing file** → log warning,
return `[]`. Visualizations render "no data" rather than 500.

**No image support in v1.** `/export/opportunity/<id>/image/` is called from raw
`httpx` callsites that bypass `ExportAPIClient` and are explicitly not migrated in
v1 (see Callsite migration). Those requests still hit prod for synthetic opps and
will typically 404 on phantom image IDs. Image serving from GDrive is listed under
Future work.

### 5. GDrive access

Service account credentials via env var `LABS_SYNTHETIC_GDRIVE_SA_KEY` (JSON path or
blob, same convention as other service-account integrations). The service account must
be shared on the parent "labs-synthetic" folder. Each per-opp folder inherits access.

Use the Drive REST API (directly via `httpx` or via `google-api-python-client`, which
is transitively available). Directory listing to map filenames → file IDs; file
download to fetch bytes.

**Security invariant:** the service account has access to a dedicated labs-synthetic
folder only. **No prod data** lives in that folder, ever. Documented on the CRUD UI
and in the design doc.

### 6. Synthetic export client

`connect_labs/labs/synthetic/client.py`. Drop-in replacement for
`ExportAPIClient`. Same two public methods, same signatures.

```python
class SyntheticExportClient:
    def __init__(self, opp_id: int, fixture_store: FixtureStore):
        self.opp_id = opp_id
        self.store = fixture_store

    def paginate(self, endpoint: str, params: dict | None = None):
        rows = self.store.load_endpoint(self.opp_id, self._endpoint_key(endpoint))
        yield [rows] if isinstance(rows, dict) else rows   # single page, all rows

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        rows = self.store.load_endpoint(self.opp_id, self._endpoint_key(endpoint))
        return [rows] if isinstance(rows, dict) else rows

    def close(self): pass
    def __enter__(self): return self
    def __exit__(self, *a): pass

    @staticmethod
    def _endpoint_key(endpoint: str) -> str:
        # "/export/opportunity/42/user_visits/" -> "user_visits"
        # "/export/opportunity/42/"             -> ""
        ...
```

`params` is accepted and ignored (with a debug log). No pagination slicing, no keyset
cursors — callers use generator-style `paginate()` or `fetch_all()`; yielding one page
with every row works identically.

### 7. Factory

`connect_labs/labs/integrations/connect/factory.py`:

```python
def get_export_client(
    opportunity_id: int,
    access_token: str,
) -> ExportAPIClient | SyntheticExportClient:
    synthetic = get_synthetic_opp(opportunity_id)
    if synthetic:
        return SyntheticExportClient(opportunity_id, fixture_store_for(synthetic))
    return ExportAPIClient(settings.CONNECT_PRODUCTION_URL, access_token)
```

### 8. Callsite migration

Replace direct `ExportAPIClient(base_url, access_token)` with
`get_export_client(opp_id, access_token)` at these callsites. Each already has
`opp_id` and `access_token` in scope — mechanical find-and-replace.

- `connect_labs/audit/views.py` (image-question user_visits fetch)
- `connect_labs/tasks/data_access.py`
- `connect_labs/funder_dashboard/data_access.py`
- `connect_labs/workflow/data_access.py`
- `connect_labs/labs/analysis/data_access.py`
- Any additional direct constructor calls surfaced during implementation

**Not migrated in v1** (raw `httpx` to `/export/opportunity/<id>/image/` or other
paths that bypass `ExportAPIClient`):

- `connect_labs/audit/data_access.py` (image fetch)
- `connect_labs/custom_analysis/kmc/views.py` (image fetch)
- `connect_labs/custom_analysis/rutf/views.py` (image fetch)
- `connect_labs/workflow/views.py` (image fetch)
- `connect_labs/labs/explorer/app_data_access.py` (different endpoint shape)

These keep hitting prod for synthetic opps and will 404 on phantom image IDs. Known
limitation; revisit if demos need image coverage.

## Error handling

| Situation                          | Behavior                                           |
|------------------------------------|----------------------------------------------------|
| Opp not in registry                | Real `ExportAPIClient` (normal)                    |
| Opp in registry, `enabled=False`   | Real `ExportAPIClient` (normal)                    |
| GDrive folder missing/unreachable  | `load_endpoint` returns `[]`, logs warning         |
| Endpoint file missing              | `load_endpoint` returns `[]`, logs warning         |
| Unknown endpoint path              | `load_endpoint` returns `[]`, logs warning         |
| Service account key invalid/missing| Loud failure on first Drive call; surfaced in UI's "Test access" button |

## Testing

New tests under `connect_labs/labs/synthetic/tests/`:

- `test_registry.py` — lookup returns row for enabled opp; returns `None` for disabled
  or unknown. TTL refresh re-queries DB. Manual invalidation clears cache.
- `test_fixture_store.py` — loads JSON via a mocked Drive client. Caches across calls
  to the same (opp, endpoint). `reload()` purges cache. Missing/unknown returns `[]`
  with a log.
- `test_synthetic_client.py` — `paginate()` yields one page with all rows;
  `fetch_all()` returns the flat list. Opportunity-detail endpoint yields a one-item
  list wrapping the dict. Ignored params don't error.
- `test_factory.py` — real opp returns `ExportAPIClient`; synthetic opp returns
  `SyntheticExportClient`.
- `test_views.py` — CRUD round-trip; reload clears cache; non-authed request gets 403.

**Integration check:** set up a synthetic opp pointing at a local directory (stub the
Drive layer to read from disk), hit a real visualization view such as
`funder_dashboard` with that opp_id, assert the page renders with fixture data.

Existing tests are not touched; they mock `ExportAPIClient` directly and keep passing
because the factory only diverges for synthetic opps, which no existing test uses.

## Env vars

- `LABS_SYNTHETIC_GDRIVE_SA_KEY` — service account JSON (path or blob).
- `LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID` — optional; the UI pre-fills a hint when
  creating a new synthetic opp. Not required since each row stores its own folder ID.

## Rollout plan (for the implementation plan, not this spec)

1. Model + migration + registry + in-memory cache + invalidation signal.
2. `FixtureStore` with Drive client + service account auth.
3. `SyntheticExportClient` + factory.
4. Callsite migration (~5 files).
5. CRUD UI with "test access" and "reload" affordances.
6. Docs: `docs/SYNTHETIC_OPPS.md` — "how to dump a real opp's exports and create a
   synthetic opp in 5 minutes."

## Future work

- **Option D (faker-driven generation):** a repo-committed Python script that
  synthesizes FLW visit streams (parameters: FLW count, date range, delivery rate,
  flag rate) and writes the same JSON files to a GDrive folder. Zero runtime changes.
- **Image endpoint migration:** extract the raw-httpx image fetches into a helper
  `get_opportunity_image(opp_id, image_id, token)` that routes through the factory,
  so synthetic opps can serve images from the `images/` subfolder.
- **Writable overlay:** if real demos require reviewers to "approve" visits without
  polluting prod `LabsRecord`, add a per-opp override bucket (`experiment="synthetic:<opp_id>"`)
  that shadows reads. Not needed today.
