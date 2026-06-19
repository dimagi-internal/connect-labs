# `/api/export/` full drop-in parity (issue #650)

**Status:** approved design
**Issue:** [#650](https://github.com/jjackson/connect-labs/issues/650) — follow-up to #637 / #645
**Date:** 2026-06-18

## Goal

Make the synthetic `/api/export/` API a true drop-in mirror of production Connect's
`/export/` API, so an external consumer (Scout) can point its **unmodified** production
Connect export client + standard `connect_sync` pipeline at `labs.connect.dimagi.com/api`
with an MCP PAT and materialize a synthetic opp end-to-end — "being labs" becomes pure
config, not a code path.

Implements the four code gaps from the issue (1, 2, 3, 5). Gap 4 (populating
`app_structure.json`'s `deliver_app`) is GDrive fixture **data**, not code, and is out of
scope here.

## Existing surface (unchanged unless noted)

- Routes mounted at `/api/export/` (`config/urls.py`), defined in
  `commcare_connect/labs/export_api/urls.py`.
- Views in `commcare_connect/labs/export_api/views.py`:
  `OpportunityListView`, `OpportunityDetailView`, `OpportunityDataView(endpoint=...)`,
  `AppStructureView`. All extend `_ExportView` (`MCPTokenAuthentication` + `IsAuthenticated`).
- Auth: `MCPTokenAuthentication` resolves an MCP PAT bearer token to a Django user.
- Visibility: `_visible_opp_or_404(user, opp_id)` → `SyntheticOpportunity.is_visible_to(user)`;
  404 for not-registered/not-visible alike (mirrors Connect, no opp-id leak).
- Data: `SyntheticExportClient.fetch_all(endpoint)` → `FixtureStore.load_endpoint(opp_id, key)`,
  reading per-opp JSON from GDrive (`ENDPOINT_FILES` maps key → filename; missing file → `[]`).

## Production reference (verified against `dimagi/commcare-connect`)

- **`opp_org_program_list`** (`ProgramOpportunityOrganizationDataView`): un-paginated
  `JsonResponse({"organizations", "opportunities", "programs"})`, shaped by:
  - `OrganizationDataExportSerializer`: `{id, slug, name}`
  - `ProgramDataExportSerializer`: `{id, name, delivery_type (slug), currency, organization (slug)}`
  - `OpportunityDataExportSerializer`: `{id, name, date_created, organization (slug), end_date,
    is_active, program (id|null), visit_count}`
- **Keyset pagination** (`IdKeysetPagination`): params `last_id`, `page_size`
  (default 1000, max 5000), `cursor_order` ∈ {`forward`,`reverse`}. Envelope is
  **`{next, results}`** — no `count`. Orders by DB `id`; `next` carries `last_id` of the
  last row + `page_size` + `cursor_order`.
- **payment / invoice / assessment**: list endpoints at `opportunity/<id>/payment/`,
  `/invoice/`, `/assessment/` (singular), same paginated envelope.

## Gap 1 — `GET /api/export/opp_org_program_list/`

New `OppOrgProgramListView(_ExportView)`. Returns un-paginated
`{"organizations", "opportunities", "programs"}` matching the production serializer field
sets above.

**Purely synthetic:** the tree is built **only** from `SyntheticOpportunity` rows with
`labs_only=True, enabled=True` that pass `is_visible_to(request.user)`. It never reads
session OAuth org data or real Connect orgs.

Per-opp values come from the registry row + its `opportunity.json` fixture:

| field | source |
| --- | --- |
| org `id`, `slug` | `synthetic_org_slug(opp)` (string slug; labs convention — synthetic orgs have no int PK) |
| org `name` | `opp.org_name or "Labs Synthetic"` |
| program `id` | `synthetic_program_id(opp)` = `opp.program_id or opp.opportunity_id` |
| program `name` | `opp.program_name or "Labs Synthetic"` |
| program `delivery_type`, `currency` | `null` (no synthetic equivalent; present for field parity) |
| program `organization` | org slug |
| opp `id` | `opp.opportunity_id` |
| opp `name` | fixture `name` → else `opp.label` → else `f"Synthetic {id}"` |
| opp `date_created` | fixture `date_created` → else `opp.created_at` (ISO) |
| opp `organization` | org slug |
| opp `end_date` | fixture `end_date` → else `null` |
| opp `is_active` | fixture `is_active` → else `True` |
| opp `program` | program id |
| opp `visit_count` | `opp.visit_count or 0` |

Orgs and programs are de-duplicated by slug / program id respectively (several opps can
share one program via `SyntheticOpportunity.program_id`).

### Anti-drift refactor (shared primitives)

The org-slug / program-id derivation rules already live inline in
`context._merge_labs_only_opps` (drives the labs UI's `user_organizations` etc.). If the
endpoint re-implements them, Scout's `opportunities[].organization` slug could silently
stop matching the rest of labs.

Extract the **derivation primitives only** into a new
`commcare_connect/labs/synthetic/org_tree.py`:

```python
def slugify(value: str) -> str: ...
def synthetic_org_slug(opp) -> str:      # f"labs-synthetic-{slugify(opp.org_name or 'Labs Synthetic')}"
def synthetic_program_id(opp) -> int:    # opp.program_id or opp.opportunity_id
```

Refactor `context._merge_labs_only_opps` to import and use these (replacing its local
`_slugify` + inline formulas) **with byte-identical output** — its template-facing dict
shape (the `labs_only` markers, `program_name` on opps, org `id == slug`) is unchanged. The
endpoint's own tree-assembly (production field set) lives in the view; only the small
derivation rules are shared.

## Gap 2 — payment / invoice / assessment endpoints

- Add to `ENDPOINT_FILES` (`fixture_store.py`): `"payment": "payment.json"`,
  `"invoice": "invoice.json"`, `"assessment": "assessment.json"`.
- Add 3 routes (`urls.py`) via the existing `OpportunityDataView.as_view(endpoint=...)`:
  `opportunity/<id>/payment/`, `/invoice/`, `/assessment/` (singular, matching production).

No new view code: `FixtureStore` already returns `[]` for a missing fixture, so an opp
without the data serves an empty page `{"next": null, "results": []}` automatically.

## Gap 3 — keyset pagination (exact mirror)

Replace `ExportPageNumberPagination` with `IdKeysetPagination` (new, in
`export_api/pagination.py`) for **all** list endpoints (`OpportunityListView`,
`OpportunityDataView`). It operates on the in-memory fixture list (not a queryset):

- params: `last_id` (int), `page_size` (default 1000, max 5000), `cursor_order`
  (`forward` default | `reverse`).
- **Envelope: `{next, results}`** — exact production match, `count` dropped.
- **Cursor rule for dict rows:** cursor value per row = `row["id"]` if `"id"` present,
  else its positional index. Sort by cursor; forward keeps `cursor > last_id`, reverse
  `cursor < last_id`; fetch `page_size + 1` to detect a next page.
  - `user_visits` rows carry `id` → keyset matches Scout's resumable `last_id` semantics
    exactly.
  - `completed_works` / `payment` / `invoice` / `assessment` / `completed_module` rows lack
    `id` → positional cursor; stable for static fixtures; Scout's full-refresh (follow
    `next`) works unchanged.
- `next` = absolute URI of `request.path` with `last_id`/`page_size`/`cursor_order` set
  (all other query params preserved), or `null` when no further rows.

`OpportunityDetailView` (single object) and `AppStructureView` are not paginated — unchanged.

## Gap 5 — `visit_count` in opp detail

`OpportunityDetailView.get` already resolves the opp via `_visible_opp_or_404` (return value
currently discarded). Capture it and inject into the response dict:
`row["visit_count"] = opp.visit_count if opp.visit_count is not None else row.get("visit_count", 0)`.
Additive field; does not affect drop-in consumers.

## OpenAPI

- `ExportPageSerializer` (`serializers.py`): drop `count`, keep `results` + `next`
  (matches the new envelope).
- Document `opp_org_program_list` response via an inline/explicit serializer
  (`{organizations, opportunities, programs}`).
- Document keyset params (`last_id`, `page_size`, `cursor_order`) on the list endpoints.

## Testing (`export_api/tests/`, FakeDrive pattern)

1. **opp_org_program_list:** correct three-key shape + production field sets; only visible
   synthetic opps appear (flag off / domain mismatch / non-labs-only excluded → omitted);
   link consistency: every `opportunities[].organization` resolves to an
   `organizations[].slug`, every `opportunities[].program` to a `programs[].id`; multiple
   opps sharing `program_id` collapse to one program; purely synthetic (no real-org leakage).
2. **payment / invoice / assessment:** empty page when fixture absent; correct rows +
   keyset envelope when present.
3. **visit_count:** present in opp detail, sourced from the registry row, falls back to 0.
4. **keyset pagination:** `last_id` advances; `cursor_order=reverse`; `page_size` clamp to
   max 5000; `next` link round-trips; id-mode (`user_visits`) vs index-mode
   (`completed_works`); envelope is exactly `{next, results}`.
5. **regression:** update existing page-number/`count` assertions to the keyset envelope.

## Files touched

- `commcare_connect/labs/synthetic/org_tree.py` — **new** (shared derivation primitives)
- `commcare_connect/labs/context.py` — use shared primitives (output-preserving)
- `commcare_connect/labs/synthetic/fixture_store.py` — 3 new `ENDPOINT_FILES` keys
- `commcare_connect/labs/export_api/pagination.py` — `IdKeysetPagination` replaces page-number
- `commcare_connect/labs/export_api/serializers.py` — envelope serializer drops `count`
- `commcare_connect/labs/export_api/views.py` — `OppOrgProgramListView`, keyset wiring,
  `visit_count` in detail
- `commcare_connect/labs/export_api/urls.py` — `opp_org_program_list/` + 3 endpoint routes
- `commcare_connect/labs/export_api/tests/` — new + updated tests
