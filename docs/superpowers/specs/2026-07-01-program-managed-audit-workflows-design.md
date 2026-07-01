# Program-Managed Audit Workflows — Design

**Status:** Draft for review
**Date:** 2026-07-01
**Context:** CHC PRE-RCT program (id 176), 4 opps: 1973 EHA, 1976 JHF, 1978 SOLINA, 1982 ISODAF.
Builds on the shipped `weekly_dual_track_audit` creator (multi-opp) + `audit_par` report.

## Problem

The current audit workflow is a single **multi-opp** instance (definition 4683) owned by one
opportunity (1973). Two requirements it structurally cannot meet:

1. **Per-org completion.** Each org must see *only their* audits, complete them, and mark *their*
   workflow complete. A multi-opp run has one owner and **one run-level completion state**
   (`complete_run_api` sets a single `status`/`completed_at`) — it cannot represent "EHA done,
   SOLINA in progress," and non-owning orgs can't open the instance at all.
2. **First-class program management.** Triggering creation, managing the shared template, and
   viewing program reports should live at the **program** level — a peer to opp-level management —
   not be buried under one opportunity's picker.

## Decision (confirmed with product)

**Full per-opp workflows for ownership/completion, orchestrated by a first-class program layer.**
The program manages a shared template; each opportunity gets an *exposed instance* it owns and
completes; a program report rolls them up. Opps may differ (e.g. 1976 has an extra rest image) —
per-opp config rides on each instance.

## What exists vs. what is new (from codebase research)

**Exists (reuse):**
- `template_scope="program:<id>"` flag + validation (`mcp/tools/workflows.py:799,821`).
- `program_id` plumbing through `BaseDataAccess` (`workflow/data_access.py:289–352`),
  `LabsRecordAPIClient` (`labs/integrations/connect/api_client.py:54–112`), and
  `request.labs_context` (`labs/context.py:16,229–248`).
- Per-opp config as a map in the definition (`weekly_dual_track_audit` `audit_batch.per_opp`).
- Audit sessions are **per-opp owned and per-opp completable** — the audit app's
  complete/uncomplete endpoints use `try_multiple_opportunities` so each org acts only on its own
  sessions (`audit/views.py:258–326`, `audit/data_access.py:885–925`).
- Saved-runs completion framework (`supports_saved_runs`, `view.complete`, snapshot_inputs).
- `program_admin_report` pattern: `watched_sources` = list of `{opportunity_id,
  workflow_definition_id}`, per-week grid, click-to-drill inline panel, deep links to
  `/audit/{id}/`, `/tasks/{id}/edit/`, run links — all opp-scoped (`templates/program_admin_report.py`).

**New (build):**
- Program-scoped workflow listing that actually returns definitions (today `list_definitions`
  returns empty when `program_id` is set without an opp).
- A flow to **instantiate a template per-opportunity**, stamping each instance with its opp config.
- Per-opp audit workflow that is **single-opp + saved-runs** (org completes + marks done).
- Program report rebuilt on the `watched_sources` model with **completion status** + richer drill.
- Program-level surface (routes, dashboard, template management, creation orchestration).

## Architecture

```
Program 176  ── program-managed template (program:176 scope): tracks, MUAC AI, render, per-opp image map
     │  instantiate per opp (stamp opp's image config)
     ├─ Opp 1973 instance  (owned by 1973)  ── org completes → Mark Run Complete
     ├─ Opp 1976 instance  (owned by 1976, extra image)
     ├─ Opp 1978 instance  (owned by 1978)
     └─ Opp 1982 instance  (owned by 1982)
                 │  watched_sources = [{opp, def} …]
        Program Report ── per-opp × week grid: completion + KPIs, drill → per-FLW rows → audit links
```

## Phase 1 — per-org completion + actionable report (buildable now)

Delivers both requirements using infra that exists; no new routing surface required.

### 1a. Per-opp audit workflow = single-opp `weekly_dual_track_audit` + saved-runs
- Add `supports_saved_runs: True` to the template; the run is one week's audit batch for **one** opp.
- Instance config holds only *that opp's* image paths (subset of the program map) — single-opp
  falls back to `[primary_opp_id]`, already supported.
- **Lifecycle:** create batch (existing job) populates the run → org reviews/completes the audit
  sessions in the audit bulk pages (already per-opp) → **Mark Run Complete** snapshots the results
  (`snapshot_inputs` capturing the per-FLW audit rollup + completion counts) and write-protects it.
- Render: the per-FLW results view already built (v16) — read via `view.*`, disable create/edit
  when `view.isCompleted`, add the completion CTA + "as of" framing.

### 1b. Instantiate the 4 per-opp instances for program 176
- Create one single-opp instance per opp from the (Phase-1 lightweight) shared config, each stamped
  with its opp's `per_opp` entry. Done via a management command / MCP orchestration for now
  (program-surface trigger is Phase 2).

### 1c. Program report rebuilt on `watched_sources`
- Change `audit_par` config `watched_source` (single creator def) → `watched_sources`
  = `[{opportunity_id, workflow_definition_id}]`, one per per-opp instance (the `program_admin_report`
  shape).
- Rollup: per source, read that opp's completed/in-progress run(s), roll up per-FLW; carry
  **completion status** (in_progress / completed + `completed_at`).
- Render (match `program_admin_report` actionability): per-opp × week grid with a **status pill**
  (⏳ in progress / ✓ complete) and KPI bars (images audited, AI-flagged, pass/fail); click a cell →
  inline per-FLW table (flagged sorted first) with **deep links to `/audit/{session_id}/?opportunity_id={opp}`**
  and a run link. Reuse the `image_count`/`ai_no_match` fixes already shipped on the creator.

### Phase 1 acceptance
- Each opp's instance shows only its audits; the org can complete them and mark the run complete.
- The program report shows all 4 opps, each with completion status + KPIs, drilling into per-FLW
  rows that open the actual audit sessions.

## Phase 2 — first-class program-management surface (sketch)

- **Program routes + dashboard:** `/labs/workflow/?program_id=<id>` (and a program home) listing the
  program's template + the per-opp instances + the program report. Fix program-scoped
  `list_definitions` (return the program's definitions when `program_id` is set, no opp).
- **Shared-template management:** edit the program template (tracks, AI, render, per-opp image map)
  and **re-stamp** instances when it changes.
- **Creation orchestration:** one "create this week across all opps" action that fans out the batch
  job to every per-opp instance (the program admin has access to all member opps).
- **Program report home:** the report lives on the program surface, not under one opp.

## Key risks / open questions
- **Instance ↔ template drift:** when the program template changes, per-opp instances must be
  re-stamped. Phase 1 stamps once; Phase 2 needs a re-stamp/sync step.
- **Who fires creation in Phase 1:** until the program surface exists, batches are created per-opp
  (or by an admin with multi-opp access) — acceptable interim.
- **Report source of truth:** report reads *completed* runs for the frozen view but should also show
  *in-progress* runs live (both, like the creator's live/snapshot split).
- **Migration:** the existing multi-opp creator (4683) + report (4685) stay until the per-opp set is
  proven, then are retired.

## Out of scope
- Task creation, non-audit workflows, cross-program reporting.
