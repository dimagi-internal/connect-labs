# Generic indicator cascade — design

**Status:** implemented (this PR). Decision owner: Jon, 2026-09-26.

## Problem

The KMC report family (`kmc_programme_metrics` → companion `kmc_flw_review`, handing
down to `kmc_opp_report`) is the only programme → organisation → opportunity →
worker → case drill in labs. Its data path is already generic (the
`semantic_snapshot` builder grades any registry; hand-down and benchmarks are
template-agnostic), but its presentation lives in page code: five hand-picked tiles
with typed targets, a fifteen-column scorecard, "babies" in the strings, an
opportunity-label table. A second programme (first: ACE's Spark facilitator
programme) cannot have the cascade without a second set of renders.

## Decisions (settled)

1. **A template trio, registry-driven.** `indicator_programme_report` (multi-opp,
   saved runs, hands down), companion `indicator_worker_review`,
   `indicator_opp_report` (receives hand-down, names its source in
   `config.source_workflow_id`, benchmarks tab). KMC's templates are untouched.
2. **Instances follow the deployed template** from creation (`TEMPLATE["follow_template"]`
   → `render_source: {template}`), config resolves from the template on read,
   pipelines are referenced, the registry is bound. Forking remains possible.
3. **Everything programme-specific is registry data** — a display contract resolved
   by `semantic/display.py`, validated on save, carried in every payload as
   `display` and in the measure catalog.
4. **The snapshot spec names only the builder.** `resolve_spec_defaults` derives the
   rest from the registry (below). A stated key always wins, so every KMC spec is
   used exactly as written.
5. **One render for the programme and opportunity reports** (`indicator_report_render.js`,
   opportunity mode keyed on `templateType`), because a handed-down slice IS a
   programme payload for one opportunity. The shared UI pieces (definition modal,
   reading chart, display helpers) moved into `window.LabsReport` (VERSION 3);
   existing components gained optional, backward-compatible props for nouns.

## The contract a registry author satisfies

Per-indicator `meta`: `headline` (true | position), `target` (bands' units),
`label`, `plain`, `order`, `scorecard` (bool), `credibility` (a
`deployment.settings` table). Registry-level `display:` in the indicators document:
`title`, `entity`, `worker`, `organisation` ({name, plural}), `categories`,
`headline_count`, `case_fields` ([{field, label, format, unit}]), `reading`
({column, label, unit}), `targets_note`. All optional; defaults in
`user_docs/semantic-layer.md#how-a-report-reads-it-display`. The organisation level
is `deployment.llo_map`; without one, opportunities stand in for organisations.

Pipelines: the registry's `properties.pipelines.entity` names the alias Layer 1
reads. The template's default pipeline is one visit-level `visits` over Connect's
base visit columns (enough for `visit_quality`); a registry that reads form fields
brings its own via `workflow_create_from_template(pipelines_from=...)` or by editing
the created pipeline's schema.

## Builder defaults (`snapshot_builders.resolve_spec_defaults`)

| key | derived as |
|---|---|
| `scopes` | every scope the registry compiles; `llo*` dropped without an `llo_map` |
| `case_index` | the entity pipeline; folded per entity key when it is visit-level (`group_by`) |
| `visits_pipeline` | the entity pipeline if visit-level, else the first visit-level extra-field pipeline |
| `maturity_anchor` | `first_visit_date` |
| `credibility` | each indicator's `meta.credibility`, under any the spec states |

## Supporting changes

- `workflow_create_from_template` MCP: `pipelines_from` and `config`.
- Companions that compute indicators share the primary's registry record.
- `benchmarks_create_opp_reports` stamps `source_workflow_id` on receiving templates.
- `pipeline-rows` accepts `case_key` (the registry's entity key) for `case_ids`.
- On-disk `kmc` registry declares the five KMC tiles, their targets and mortality's
  credibility table, so a KMC registry renders in the generic report like the KMC one.

## Open questions (defaults chosen)

1. *Per-case indicator contributions in the worker review* — not shown; the review
   reads saved figures only. The `case` scope exists if wanted later.
2. *Org label for an opportunity* (KMC's `OPP_LABEL`) — opportunities show as
   "Opportunity <id>"; a `display.opportunity_labels` map could be added.
3. *Scorecard default* — every `prominence: Top` indicator (18 for KMC vs its 15
   hand-picked); a registry narrows it with `meta.scorecard: false`.
4. *Image audit action* — not ported; the review shows images where visit rows carry
   them. The KMC scale-agent routing stays KMC-only.
