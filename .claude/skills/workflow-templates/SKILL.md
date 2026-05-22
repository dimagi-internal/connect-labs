---
name: workflow-templates
description: Use this skill ONLY when authoring a new SEED template — a Python file in commcare_connect/workflow/templates/ that ships with labs and scaffolds new workflows via workflow_create_from_template. For editing a live workflow instance in labs (the common case), use workflow-author instead. For editing a live pipeline schema, use pipeline-author.
---

# Authoring Seed Workflow Templates

## Check write access first — required before any other step

This skill writes Python files to the repo. **Before doing anything else**, verify
that file writes are available in this session:

1. Attempt a trivial `Write` or `Edit` call (e.g., append a blank line to any file).
2. If the tool is **blocked or denied**, stop immediately and tell the user exactly this:

> **workflow-templates cannot run in this session.**
> This skill requires `Write`/`Edit` access to modify Python files in
> `commcare_connect/workflow/templates/`, but those tools are denied
> (you are likely in a safe-mode session started with `inv safe-claude`).
>
> To author seed templates, open a **regular Claude Code session** from
> the connect-labs directory instead:
>
> ```
> cd ~/your/connect-labs
> source .venv/bin/activate
> claude
> ```
>
> Then re-invoke this skill.

Do **not** proceed with any file reads, MCP calls, or partial work after this
check fails — report the incompatibility and stop.

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
- `"snapshot_inputs"` — declarative manifest of what the framework's default hook captures: `{"pipelines": [aliases], "workers": bool, "state_keys": [keys]}`. Anything not listed is not captured. **Use this for almost every template** — render code recomputes derived values (summary cards, sorts, filters) at render time from the captured inputs.
- `"snapshot_schema"` — documents the keys render code reads off `instance.snapshot`. Used by the framework for completion-confirm copy and as authoring documentation.

The render code reads `view.workers`, `view.pipelines.<alias>`, `view.state.<key>`. The manifest's shape mirrors what `view.X` exposes while in_progress, so render code is identical in both modes — that's the authoring contract.

**Escape hatch:** Module-level `def build_snapshot(*, pipelines, state, opportunity_id, workers, opportunity_ids, **_) -> dict:` overrides the manifest entirely. Reach for it only when:

1. Pipelines are huge and you want to capture aggregates instead of raw rows (compactness).
2. You need server-side context the FE doesn't have (DB lookup, server-only timestamp, multi-opp roll-up).
3. The captured shape needs to differ structurally from the inputs.

If none of those apply, use `snapshot_inputs` — it's strictly simpler.

Reference: `commcare_connect/workflow/templates/performance_review.py` (manifest path).

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

## Iterating on a new template — use the sync tool, not deploys

While iterating on a new template, do not redeploy labs between edits. Instead:

1. Create the `.py` (and any `_render.js` sidecar) under `commcare_connect/workflow/templates/`.
2. Spin up a preview workflow via `workflow_create_from_template` (manually-registered templates may need a one-time deploy first; once registered, additional iteration is deploy-free).
3. Iterate: edit the template file, call `workflow_sync_from_template_file(workflow_id, opportunity_id, template_source=<py contents>, sidecar_files={"foo_render.js": <js contents>}, expected_render_code_version=N, expected_definition_version=M)`, reload the labs tab.
4. Use `dry_run=true` to validate + diff without writing when you want a sanity check before pushing.
5. Commit the `.py` (and sidecar) once the design has settled.

If the tool returns `PARTIAL_SYNC`, call `workflow_get` to see what landed and what didn't, then fix the failing piece (usually a pipeline schema) and re-run. The definition and render_code writes are durable across the failure.

### Parser grammar — what the sync tool can and can't read

`workflow_sync_from_template_file` parses the `.py` source via a constrained AST walker — no `exec`. It supports module-level assignments that are: string/number/bool literals, dict/list/tuple/set literals, references to other module-level names, negative numeric literals, and the `(Path(__file__).parent / "X.js").read_text(...)` sidecar idiom. Anything else (function calls in `DEFINITION`/`PIPELINE_SCHEMAS`, list comprehensions, `*spread` unpacking, subscript access, `dict.get(...)`) makes the tool reject with `INVALID_TEMPLATE`.

If you want a template to be sync'd, keep its module-level definitions in that literal-with-names form. If you need helper functions or comprehensions, run them inside a module-level loop that builds plain lists/dicts and assign those plain values to `DEFINITION`/`PIPELINE_SCHEMAS` instead.

Templates known to be sync-incompatible today (use a redeploy instead, or refactor the offending module-level expression to literal form): `kmc_longitudinal`, `kmc_project_metrics`, `llo_weekly_review`, `mbw_monitoring_v3`, `program_admin_audit`, `sam_followup`. The parametrized test in `commcare_connect/mcp/tests/test_template_parser.py::test_parser_handles_every_shipped_template` is the source of truth for this list.
