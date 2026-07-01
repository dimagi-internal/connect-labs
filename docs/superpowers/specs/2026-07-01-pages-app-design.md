# Pages App — Composable Card Surfaces for Opps, Programs & Users

**Date:** 2026-07-01
**Status:** Design approved (brainstorming) — pending spec review → implementation plan
**App:** new Django app `connect_labs/pages/`

## Problem

We need the ability to define custom landing pages for opportunities, programs, and
users. Two concrete near-term drivers:

- **Use case A — Internal program-management hub.** Multiple Dimagi users go to one
  shared, program-scoped landing page that links to (and summarizes) everything they
  need for managing that program — audits, workflows, dashboards, etc.
- **Use case B — External task landing.** An external Connect user (an FLW or
  partner-org member) is already working in `connect.dimagi.com`, clicks a clear URL
  we shared with them, lands in labs to do a specific slice of work (e.g. an audit),
  then returns to Connect. The existing labs chrome is already minimal and visually
  matches Connect, so no special "kiosk" chrome is required.

A third driver — an **Ops work-queue** ("what needs me today" across opps) — is
explicitly **deferred**. It is a different beast (work-queue, not composition) and we
want a better sense of what lives inside cards first.

Both A and B are, mechanically, the **same thing**: an authenticated page of cards at
a clean, shareable URL, differing only in scope and card set. A is comprehensive and
program-scoped; B is lean and task-scoped.

## Naming note

There is already a `connect_labs/labs/configurable_ui/` module, but it is an
unrelated per-child *timeline/detail widget* framework (KMC/nutrition). This feature
is a **navigation/landing hub**, so it lives in a new, distinctly-named app: `pages`.

## Core concepts

Three concepts, one uniform engine:

- **Surface** — a named, slugged page of cards, scoped to an entity. Stored as a
  `LabsRecord` (all labs persistence goes through the Labs Record API):
  - `type = "surface"`
  - `experiment = <program_id or opportunity_id>` (scope key)
  - scoped via `program_id` / `opportunity_id` / `organization_id` (and `username`
    for user-scoped surfaces later)
  - `data = { title, slug, scope, cards: [CardInstance...], options }`
  - Served at `/labs/p/<slug>`. Slug is unique and human-friendly; the record id is
    never in the URL (it is the "clear URL we shared with them").

- **CardInstance** — one placed card on a surface:
  `{ provider, target, options, layout }`
  - `provider` — which card provider (e.g. `"audit"`, `"workflow"`)
  - `target` — what it points at; shape is provider-defined (an `opportunity_id`, a
    `program_id`, a workflow id, …)
  - `options` — display tweaks (title override, size)
  - `layout` — order / column placement

- **CardProvider** — turns a `target` into a rendered card. Two kinds:
  - **Core-object providers** — platform-shipped, one per first-class object type.
    `audit` is the first. Adding another object type (solicitation, fund, coverage,
    …) is one new provider file — **no engine change**.
  - **Workflow-declared cards** — a workflow template declares its own card spec
    alongside `DEFINITION` / `RENDER_CODE` / `PIPELINE_SCHEMAS`. Registering the
    workflow **auto-registers a provider** for it. This is where "quite complex"
    cards live, reusing the workflow's own pipeline data and render approach.

A **surface is an ordered composition of card instances**; each provider renders its
own card. The engine never needs to know provider internals. The system extends by
adding providers, not by editing the engine.

## Provider contract

Providers are registered in `pages/providers/` via an auto-discovery registry
(mirroring how workflow templates self-register). Each exposes:

```
key           "audit"                            # stable id used in CardInstance.provider
label         "Audit summary"                    # human name (authoring / future palette)
target_kind   "opportunity"                     # what `target` must reference
entitled(request, target) -> bool                # reuse get_org_data / labs_context scope
get_card_data(request, target, options) -> dict  # uniform payload (below)
```

The uniform **payload** — so the page engine and client harness stay provider-agnostic:

```json
{
  "title": "…",
  "status": "…",
  "metrics": [{ "label": "…", "value": "…", "trend": "…" }],
  "body": "…optional…",
  "cta": { "label": "Open audit", "url": "/labs/…" },
  "card_type": "audit_summary",
  "render_code": "…optional JSX escape hatch…"
}
```

- **Core-object provider (`audit`, first):** given an `opportunity` target, returns
  audit status + counts (e.g. "142 visits, 118 reviewed, 12 flagged") and a CTA that
  deep-links into the real audit tool. Cards are **actionable entry points**, not just
  summaries — they summarize state *and* link into the working tool.

- **Workflow-declared cards:** a workflow template gains an optional `CARD` block:
  `{ card_type or render_code, data_source (pipeline + fields), title, cta }`. When
  present, registering the workflow auto-registers a provider whose `target` is a
  workflow id; the card summarizes that specific workflow instance using its pipeline
  data.

## Rendering & assembly

One uniform client harness, two rendering paths:

- **Shipped renderers** keyed by `card_type` (`stat`, `list`, `summary`,
  `audit_summary`, …) — React components we ship. Common shapes are pure config.
- **JSX escape hatch** — a provider (especially workflow-declared) may return
  `render_code`, transpiled by the **same Babel/React runtime the workflow engine
  already uses**. Unbounded richness on proven infra; no separate runtime.

**Assembly flow:** `/labs/p/<slug>` → resolve the surface record → for each
CardInstance, look up its provider → `entitled()` check → render a grid of card
**shells**. **Each card lazy-loads its own data** via a per-card endpoint
(`/labs/p/<slug>/card/<i>/data`), mirroring the existing pipeline-preview / SSE
pattern. The surface paints instantly with skeletons; one slow audit card never
blocks the page.

## Exposure, URLs & permissions

- **URL:** `/labs/p/<slug>` — human-friendly, stable, shareable.
- **Auth:** existing Connect OAuth. No new auth, no public/tokenized links in v1.
- **Chrome:** renders inside the existing labs chrome as-is (already minimal, matches
  `connect.dimagi.com`). No kiosk/stripped mode.
- **Permissions:** the surface is reachable by any authenticated user who has the
  link, but **each card self-guards** via its provider's `entitled()` check against
  the viewer's `get_org_data` / `labs_context` scope. Unentitled cards are **silently
  dropped** (no access-denied noise). A single shared URL therefore degrades
  gracefully per viewer — each person sees only the cards they may see.

## Authoring via MCP (v1 — no builder UI)

Surfaces are AI-composed through new `connect_labs` MCP tools plus a thin skill.
A surface is just a scoped `LabsRecord`, so the CRUD tools wrap the existing record
API:

- `pages_list_providers` — available card providers + their `target_kind` and options,
  so the model knows what it can place.
- `pages_create` / `pages_get` / `pages_update` / `pages_list` — CRUD on surface
  records.
- A `pages-author` skill documents the surface schema + provider catalog so Claude can
  compose a surface from a prompt like *"build a program hub for program 25 with an
  audit card per opp and the weekly-review workflow card."*

The `cards[]` data model is designed so a drag/arrange **builder UI** (the "hybrid C"
endgame) is purely additive later — it would write the same `cards[]` array with no
schema rework.

## v1 scope (YAGNI boundary)

**In:**
- the `pages` Django app
- surface `LabsRecord` (`type="surface"`) + slug routing at `/labs/p/<slug>`
- provider registry with auto-discovery
- **one** core-object provider: `audit`
- workflow-declared `CARD` support, with one real workflow wired up end-to-end
- shipped renderers for `stat` / `list` / `summary` + the JSX escape hatch (reusing
  the workflow Babel/React runtime)
- lazy per-card data loading via per-card endpoints
- MCP authoring tools + `pages-author` skill
- use cases **A** (internal program hub) and **B** (external task landing)

**Out (deferred):**
- Ops work-queue
- builder UI
- public / tokenized links
- providers beyond `audit` + workflow-declared
- cross-surface theming
- user-scoped surfaces (schema leaves room via `username` scope, not built in v1)

## Open questions / to confirm during planning

- Exact shape of the `audit` provider payload (which counts/statuses) — pin against
  the real audit `data_access.py`.
- Which existing workflow to wire as the first workflow-declared `CARD`.
- Slug uniqueness/collision handling and whether slugs are scoped globally or per
  entity.
