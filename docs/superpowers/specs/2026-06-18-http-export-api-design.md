# HTTP `/api/export/` API for synthetic opportunities

**Issue:** [#637](https://github.com/dimagi-internal/connect-labs/issues/637)
**Date:** 2026-06-18

## Goal

Expose authenticated **HTTP** endpoints on connect-labs that serve **synthetic**
opportunity data in the exact JSON shape of CommCare Connect's real `/export/...`
API, so an external consumer (the Scout data-agent platform) can pull synthetic
data over the network as a new data source — identically to how it pulls from
real Connect.

Today synthetic data is only reachable **in-process** via `SyntheticExportClient`
reading fixtures through `FixtureStore`. There is no HTTP surface. This work wires
the existing fixture-serving code to REST routes. It is mostly wiring, plus one
small data-logic addition (an `app_structure` fixture key).

## Scope

- **In scope:** `labs_only=True` synthetic opportunities (IDs in the reserved
  `>= 10_000` range). These have no real Connect opp behind them and are gated
  purely by `view_synthetic_opps` + `allowed_domains` — no real-Connect
  membership is involved.
- **Out of scope:** `labs_only=False` synthetic opps (need real-Connect
  membership checks), `labs_record`, and `image/{id}`.
- **Added vs. the original issue:** the `app_structure` endpoint is **in scope**
  (see below) and is conditionally present per opp.

## Endpoints

All under the `/api/export/` prefix. All require a valid MCP Personal Access
Token (PAT).

| Method · Path | Returns | Notes |
|---|---|---|
| `GET /api/export/opportunities/` | paginated envelope | `results` = the `opportunity.json` detail dict for each synthetic opp **visible** to the token user |
| `GET /api/export/opportunity/<int:opportunity_id>/` | bare dict | the `opportunity.json` object |
| `GET /api/export/opportunity/<id>/user_visits/` | paginated envelope | |
| `GET /api/export/opportunity/<id>/user_data/` | paginated envelope | FLW roster |
| `GET /api/export/opportunity/<id>/completed_works/` | paginated envelope | |
| `GET /api/export/opportunity/<id>/completed_module/` | paginated envelope | |
| `GET /api/export/opportunity/<id>/app_structure/` | `{"learn_app", "deliver_app"}` wrapper | parity with real Connect: always 200 with both keys (each app JSON or null); honors `?app_type=learn\|deliver\|both` (default `both`); invalid app_type → 400; no app fixture → both keys null |

### Response shapes

- **Paginated endpoints** return the production envelope exactly:
  `{"results": [...], "next": "<url|null>", "count": <int>}`. Support
  `?page_size=` (default `2500`). Rows are sliced in the view (the fixture store
  returns all rows in one batch) and a real absolute `next` URL is emitted.
- **Single-object endpoints** (`/opportunity/{id}/` and `/app_structure/`) return
  the bare dict.
- Field names/types must match what `ExportAPIClient` returns today — i.e. what
  the fixtures already contain (`FixtureStore.ENDPOINT_FILES`).

## Architecture & placement

The project's general API approach is **DRF + drf-spectacular**, with each app
contributing its own `urls.py` included from `config/urls.py`
(solicitations, tasks, audit, microplans all follow this). Global DRF defaults:
OAuth2/Session auth + `IsAuthenticated` (`config/settings/base.py:362`).

New package `connect_labs/labs/export_api/` — a sub-package of the existing
`labs` app (no models, so **not** a separate `INSTALLED_APP`), structured like
`labs/explorer/`:

```
labs/export_api/
  __init__.py
  authentication.py   # MCPTokenAuthentication (DRF BaseAuthentication)
  pagination.py       # ExportPageNumberPagination -> {results, next, count}
  views.py            # DRF APIViews + a shared visibility helper
  urls.py             # 7 routes
  tests/              # auth, visibility, pagination, shape parity, app_structure
```

Mounted in `config/urls.py`:

```python
path("api/export/", include("connect_labs.labs.export_api.urls")),
```

placed **before** the existing `path("api/", include("config.api_router"))` line.
Django's `include()` does not backtrack, so the more-specific prefix must come
first.

## Components

### `authentication.py` — `MCPTokenAuthentication(BaseAuthentication)`

- Reads `Authorization: Bearer <pat>`.
- Verifies via `MCPAccessToken.verify(raw)` (the same PAT machinery the MCP
  server uses); on success returns `(user, token)`.
- No `Authorization` header → returns `None`. Header present but invalid/expired
  → raises `AuthenticationFailed`.
- Implements `authenticate_header()` → `'Bearer realm="labs-export"'` so DRF
  emits **401** (not 403) on missing/invalid credentials.
- Set explicitly as `authentication_classes` on each view, overriding the global
  OAuth2/Session default.

### Authorization — `get_visible_opp_or_404(user, opportunity_id)`

Shared helper. Loads the `SyntheticOpportunity` and checks `is_visible_to(user)`.
That method already returns `False` unless the opp is `labs_only=True`,
`enabled=True`, the user has `view_synthetic_opps`, and the email-domain gate
passes — so the labs_only scope is enforced for free. Not visible / not
registered → **404** (mirrors prod's 404-on-no-access; avoids leaking
existence).

### `pagination.py` — `ExportPageNumberPagination(PageNumberPagination)`

- `page_size = 2500`, `page_size_query_param = "page_size"`.
- `get_paginated_response` overridden to emit exactly
  `{"results", "next", "count"}` (drops DRF's default `previous`, for parity with
  the prod envelope).
- `next` is a full absolute URL with `?page=N&page_size=M`. Following it to
  exhaustion yields `count` rows with no dupes. (Synthetic uses page-number
  paging rather than prod's opaque cursor — the envelope shape is what matters to
  Scout.)

### `views.py` — DRF `APIView`s

All set `authentication_classes = [MCPTokenAuthentication]` and
`permission_classes = [IsAuthenticated]`.

- **`OpportunityListView`** — `GET /opportunities/`. Lists synthetic opps where
  `is_visible_to(user)`; for each, returns its `opportunity.json` detail dict.
  Paginated envelope.
- **`OpportunityDetailView`** — `GET /opportunity/<id>/`. Authorize, then return
  the bare `opportunity.json` dict.
- **`OpportunityDataView`** — one class serving the four paginated endpoints
  (`endpoint` kwarg bound per URL). Authorize → build client → `fetch_all` →
  paginate the in-memory list → envelope.
- **`AppStructureView`** — `GET /opportunity/<id>/app_structure/`. Mirrors real
  Connect's `AppStructureView` exactly: always returns the
  `{"learn_app": <json|null>, "deliver_app": <json|null>}` wrapper. Reads
  `?app_type=` (default `both`; valid `learn`/`deliver`/`both`; anything else →
  **400** with `{"error": ...}`). Loads the single `app_structure.json` wrapper
  fixture via `fetch_all("app_structure")` and nulls out the key(s) the
  requested `app_type` excludes. A missing fixture yields `{}`, so both keys
  come back null at **200** (an opp that exists but has no app — matching prod's
  shape rather than 404-ing).

### Data flow (paginated endpoint)

1. DRF → `MCPTokenAuthentication` resolves the user (401 on bad/missing PAT).
2. `IsAuthenticated` permission.
3. `get_visible_opp_or_404(user, opportunity_id)` (404 if not visible).
4. `client = get_export_client(opportunity_id, access_token="", user=user)` —
   returns a `SyntheticExportClient` because the opp is registered + enabled
   (the `access_token` arg is unused on the synthetic path). Defensively assert
   the returned client is synthetic.
5. `rows = client.fetch_all(endpoint_key)` — full list. We pass the bare
   `endpoint_key` (`"user_visits"`, `"app_structure"`, `""`, …) which
   `FixtureStore.ENDPOINT_FILES` already maps.
6. Paginate the in-memory list → `{results, next, count}`.

The detail endpoint unwraps the single-element list to a bare dict.

### One data-logic change: `FixtureStore.ENDPOINT_FILES`

Add one key:

```python
"app_structure": "app_structure.json",
```

`app_structure.json` holds the full `{"learn_app", "deliver_app"}` wrapper (the
shape real Connect returns for `app_type=both`). This is the only change to
existing fixture code. `load_endpoint` returns the parsed wrapper when the file
exists and `[]` on miss — which the view treats as "no app" (200 with null
keys). Writing this file into fixture folders is the generation side's job and is
out of scope here.

## Errors

| Condition | Status |
|---|---|
| Missing or invalid PAT | **401** (`WWW-Authenticate: Bearer realm="labs-export"`) |
| Opp not visible / not registered / not labs_only | **404** |
| `app_structure?app_type=` other than `learn`/`deliver`/`both` | **400** + `{"error": ...}` |
| `app_structure` for an opp with no app fixture | **200** with both keys null |
| Empty fixture (paginated endpoint) | **200** `{results: [], next: null, count: 0}` |
| Page number past the end | **404** (DRF standard) |

## Testing

Pytest, in `labs/export_api/tests/`. Monkeypatch the drive client so `fetch_all`
returns known rows (the `synthetic/tests/test_factory.py` pattern); seed a
`SyntheticOpportunity` + a `User(view_synthetic_opps=True)` + an
`MCPAccessToken`; drive via DRF `APIClient` with
`HTTP_AUTHORIZATION="Bearer <raw>"`.

Coverage:

- **Auth:** missing header → 401; garbage token → 401; valid PAT → 200 and runs
  as the token's user.
- **Visibility gating:** user without `view_synthetic_opps` → 404; user whose
  email domain is not in a non-empty `allowed_domains` → 404; permitted user →
  200. `/opportunities/` lists only the permitted opps.
- **Pagination correctness:** e.g. 7 rows at `page_size=3` → 3 pages; following
  `next` to exhaustion yields 7 unique rows; the last page's `next` is `null`;
  total equals `count`.
- **Shape parity:** fixture rows are returned verbatim in `results` (field
  names/types preserved); single-object endpoints return the bare dict.
- **app_structure:** wrapper fixture present → 200 with both keys; `app_type`
  filters to the requested key(s) (others null); no fixture → 200 with both
  null; invalid `app_type` → 400.

## Acceptance criteria (from the issue)

- [ ] External caller with a valid PAT can `GET …/user_visits/?page_size=500` for
  a `labs_only` synthetic opp and receive `{results, next, count}` with fixture
  rows.
- [ ] `next` paginates through all rows; exhaustion yields `count` total with no
  dupes.
- [ ] All core endpoints work (`opportunities/`, detail, `user_visits`,
  `user_data`, `completed_works`, `completed_module`, plus `app_structure`).
- [ ] `/opportunities/` lists only synthetic opps the token user may see.
- [ ] No PAT → 401; opp the user can't see → 404.
- [ ] Response field shapes match `ExportAPIClient` for the same endpoints.
- [ ] Tests cover auth failure, visibility gating, pagination correctness, and
  shape parity against a fixture, plus app_structure wrapper/app_type/absent/400.
