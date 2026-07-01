# Workflow Engine Documentation & Tooling DRY Refactor

**Date:** 2026-03-09
**Status:** Approved

## Problem

Workflow authoring knowledge is fragmented across 5+ locations (skill, agent instructions, README, base.py, individual templates). Building workflows from external documents (indicator specs, monitoring frameworks) requires undocumented knowledge about MCP field discovery. The in-product AI agent and Claude Code maintain duplicate reference material that drifts.

## Architecture

Single source of truth: `connect_labs/workflow/WORKFLOW_REFERENCE.md`

```
WORKFLOW_REFERENCE.md                      <- canonical reference (all detail lives here)
    ^ read by
workflow_agent.py                          <- in-product agent loads at module init
    ^ linked from
.claude/skills/workflow-templates/SKILL.md <- process guide + link to reference
    ^ linked from
CLAUDE.md                                  <- brief summary + link
```

## Deliverables

### 1. `connect_labs/workflow/WORKFLOW_REFERENCE.md`

Comprehensive authoring guide with these sections:

1. **Template Anatomy** — DEFINITION, PIPELINE_SCHEMAS, RENDER_CODE, TEMPLATE export
2. **Pipeline Schema Deep-Dive** — fields, paths, aggregations, transforms, terminal_stage, linking_field, multi-path fallback
3. **Discovering Field Paths** — using MCP `get_form_json_paths` or manual CommCare inspection
4. **Render Code Contract** — props interface, constraints (`var` not `const/let`, no imports), CDN libs
5. **Actions API** — complete reference with signatures
6. **Common UI Patterns** — KPI cards, tables, charts, maps, status badges, SSE streaming
7. **Building from External Specs** — indicator document -> pipeline fields -> render code

### 2. Improved `workflow-templates` Skill

Restructure as two-phase process:
- **Phase 1 (conditional):** When building from external spec — analyze document, use MCP tools to discover field paths, map indicators to pipeline fields
- **Phase 2:** Build the template — references WORKFLOW_REFERENCE.md instead of duplicating details

### 3. CLAUDE.md Addition

Add `## Workflow Engine` section (~8 lines) with mental model and link to reference.

### 4. Agent Refactor

`workflow_agent.py` loads WORKFLOW_REFERENCE.md at module init, replacing hardcoded duplicate material in WORKFLOW_AGENT_INSTRUCTIONS.

## Design Decisions

- **One skill, not two** — `workflow-from-spec` merged into `workflow-templates` as a conditional phase. Avoids maintaining duplicate schema/render/action documentation.
- **Reference file in workflow dir, not docs/** — it's a developer/AI reference, not user documentation. Lives next to the code it describes.
- **Agent loads file at import time** — simple, no runtime overhead, file changes picked up on server restart. Same pattern as templates auto-discovery.
