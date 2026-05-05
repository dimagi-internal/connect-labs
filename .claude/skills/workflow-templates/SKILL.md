---
name: workflow-templates
description: Use this skill ONLY when authoring a new SEED template — a Python file in commcare_connect/workflow/templates/ that ships with labs and scaffolds new workflows via workflow_create_from_template. For editing a live workflow instance in labs (the common case), use workflow-author instead. For editing a live pipeline schema, use pipeline-author.
---

# Authoring Seed Workflow Templates

A seed workflow template is a Python file in `commcare_connect/workflow/templates/` that ships with labs. Users instantiate one as a new workflow via the MCP tool `workflow_create_from_template(template_key=...)`. Editing a seed template is a deploy-gated change to the codebase — not a change to any live workflow.

**For editing a LIVE workflow or pipeline in labs, use `workflow-author` or `pipeline-author`, NOT this skill.** The MCP tools are strictly better for that case (no redeploy, no git round-trip, server-side validation).

## When to use this skill

- Shipping a new starter template that every labs user should be able to clone.
- Updating an existing seed template's JSX or pipeline schema for use in future opportunities.
- Fixing a bug in a seed template that affects the initial scaffold.

## When NOT to use this skill

- Editing a workflow that's already live on an opportunity. Use `workflow-author`.
- Fixing a rendering bug in one user's deployed workflow. Use `workflow-author`.
- Adding a new field to a pipeline already in use. Use `pipeline-author`.

## File structure

Each seed template is a single Python file exporting these module-level names:

- `DEFINITION` — dict with `name`, `description`, `statuses` (list), `config` (dict). Shape is validated by the MCP tool when cloning.
- `RENDER_CODE` — a string containing the JSX. Same rules as live workflows: must declare `function WorkflowUI(...)`, must use `var` (not `const`/`let`), only `React` + Chart.js + Leaflet globals are available.
- `PIPELINE_SCHEMAS` (optional) — a list of dicts, one per pipeline this template creates alongside the workflow. Each schema has `fields`, `aggregations`, `transforms`, `groupings`.
- `TEMPLATE` — the registry export. Includes `key`, `name`, `description`, `icon`, `color`, `definition`, `render_code`, `pipeline_schema(s)`, plus optional flags described below.

## Run-shaped vs action-shaped: choose one

Decide upfront whether your template is **run-shaped** (a periodic review with a "moment of completion" — the user marks a run done and reopens it later as a frozen artifact) or **action-shaped** (an orchestration tool whose value lives in artifacts persisted in their own models — audit sessions, tasks, OCS conversations).

### Run-shaped (`supports_saved_runs: True`)

Add to `TEMPLATE`:

- `"supports_saved_runs": True` — opts in to the in_progress|completed lifecycle. Render code receives `view`; the runner shows a completion verb.
- One of (or both):
  - `"snapshot_inputs"` — declarative manifest of what the framework's default hook captures: `{"pipelines": [aliases], "workers": bool, "state_keys": [keys]}`. Anything not listed is not captured. Use this when the snapshot is a verbatim subset of the inputs.
  - Module-level `def build_snapshot(*, pipelines, state, opportunity_id, workers, opportunity_ids, **_) -> dict:` — a custom hook that shapes the snapshot. Use when the snapshot is computed (KPI summaries, rolled-up metrics) rather than verbatim. The hook overrides `snapshot_inputs` if both are present.
- `"snapshot_schema"` — documents the keys render code reads off `instance.snapshot`. Used by the framework for completion-confirm copy and as authoring documentation.

The render code reads `view.workers`, `view.pipelines.<alias>`, `view.state.<key>`. The shape exposed by `view` when completed must match what the snapshot writes — that's the authoring contract.

Reference: `commcare_connect/workflow/templates/performance_review.py`.

### Action-shaped (no flag)

Omit `supports_saved_runs` (or set it `False`). Don't include `snapshot_*` keys. Render code reads `workers`/`pipelines`/`instance.state` directly. There is no "Mark Run Complete" button.

References: `audit_with_ai_review`, `bulk_image_audit`, `ocs_outreach`, `kmc_*` (continuous tracking).

See `commcare_connect/workflow/WORKFLOW_REFERENCE.md` § 9 "Saved-runs templates" for the full contract.

## Discovering form JSON paths

When building `PIPELINE_SCHEMAS` fields, you need exact JSON paths like `form.anthropometric.child_weight_visit`. Use the `commcare_hq_mcp` local tools:

1. `get_opportunity_apps(opportunity_id)` → returns apps for an opportunity
2. `get_app_structure(domain, app_id)` → lists forms
3. `get_form_json_paths(xmlns, domain, app_id)` → maps questions to JSON paths

Pick an opportunity that uses the real app you're targeting and work from there.

## Deploy

Seed templates are picked up automatically on deploy — no registration step. Place the file in `commcare_connect/workflow/templates/` with a good module name (it becomes the `template_key` users pass to `workflow_create_from_template`).

## After the change lands

Test by cloning into a throwaway opportunity:

> "Create a new workflow from the my_new_template template in opp 999"

Claude will call `workflow_create_from_template` and you can verify the result lives in labs. If the template needs changes, edit the Python file and redeploy — seed templates are a deploy-gated surface.
