# Pages — Scoping & Context-Aware Slug Resolution

**Date:** 2026-07-01
**Status:** Design approved (brainstorming) — pending spec review → implementation plan
**App:** `connect_labs/pages/` (enhancement to the shipped pages app, PR #796)

## Problem

The shipped `pages` app hardcodes surfaces as **public** LabsRecords and resolves a
slug via the `public` flag with no viewer context. That means:

- Every landing page is world-readable at the API layer — wrong for external partner
  pages, which should be limited to the people who share the page's scope.
- `get_surface_by_slug` can't resolve a scoped (non-public) record at all (it queries
  `public=True` with no scope), so scoped pages are unreachable — and even the public
  path is currently returning `null` for a freshly-created public surface.

We want a surface to be scoped to any of **user / org / opp / program / public**, and
we want resolution to work the standard labs way: the viewer's `labs_context` (URL
param → session) supplies the scope, and the prod Labs Record API's own membership
check enforces the ACL.

Driving use case: program 176 ("CHC PRE-RCT (Nigeria)") has 4 opps, each with its own
"MUAC Image Audit" workflow (defs 5049/5051/5053/5055 for opps 1973/1976/1978/1982).
External LLO users have **opp ACL, not program ACL**. So each opp needs its own
opp-scoped landing page surfacing that opp's one MUAC workflow.

## Design

### 1. Scope model — reuse LabsRecord's own scoping

A surface is scoped to exactly one of `user` / `org` / `opp` / `program` / `public`.
No new ACL machinery: the scope **is** the LabsRecord's existing FK / flag —
`username`, `organization_id`, `opportunity_id`, `program_id`, or `public=True`. The
record's scoping is the ACL; the prod API enforces it.

A lightweight `scope: {"type": "opp"|"program"|"org"|"user"|"public", "id": <id or
username>}` hint is also stored in `data` — purely for listing/display and for the MCP
`pages_get` path (which has no `request.labs_context`). It is **not** the source of
truth for ACL; the FK is.

### 2. Creation

`SurfaceDataAccess.create_surface` and the `pages_create` MCP tool gain the full scope
set (`organization_id`, `username`, `public` in addition to the existing
`opportunity_id` / `program_id`) and **drop the hardcoded `public=True`**. Exactly one
scope is set per surface. `public=True` is used only when the caller explicitly asks
for a public page. The `scope` hint in `data` is derived from whichever scope was set.

### 3. Context-aware resolution

`get_surface_by_slug` becomes request-aware: `get_surface_by_slug(request, slug)`.

- Read `request.labs_context` (`opportunity_id` / `program_id` / `organization_id`,
  already populated from URL query params → session by the labs middleware) and
  `request.user`.
- Query `get_records(type="surface", data__slug=slug, <scope from context>)`, choosing
  the scope param from context in priority order **opp → program → org**, then a
  `username`-scoped query for `user` pages, then the `public` path. Return the first
  match (deterministic by lowest id within a query, as today).
- The prod API's membership check does the ACL: a scoped page is invisible — even at
  the API — to a viewer whose context/access doesn't match.
- External shared links carry the standard context param when needed
  (`/labs/p/<slug>?opportunity_id=1973`); users with a single accessible scope
  auto-resolve via session context.

For the MCP `pages_get(slug, ...)` tool (no `request`): accept optional scope params
and/or resolve against the calling user's accessible scopes + public. (Web view is the
primary path; MCP get is a convenience.)

### 4. Soft not-found — in-chrome + context switcher

When no surface matches the current context, `SurfacePageView` does **not** raise
`Http404`. It renders inside the normal labs chrome a "no page for your current
context" state that includes the existing labs context switcher partial
(`connect_labs/templates/labs/context_selector.html`), so the viewer can switch
org/program/opp and reload — which may then resolve the page. The per-card data
endpoint (`CardDataView`) keeps its JSON `403` (not entitled) / `404` (missing
surface / bad index / unknown provider) semantics unchanged.

### 5. This use case (post-change)

Create **four opp-scoped surfaces**, one per opp, each with its single MUAC workflow
card:

| slug (proposed) | opp | workflow def |
| --- | --- | --- |
| `eha-muac` | 1973 | 5049 |
| `jhf-muac` | 1976 | 5051 |
| `solina-muac` | 1978 | 5053 |
| `isodaf-muac` | 1982 | 5055 |

Shared as `/labs/p/<slug>?opportunity_id=<opp>`. The wrongly-public surface 5081
(program-scoped + public) is deleted/cleaned up.

## Testing

- `create_surface` sets the right FK per scope and only sets `public=True` for the
  public scope; derives the `scope` hint. (mock `LabsRecordAPIClient`)
- `get_surface_by_slug(request)` picks the scope from `request.labs_context` and issues
  the correctly-scoped query; falls through opp → program → org → user → public;
  returns None when nothing matches. (mock `labs_context` + client)
- `SurfacePageView` renders the soft not-found template (with the context switcher)
  instead of 404 when resolution returns None; renders cards when it resolves.
- `pages_create` MCP tool passes the new scope params through.

## Out of scope / deferred

- No builder UI (unchanged).
- No slug→scope public index; context-driven resolution is sufficient given
  `labs_context` supplies the scope.
- Resolution across a mega-account's full scope list is not "probed" — we use the
  single active context, so there is no scale concern.
- A `pages_delete` MCP tool is not added here; surface 5081 is cleaned up manually via
  the record API for now.
