---
name: pages-author
description: Use when composing or editing a labs "surface" — a card landing page at /labs/p/<slug> — via the connect_labs MCP. Triggers on "build a program hub", "make a landing page for program/opp X", "add an audit card", "create a pages surface".
---

# Authoring pages surfaces

A **surface** is a card landing page served at `/labs/p/<slug>`. It is a
`LabsRecord` (`type="surface"`, `public=True`) whose `data` holds
`{slug, title, cards, options}`.

## Workflow

1. `pages_list_providers` — see available card providers and their `target_kind`.
2. Build the `cards` list. Each card is:
   `{ "provider": "<key>", "target": {<provider target_kind fields>}, "options": {"title"?: str} }`
   - `audit` → `target = {"opportunity_id": <int>, "opportunity_name"?: str}`
   - `workflow` → `target = {"definition_id": <int>}`
3. `pages_create` with a unique `slug`, a `title`, the `cards` list, and a scope
   (`program_id` for a program hub, `opportunity_id` for a task landing).
4. Share `/labs/p/<slug>`. Each card self-guards: a viewer only sees cards whose
   provider `entitled()` passes for them.

## Rules

- Slugs are lowercase, hyphenated, unique (e.g. `prog-25-hub`).
- Surfaces are public records; never put sensitive literals in `title`/`options`.
  Sensitive data lives behind per-card entitlement, not in the surface config.
- To edit, `pages_get` the slug, change `cards`, then `pages_update` with the
  returned record id.
