# Labs MCP + Workflow Author Skills — Design

**Status:** Draft for review
**Date:** 2026-04-19
**Author:** jjackson (via brainstorming with Claude)

## Motivation

Workflow authors in labs are hitting a friction cliff. The current iteration loop is: edit JSX in the labs web UI, cut-and-paste it into Claude Code, iterate, paste it back, save. This works for one author but doesn't scale, loses history, and pushes people away from live-instance iteration toward editing seed template `.py` files in the repo as a workaround — because the in-app AI chat is meaningfully weaker than Claude Code.

At the same time, the tooling story has drifted. `tools/commcare_mcp/` started as a CommCare HQ schema-lookup server but has accumulated labs-product tools (solicitations, reviews, funds, sample IDs) and an unrelated Google Sheets integration. The single server serves three audiences with different auth stories and different product domains.

This design fixes both at once: split the MCP surface into product-scoped servers, host the labs-product server as a remote MCP endpoint inside the labs Django app, and ship three skills that teach the model how to iterate on live workflows and pipelines without the copy-paste dance.

## Goals

- Workflow authors can iterate on a live labs workflow end-to-end from Claude Code, with no copy-paste through the web UI.
- Pipeline authors can edit a pipeline schema and preview sample rows from real opportunity data in the same loop.
- The MCP surface is split cleanly by product: one server per product concern, no cross-concern drift.
- The labs MCP works on every Claude Code surface that supports MCP: CLI, desktop, IDE extensions, **and web** (claude.ai/code).
- Server-side validation prevents broken render code or invalid pipeline schemas from reaching the DB.
- Version-based concurrency prevents silent overwrites when the web UI and MCP touch the same workflow.
- Templates stop being a separate concept gated by git/redeploy and become a flag on workflows that any author can promote.

## Non-goals

- Re-architecting the workflow execution engine, pipeline runner, or render-code sandbox.
- Replacing the web UI. The labs web UI remains the primary viewing surface; Claude Code becomes the primary editing surface.
- Adding a hosted MCP for CommCare HQ. The HQ server stays local stdio for now — no natural hosting spot, small user audience.
- Multi-tenant / external-customer MCP access. Labs users are internal Dimagi.
- Migrating existing seed `.py` templates into DB-flagged workflows. That's a follow-on; Python seeds keep working unchanged.

## Architecture

Three MCP servers, each with a clear product scope:

| Server | Transport | Host | Purpose |
|---|---|---|---|
| `commcare_hq_mcp` | stdio (local) | `tools/commcare_hq_mcp/` | CommCare HQ app structure (form paths, xmlns, modules). Used when authoring pipeline schemas. |
| `connect_labs` | HTTP (remote) | `connect_labs/mcp/` Django app, mounted at `https://labs.connect.dimagi.com/mcp/` | All labs-product operations: workflows, pipelines, solicitations, reviews, funds, sample IDs. |
| *(Google tools)* | — | *Deleted* | `tools/commcare_mcp/google_tools.py` and `google_auth.py` removed. The `ace-gdrive` plugin is strictly richer. |

### Why remote for labs

- Works on **every** Claude Code surface, including web. Local stdio cannot run in the web agent since it can't spawn subprocesses on the user's machine.
- Zero local install. Users paste one URL into their `.claude/mcp.json` and authenticate once via OAuth.
- The server co-locates with the data. `pipeline_preview` runs the existing pipeline executor in-process — no two-endpoint architecture needed.
- Deploys atomically with labs. New tools show up as soon as labs deploys them.
- Auth reuses the Connect OAuth labs already speaks.

### Why local stdio for HQ

- No natural server to host it on. The HQ MCP is a thin proxy over CommCare HQ's app-definition API.
- User audience is small (workflow authors at schema-discovery time) and already comfortable with local installs.
- Can revisit later without blocking this work.

## Tool catalog — `connect_labs` remote MCP

### Workflow tools (new)

| Tool | Purpose |
|---|---|
| `workflow_list(scope)` | List by `opportunity_id` / `program_id` / `organization_id` / `mine`. Returns `[{id, name, description, template_type, is_template, template_scope, updated_at, pipeline_source_count}]`. |
| `workflow_get(workflow_id)` | One call returns everything to iterate on: full definition (name, description, statuses, config, template_type, is_template, template_scope), latest `render_code` with version number, and `pipeline_sources: [{pipeline_id, alias, name, schema_summary}]`. |
| `workflow_update_render_code(workflow_id, component_code, expected_version)` | Server-side Babel parse + pattern check (must define `function WorkflowUI`, flag `const`/`let`). Rejects invalid code with a specific error. Returns the new version number on success. `expected_version` for concurrency. |
| `workflow_update_definition(workflow_id, patch, expected_version)` | Shallow-merge patch over `{name, description, statuses, config}`. `statuses` replaces wholesale. Rejects patches that drop required fields. |
| `workflow_revert_render_code(workflow_id, to_version)` | Restores a prior render_code version. Creates a new version (no history rewrite). |
| `workflow_create_from_template(template_key, opportunity_id, name=None)` | Thin wrapper around existing `create_workflow_from_template`. Python-file seed templates only. |
| `workflow_clone(source_workflow_id, target_opportunity_id, new_name=None)` | Create a new workflow from any existing workflow the caller can read. This is the tool used for DB-backed templates; templates are just workflows with `is_template=true`. |
| `workflow_set_template_flag(workflow_id, is_template, template_scope)` | Mark a workflow as a template. `template_scope` is one of `"global"`, `"org:<id>"`, `"program:<id>"`. Owner can set org/program scopes; only labs admins can set `"global"`. |

### Pipeline tools (new)

| Tool | Purpose |
|---|---|
| `pipeline_list(scope)` | Same scoping shape as workflows. |
| `pipeline_get(pipeline_id)` | Name, description, full schema, source (opp_id, form xmlns if bound). |
| `pipeline_update_schema(pipeline_id, schema, expected_version, name=None, description=None)` | Server-side schema validation (shape, aggregation/transform allow-lists). Rejects unknowns. |
| `pipeline_preview(pipeline_id, opportunity_id, sample_size=50, schema_override=None)` | Run the pipeline against real opportunity data, return sample rows. `schema_override` previews unsaved changes without persisting. The iteration hot path. |
| `pipeline_sql(pipeline_id, opportunity_id, schema_override=None)` | Return generated SQL for debugging. |

### Migrated tools (file move only, no signature changes)

`solicitation_*`, `review_*`, `fund_*`, `sample_ids_*` — move from `tools/commcare_mcp/` to `connect_labs/mcp/tools/`. Existing tests move with them and must pass unchanged.

### Error shape (uniform across all tools)

```json
{"error": {"code": "INVALID_JSX", "message": "Babel parse failed at line 42, col 3: Unexpected token", "details": {"line": 42, "col": 3}}}
```

Codes: `INVALID_JSX`, `INVALID_SCHEMA`, `NOT_FOUND`, `PERMISSION_DENIED`, `VERSION_CONFLICT`, `RATE_LIMITED`, `UPSTREAM_ERROR`. Every tool's docstring tells the model how to react (e.g. on `VERSION_CONFLICT`, re-read and retry; on `INVALID_JSX`, fix the named parse error and retry; on `PERMISSION_DENIED`, stop and tell the user).

## Skills

Three skills, each a short procedure on top of the MCP tools.

### `workflow-author` (new, primary)

**Triggers:** "iterate on workflow X," "fix the render code on workflow 1234," "the workflow UI is broken," "clone this workflow," "promote this to a template."

**Procedure:**

1. Parse the workflow URL or ID from the user's message. Call `workflow_get(id)` to load definition + render_code + linked pipeline metadata in one shot.
2. If the request touches pipeline data, also call `pipeline_get(pipeline_id)` for each linked source.
3. Edit the JSX in a scratch file or in-memory. Always: `function WorkflowUI(...)` declaration, `var` only, only `React` plus allowed CDN globals (Chart.js, Leaflet).
4. Before saving: re-read the JSX and sanity-check that it still references props that exist in the pipeline schema.
5. Call `workflow_update_render_code(id, new_jsx, expected_version)`. On `INVALID_JSX`, fix the named error and retry; do not swallow the failure. On `VERSION_CONFLICT`, re-read and redo the edit.
6. Confirm live: report the new version number and tell the user to refresh the labs tab.
7. **Clone flow:** on "make a new workflow from this," call `workflow_clone(source_id, target_opp_id)` then iterate on the new one.
8. **Template flow:** on "make this reusable" or "promote this," call `workflow_set_template_flag(id, true, scope)`. Ask the user the scope before setting global.

### `pipeline-author` (new)

**Triggers:** "add a field to the pipeline," "this aggregation is wrong," "preview pipeline X."

**Procedure:**

1. `pipeline_get(id)` for current schema.
2. If the user is adding fields from a new form question, use the **local `commcare_hq_mcp`** tools (`get_opportunity_apps`, `get_app_structure`, `get_form_json_paths`) to discover exact JSON paths. This is the one place the two servers cross; the skill names this explicitly.
3. Propose a schema diff in chat.
4. Call `pipeline_preview(id, opp_id, schema_override=new_schema)`. Show the user 5–10 sample rows. Tight loop: read rows, refine, preview again.
5. Once the preview looks right, `pipeline_update_schema(id, new_schema, expected_version)`.
6. If a workflow depends on this pipeline, suggest pivoting to `workflow-author` to verify the UI still renders correctly.

### `workflow-templates` (existing, re-scoped)

Narrowed to **seed template authoring only** — the repo `.py` files in `connect_labs/workflow/templates/` that ship with labs. `SKILL.md` updated to:

- Direct the reader to `workflow-author` if they're editing a live instance.
- Remove any overlap with live-instance editing procedures.
- Keep the MCP-based form-path discovery section (still relevant for `PIPELINE_SCHEMAS`).

Over time (outside this design's scope) this skill can shrink or retire as Python seeds migrate into DB templates.

## Templates as a flag on workflows

The current distinction between "template" (Python file in repo, requires redeploy) and "workflow instance" (LabsRecord in DB, editable live) was a workaround for the weak in-app AI. With `workflow-author` iterating on live instances well, the distinction stops making sense as a product concept.

### Data model

Workflow `LabsRecord.data` gains two fields:

- `is_template: bool` (default `false`).
- `template_scope: str` — one of `"global"`, `"org:<id>"`, `"program:<id>"`. Ignored when `is_template=false`.

No DB migration required; LabsRecord is JSON-backed.

### Permissions

- Owner can toggle `is_template` and set `template_scope` to their own org or any program they're a member of.
- `template_scope="global"` requires labs admin role. Prevents accidental "my hacky prototype is now a global template."
- Reading a template respects the scope: global templates are visible to everyone; org/program templates only to members of that org/program.

### Cloning

`workflow_clone(source_id, target_opp_id, new_name=None)` creates a new workflow from the source's definition + current render_code + pipeline schemas. The new workflow has its own ID, its own version history, and `is_template=false` by default.

### Coexistence with Python seeds

Python-file seed templates keep working unchanged. `workflow_create_from_template(template_key=...)` remains the entry point for Python seeds only. DB-backed templates use `workflow_clone(source_workflow_id=...)`. Two distinct tools, one for each mechanism, until the Python seeds are migrated out in a follow-on.

## Safety, validation, errors

### Authentication

MCP OAuth 2.1 flow bridged to Connect OAuth.

- First connection: server returns `WWW-Authenticate` pointing to `/mcp/authorize`.
- Client opens the URL in the user's browser (CLI/desktop/IDE: local browser; web: Anthropic's app handles the redirect).
- Labs bounces the user to Connect OAuth, captures identity, issues an MCP access token scoped to that user.
- Token lifetime matches Connect OAuth (~1 hour) with refresh-token support. Expired token → 401 with a fresh authorize URL.

**Contingency:** if OAuth 2.1 integration proves harder than expected, launch with labs-generated personal access tokens (users generate in the labs UI, paste into Claude Code config once). Explicitly captured as a contingency in the implementation plan, not a long-term goal.

### Server-side validation on writes

Mandatory, not bypassable:

- `workflow_update_render_code`: Babel-parse the JSX; enforce `function WorkflowUI(...)`; flag `const`/`let`. Reject with a specific error naming the failure. No write reaches the DB on failure.
- `workflow_update_definition`: JSON-schema validate the patch shape; reject missing required fields and unknown top-level keys.
- `pipeline_update_schema`: validate aggregation and transform names against an allow-list; reject unknowns.
- `workflow_set_template_flag`: validate scope string and permission.

### Version-based optimistic concurrency

- `workflow_get` returns `render_code_version` and `definition_version` as separate numbers (they advance independently).
- `workflow_update_render_code` takes `expected_version` checked against `render_code_version`. `workflow_update_definition` takes `expected_version` checked against `definition_version`. `pipeline_update_schema` takes `expected_version` checked against the pipeline's own version counter.
- Server rejects writes with `VERSION_CONFLICT` when a newer version exists on the server than `expected_version`.
- The model is taught via tool docstrings to re-read and retry on conflict, not force.

### Blast-radius mitigation

- Every successful update is a new version; old versions retained.
- `workflow_revert_render_code(id, to_version)` is an explicit recovery tool the model can call on user request.
- No auto-backup files; versioning is the recovery surface.

### Audit log

Every MCP tool call is logged: `user_id, tool_name, args (writes: full; reads: type+scope), success/failure, version_before, version_after`. Visible to labs admins. Lives in the labs DB as a new table (`mcp_audit_log`) or as LabsRecords (type `mcp_audit`).

### Rate limits

- Per-user write rate cap (default 30 writes/min, combined across all update/clone/revert/set-flag tools). Prevents runaway-model damage.
- Reads effectively uncapped.
- Limits in Django settings, not hardcoded. `RATE_LIMITED` error returned when exceeded.

## Deferred to implementation planning

These are implementation-level decisions that don't shape the design but need a call during `writing-plans`:

1. **`pipeline_preview` / `pipeline_sql` endpoint unification.** The existing `/workflow/api/pipeline/<id>/preview/` and `/sql/` endpoints are session-authenticated for the web UI. Either extend them to accept OAuth Bearer tokens (reuse, small change) or add dedicated `/mcp/`-internal handlers (clearer separation). Pick during implementation. Invisible to users either way.
2. **Audit log storage.** Django table vs LabsRecord type. Lean toward a dedicated Django table for query efficiency, but LabsRecord keeps the all-JSON-backed pattern consistent.
3. **OAuth 2.1 library choice.** Django has several options; needs a quick evaluation for MCP compatibility.

## Migration plan

Ordered for independently landable PRs.

1. **Rename `tools/commcare_mcp/` → `tools/commcare_hq_mcp/` and prune.** Delete `google_tools.py`, `google_auth.py`. Move labs-product tool files (solicitation, review, fund, sample_ids) into a `_pending_migration/` subdir so they're still served by the old server name while the new home is being built. Update `.claude/mcp.json`.
2. **Create `connect_labs/mcp/` Django app scaffolding.** INSTALLED_APPS entry, URL routes at `/mcp/`, Streamable HTTP transport handler, stub tools catalog returning an empty list. Ship to staging. Verify Claude Code can connect and `tools/list` round-trips.
3. **Land OAuth 2.1 auth** (or the PAT fallback — commit to one at the start of this step).
4. **Port workflow + pipeline tools into the new server** with server-side validation and audit logging.
5. **Move `_pending_migration/` tools into `connect_labs/mcp/tools/`.** Delete the old stdio versions. Publish migration doc for `.claude/mcp.json` updates.
6. **Ship templates-as-flag.** Add `is_template` and `template_scope` to workflow LabsRecord data. Add `workflow_clone` and `workflow_set_template_flag` tools. Update `workflow-author` skill.
7. **Update docs.** Replace CLAUDE.md's "CommCare MCP Server" section with "MCP Servers" covering both. Add `docs/MCP_SETUP.md` for first-time setup. Update skills' `SKILL.md`.

## Testing

### MCP transport (new surface, needs basic coverage)

- Client can connect; `tools/list` returns expected catalog with correct schemas.
- `tools/call` end-to-end on a sample tool (happy path).
- OAuth flow: happy path, expired token, missing token, revoked token.
- Cross-surface smoke: CLI, desktop, web each connect and invoke one tool successfully.

### Workflow tools

- `workflow_list` / `workflow_get` with each scope (opp_id, program_id, org_id, mine).
- `workflow_update_render_code`: valid JSX → new version; Babel-broken JSX → `INVALID_JSX` with line/col; missing `WorkflowUI` → `INVALID_JSX`; `const`/`let` flagged per policy.
- `workflow_update_definition`: valid patch; invalid patch; missing required fields.
- `workflow_clone`: happy path; scope rules (cannot clone from a workflow the user cannot read); name collision handling.
- `workflow_set_template_flag`: owner flags own OK; non-admin global promotion → `PERMISSION_DENIED`.
- `workflow_revert_render_code`: restores chosen version as a new version.
- Version conflict: concurrent writes → `VERSION_CONFLICT`; model retries on re-read.

### Pipeline tools

- `pipeline_list` / `pipeline_get`.
- `pipeline_update_schema`: valid; unknown aggregation → `INVALID_SCHEMA`.
- `pipeline_preview` against a real opp; with `schema_override` (unsaved preview).
- `pipeline_sql` returns a string.

### Migrated tools (signature parity contract)

Existing tests for solicitation, review, fund, sample_ids ported to new server home. Must pass unchanged.

### Safety

- Rate limit: write rate cap kicks in after threshold; reads unaffected.
- Audit log: every write produces a log row with user/tool/args/version_before/after.
- Permission isolation: user A cannot read user B's non-public workflows; non-admin cannot set `template_scope="global"`.

## Open product question (resolved)

**Who can flag a workflow as a template?** — Owner for org/program scopes; labs admin only for global. Captured in the Permissions section above.

## Success criteria

- A workflow author can pull a live labs workflow, edit the JSX in Claude Code, push it back, and see the change in the labs UI on refresh — without touching the browser's code editor.
- A pipeline author can iterate schema → preview → refine → save without leaving Claude Code.
- The same skill works identically in CLI, desktop, and web surfaces.
- An invalid JSX push is rejected by the server with a specific error; the model self-corrects on retry.
- Two concurrent edits produce a `VERSION_CONFLICT` rather than a silent overwrite.
- After migration: `tools/commcare_mcp/` no longer exists; `tools/commcare_hq_mcp/` serves HQ tools only; `connect_labs/mcp/` serves labs tools only.
- `workflow-templates` skill is narrowed to seed-file authoring; `workflow-author` is the primary skill mentioned in onboarding.
