---
name: workflow-author
description: Use this skill when iterating on a live workflow in labs — reading JSX, editing render code, pushing it back — via the connect_labs MCP. Triggers on phrases like "pull workflow", "fix the workflow UI", "edit workflow X", "update the render code on workflow N", "clone this workflow", "make this a template". Do NOT use for editing seed template .py files in the repo (use workflow-templates for that).
---

# Authoring Live Workflows

## When to use this skill — and when to reach for the template-sync tool instead

Two iteration loops are supported. Pick the right one for the change at hand:

- **One-off workflow (default):** edit `render_code` / `definition` directly on the workflow via `workflow_update_render_code`, `workflow_patch_render_code`, `workflow_update_definition`. No template file needed. This is the right choice for most edits.
- **Template authoring:** edit the `.py` file in `connect_labs/workflow/templates/`, then call `workflow_sync_from_template_file` against a live preview workflow. The `.py` file is the source of truth — do not fork iteration onto the workflow itself. Commit the `.py` when the design has settled.

A common failure mode is iterating directly on a workflow because "deploys are slow," then forgetting to back-port to the template. The sync tool removes that incentive — the template file iteration loop is just as fast as the workflow one.

Use the `connect_labs` MCP tools to round-trip a workflow between labs and Claude Code without copy-pasting through the browser.

**Prereq:** the user must have logged into labs in a browser at least once (creates their `UserConnectToken`) and have a PAT configured in `~/.claude/mcp.json` (see `docs/MCP_SETUP.md`). If the first MCP call returns `PERMISSION_DENIED`, tell the user to log into labs in a browser and retry.

## Basic iteration loop

1. **Identify the workflow.** Parse the workflow URL or ID from the user's request. A labs URL looks like `https://labs.connect.dimagi.com/labs/workflow/<id>/run/`. You also need the `opportunity_id` — it's in the URL query string or the user's context.

2. **Pull it.** Call `workflow_get(workflow_id, opportunity_id)`. This returns definition (name, description, statuses, config), the full JSX in `render_code`, the `render_code_version` number, and metadata for any linked pipelines. Note the version — you'll need it on push.

3. **Understand the data before editing.** If the request touches pipeline data (charts, tables, computed values), also call `pipeline_get(pipeline_id, opportunity_id)` for each linked source to see the schema. If the user is adding NEW fields from the underlying form, use the local `commcare_hq_mcp` tools (`get_opportunity_apps`, `get_form_json_paths`) to discover exact JSON paths — then update the pipeline FIRST via `pipeline_update_schema` before editing the workflow JSX to reference the new fields.

4. **Edit the JSX.** Rules the server enforces:

   - Must declare `function WorkflowUI(...)` as a function declaration (not arrow, not `const WorkflowUI = ...`)
   - Use `var`. No `const` or `let`.
   - Globals available: `React`, Chart.js, Leaflet, Mapbox GL (`window.mapboxgl`), and the shared map components `window.ConnectMap` + `window.PlanLayers` (draw microplan plan layers via these — see WORKFLOW_REFERENCE.md §4a "Shared map components" — rather than hand-rolling layer paint).
   - Props are `{definition, instance, workers, pipelines, links, actions, onUpdateState, view}`.
   - **Run-shaped templates (`workflow_get` returns `saved_runs.supports_saved_runs: true`):** read run data via `view`, not bare props.
     - `view.workers`, `view.pipelines`, `view.state` resolve to live data while in_progress and to snapshot data once completed — same shape, same render.
     - `view.isCompleted` and `view.asOf` (= `completed_at`) drive read-only banners and "as of" framing.
     - `view.complete({ confirm: "..." })` is the canonical "Mark Run Complete" action — confirms with the user, builds the snapshot atomically, reloads.
     - Disable mutation UIs (`disabled={view.isCompleted}`) for clarity even though the BE returns 409.
   - **Action-shaped templates (`saved_runs.supports_saved_runs: false` or no `saved_runs` key):** read `workers`/`pipelines`/`instance.state` directly. There is no completion flow at the run level.

5. **Sanity check before pushing.** Re-read the JSX. Does it reference any pipeline aliases or field names that don't exist in the schema you fetched in step 3? If so, fix the reference before pushing — server-side validation doesn't catch semantic errors.

6. **Push.** Call `workflow_update_render_code(workflow_id, opportunity_id, component_code, expected_version)`. On success, report the new version number to the user and tell them to refresh the labs tab.

7. **On VERSION_CONFLICT:** someone else (or you, earlier) saved a newer version. Call `workflow_get` again, reapply your edit on top of the new version, retry the push.

8. **On INVALID_JSX:** the server rejected your code with a specific line/column and reason (missing `function WorkflowUI`, or `const`/`let` found). Fix the named problem and retry. Don't paper over — the server catches real issues.

9. **On PERMISSION_DENIED:** the caller's `UserConnectToken` is missing or expired. Tell them to log into labs in a browser, then retry.

## Related flows

### Clone a workflow

User: "Make a copy of workflow X in opp Y" or "Clone workflow 42 into opp 100".

- `workflow_clone(source_workflow_id, source_opportunity_id, target_opportunity_id, new_name="optional")`
- Cloning from a template (a workflow with `is_template=true`) produces a regular workflow — the clone's template flags are always stripped.
- Report the new `new_workflow_id` and invite the user to iterate on it.

### Promote a workflow to a template

User: "Make this reusable" or "Save this as a template".

- Ask the scope if not obvious: `org:<id>`, `program:<id>`, or `global` (admin-only).
- Call `workflow_set_template_flag(workflow_id, opportunity_id, is_template=True, template_scope="org:42")`.
- `global` scope requires admin permissions — if the user lacks them, they'll get `PERMISSION_DENIED` back.

### Create a new workflow from a seed template

User: "Create a new workflow from the performance_review template".

- `workflow_create_from_template(template_key="performance_review", opportunity_id=..., name=optional)`.
- Seed templates live in the repo at `connect_labs/workflow/templates/*.py`. `template_key` is the module name (e.g. `performance_review`, `kmc_longitudinal`).

### Editing a run-shaped template's render code

When `workflow_get` returns `saved_runs.supports_saved_runs: true`:

- Inspect `saved_runs.snapshot_schema` to see what keys render code can rely on after completion. If you're adding new fields the template reads, the snapshot must produce them too — either via `snapshot_inputs` (declarative manifest) or a `build_snapshot` hook (computed). Both are authored at the SEED-template level (`connect_labs/workflow/templates/<key>.py`), not on the live workflow record. If the schema needs to change, that's a `workflow-templates` task, not a `workflow-author` task.
- The render code's contract is `view.workers` / `view.pipelines.<alias>` / `view.state.<key>` — anywhere the existing JSX reads bare `workers` / `pipelines` / `instance.state` for run data, switch it to `view.X`. `definition`, `links`, `actions`, `onUpdateState` are unchanged.
- A "Mark Run Complete" button always calls `view.complete({ confirm: "<copy>" })`. Don't POST to `/complete/` directly — the helper handles confirmation and the page reload.
- Reference: `connect_labs/workflow/templates/performance_review.py`. See also `connect_labs/workflow/WORKFLOW_REFERENCE.md` § 9 "Saved-runs templates".

### Update definition metadata

User: "Rename workflow X to Y", "Add a status", "Change the config".

- `workflow_update_definition(workflow_id, opportunity_id, patch={...}, expected_version=V)`.
- Patch keys allowed: `name`, `description`, `statuses`, `config`. Any other key is rejected.
- `statuses` replaces wholesale; `config` shallow-merges.
- Same version-conflict discipline as render_code: on VERSION_CONFLICT, re-read and retry.

## Anti-patterns

- **Do NOT** edit the repo's seed template `.py` files to change a LIVE workflow. That path requires a redeploy and creates two sources of truth. Use `workflow_update_render_code` on the live instance.
- **Do NOT** hand-craft JSX that references pipeline aliases the user hasn't provided. If unsure, call `workflow_get` to see which aliases exist.
- **Do NOT** retry on `VERSION_CONFLICT` without re-reading — you'll stomp someone's edit.
- **Do NOT** clone a template then try to `workflow_update_definition` it with `is_template=True` — use `workflow_set_template_flag` for that, and only on workflows you want to mark as templates.
