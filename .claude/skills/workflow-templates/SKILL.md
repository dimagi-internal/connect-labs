---
name: workflow-templates
description: Create and modify workflow templates for CommCare Connect. Use when the user wants to create a new workflow template, add a workflow type, modify existing workflow UI code, or asks about workflow render code patterns.
---

# Building Workflow Templates

Workflow templates define reusable workflow types with data pipelines and custom React UIs.

**Reference:** Read `commcare_connect/workflow/WORKFLOW_REFERENCE.md` for the complete authoring guide — template structure, pipeline schemas, render code contract, actions API, and common patterns.

## Phase 1: Understand the Data (when building from external specs)

Skip this phase if you already know the CommCare form paths and indicators.

1. **Analyze the source document** — identify indicators, data points, groupings, and visualization needs
2. **Discover field paths** using MCP tools:
   - `get_opportunity_apps(opportunity_id)` → domain + app IDs
   - `get_app_structure(domain, app_id)` → modules, forms, xmlns
   - `get_form_json_paths(xmlns, domain, app_id)` → exact JSON paths for each question
3. **Map each indicator** to a pipeline field with the right aggregation and transform
4. **Decide terminal_stage**: `visit_level` for per-visit detail, `aggregated` for per-worker summaries

## Phase 2: Build the Template

1. **Create the file** in `commcare_connect/workflow/templates/` (e.g., `my_template.py`)
2. **Write PIPELINE_SCHEMAS** with fields mapped from Phase 1 (see WORKFLOW_REFERENCE.md > Pipeline Schema Deep-Dive)
3. **Write DEFINITION** with statuses and config (see WORKFLOW_REFERENCE.md > Template Anatomy)
4. **Write RENDER_CODE** as JSX string (see WORKFLOW_REFERENCE.md > Render Code Contract)
   - Must define `function WorkflowUI({...})` — not const/let
   - Use `var` for all declarations
   - Only `React` global available (plus Chart.js and Leaflet from CDN)
5. **Export TEMPLATE dict** with key, name, description, icon, color, definition, render_code, pipeline_schema(s)
6. **Test** with `?edit=true` URL parameter — verify pipeline data is non-empty, check browser console for Babel errors

## Key Files

- **Templates directory**: `commcare_connect/workflow/templates/` — add new `.py` files here
- **Registry**: `commcare_connect/workflow/templates/__init__.py` — auto-discovers templates
- **Reference**: `commcare_connect/workflow/WORKFLOW_REFERENCE.md` — full authoring guide
- **Types**: `components/workflow/types.ts` — TypeScript interface definitions
- **Examples**: `performance_review.py` (simple), `kmc_longitudinal.py` (complex multi-pipeline)

## Checklist

Before considering a template complete:

- [ ] Template key is unique
- [ ] All field paths verified via MCP or CommCare HQ inspection
- [ ] RENDER_CODE uses `var` (not `const`/`let`) and function is named `WorkflowUI`
- [ ] Tested with `?edit=true` — data loads, no console errors
- [ ] TEMPLATE dict has all required fields (key, name, description, icon, color, definition, render_code)
