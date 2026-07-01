# workflow_sync_from_template_file — design

**Date:** 2026-05-21
**Status:** Draft, pre-implementation

## Problem

Workflow templates are checked into git at `connect_labs/workflow/templates/*.py`. Workflow *instances* (created from those templates via `workflow_create_from_template`) live in production Connect as labs records and are edited via MCP tools that take effect immediately, no deploy required.

That asymmetry creates a temptation: when iterating, edit the live workflow's `render_code`/`definition` directly because it's fast — even when the change really belongs in the template. The template file then drifts out of sync with the workflow, and the version-controlled source of truth quietly loses authority. This is the failure mode that bit a teammate recently (Ali / mbw_auditing_v4): ~15 commits of authoring iteration on the template file, but the actual current state lived in uncommitted edits and in the live workflow.

We want a way to push a template `.py`'s contents directly to a live preview workflow with no deploy step, so iterating on the template file is as fast as iterating on the workflow itself.

## Goal

Add one MCP tool: `workflow_sync_from_template_file`. Given a workflow ID and the contents of a template `.py` file (plus any `_render.js` sidecar), parse the template's `RENDER_CODE`, `DEFINITION`, and `PIPELINE_SCHEMAS`, then apply them to the named live workflow.

Outcome: the iteration loop for template authoring becomes `edit .py in git → call tool → see in browser → repeat`, with no deploy and no drift between the template file and the workflow.

## Non-Goals

- **Not a forced workflow.** Iterating directly on a one-off workflow remains fully supported. The new tool only matters for the "I want this change to land in the template file" case.
- **No workflow creation.** `workflow_create_from_template` already covers create. The new tool only updates existing workflows.
- **No reparenting.** The tool does not change the workflow's `template_type` field. If you want to switch a workflow to a different template, that's a different operation.
- **No git/sha tracking.** A `last_synced_from` provenance field can be added later if drift between tool calls becomes a real problem. Out of scope for v1.
- **No multi-workflow fan-out.** One tool call updates one workflow. Syncing the same template to three workflows is three calls.
- **No DB-transactional atomicity across the three sub-records.** `render_code`, `definition`, and pipeline schemas are stored as independent labs records with independent version counters. Writes are ordered and partial failures are surfaced clearly, but they are not wrapped in a single transaction.

## Tool signature

```python
@register(
    name="workflow_sync_from_template_file",
    is_write=True,
    input_schema={
        "type": "object",
        "properties": {
            "workflow_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "template_source": {
                "type": "string",
                "description": "Full contents of the template .py file."
            },
            "sidecar_files": {
                "type": "object",
                "additionalProperties": {"type": "string"},
                "description": (
                    "Optional map of sidecar filename (basename only, e.g. "
                    "'mbw_auditing_v4_render.js') → file contents. "
                    "Required if template_source loads a sidecar via "
                    "`Path(__file__).parent / \"<name>\".read_text(...)`."
                ),
            },
            "expected_render_code_version": {"type": "integer"},
            "expected_definition_version": {"type": "integer"},
            "dry_run": {"type": "boolean"},
        },
        "required": [
            "workflow_id",
            "opportunity_id",
            "template_source",
            "expected_render_code_version",
            "expected_definition_version",
        ],
        "additionalProperties": False,
    },
)
```

The client (Claude Code) reads the `.py` and any sidecar `.js` files off the local filesystem and passes their contents. The MCP server never touches the client's filesystem.

## Server-side behavior

### 1. Parse via constrained AST walk (no `exec`)

The server parses `template_source` with `ast.parse` and walks the module for these top-level assignments:

- `RENDER_CODE` — string. Two accepted forms:
  - A string literal (`RENDER_CODE = "..."`).
  - The sidecar idiom: `RENDER_CODE = (Path(__file__).parent / "<name>").read_text(...)`. The walker recognizes this exact AST shape and resolves `<name>` against `sidecar_files`. Any other expression form is rejected with `INVALID_TEMPLATE`.
- `DEFINITION` — dict. Must be a dict literal (`ast.literal_eval`able). Reject otherwise.
- `PIPELINE_SCHEMAS` — list of dicts (plural; matches the live template convention, not the stale singular `pipeline_schema` in `base.py`'s docstring). Optional. Must be a list literal.
- `TEMPLATE` — dict literal containing at minimum a `key` field. Used only to confirm the template's `key` matches the target workflow's `template_type` (see step 2).

Why constrained AST rather than `exec`: PAT auth already lets the caller clobber any workflow they can access, so the new tool doesn't expand *workflow* trust. But running arbitrary client-supplied Python in the server process would meaningfully expand the *server* trust surface (escape-the-container risk, ability to call any installed library). All four existing sidecar-using templates use the same idiom, so a structured AST walker covers the real world without giving up that boundary.

### 2. Validate

Before any write:

- `RENDER_CODE` exists, is a non-empty string, ≤ 512 KB (same cap as `workflow_update_render_code`).
- `DEFINITION` is a dict with the standard required keys (`statuses`, `pipeline_sources`, etc. — match the existing template contract in `base.py`'s `TemplateDefinition`).
- `PIPELINE_SCHEMAS`, if present, is a list of schema dicts each conforming to `PipelineSchema`.
- `TEMPLATE["key"]` matches the target workflow's `template_type`. This prevents accidental cross-template syncs (e.g. pushing the `kmc_longitudinal` template into an `mbw_auditing_v4` workflow). Mismatch raises `TEMPLATE_KEY_MISMATCH`.
- `expected_render_code_version` and `expected_definition_version` match current — same optimistic-concurrency pattern as `workflow_update_render_code` and `workflow_update_definition`. Mismatch raises `VERSION_CONFLICT`.

If `dry_run=True`, the response includes a diff summary (see step 4) and stops. No writes.

### 3. Apply writes (ordered, not atomic)

Writes happen in this order against the existing data-access layer:

1. `WorkflowDataAccess.update_definition(...)` — bumps `definition.version`.
2. `WorkflowDataAccess.save_render_code(..., version=expected_render_code_version + 1)` — bumps `render_code.version`.
3. For each entry in `PIPELINE_SCHEMAS`: look up the pipeline by alias (matching `definition.pipeline_sources[*].alias`), call `PipelineDataAccess.update_schema(...)`.

Partial-failure handling: any failure mid-sequence surfaces an `MCPToolError` with `code="PARTIAL_SYNC"` and a `details` payload listing what was written and what was not. Callers can then re-fetch via `workflow_get` and decide whether to retry the remaining steps. This matches the existing labs pattern where multi-record updates are not transactional.

### 4. Return

```python
{
    "workflow_id": int,
    "render_code": {
        "version_before": int,
        "version_after": int,           # equals version_before on dry_run
        "bytes_before": int,
        "bytes_after": int,
        "changed": bool,
    },
    "definition": {
        "version_before": int,
        "version_after": int,
        "changed_keys": [str, ...],     # top-level keys that differ
    },
    "pipelines": [
        {
            "alias": str,
            "pipeline_id": int,
            "schema_version_before": int,
            "schema_version_after": int,
            "changed": bool,
        },
        ...
    ],
    "dry_run": bool,
}
```

## Skill update

`workflow-author` gets a decision point near the top, before the existing instructions:

> **Are you iterating on a template that should land in git, or just tweaking a one-off workflow?**
>
> - **One-off workflow (default):** edit `render_code` / `definition` directly on the workflow via `workflow_update_render_code`, `workflow_patch_render_code`, `workflow_update_definition`. No template file needed. This is the right choice for most edits.
> - **Template authoring:** edit the `.py` file in `connect_labs/workflow/templates/`, then call `workflow_sync_from_template_file` to push it to your live preview workflow. The template file is the source of truth — do not fork iteration onto the workflow itself. Commit the `.py` when the design has settled.

`workflow-templates` (the seed-template authoring skill) gets a pointer to the new tool: "While iterating on a new template, use `workflow_sync_from_template_file` against a preview workflow created via `workflow_create_from_template`. This gives you the deploy-free iteration loop the in-place `workflow_update_*` tools provide for one-off workflows."

## Testing

Pytest coverage:

- AST parsing — happy path with literal `RENDER_CODE`, happy path with sidecar idiom, rejection of arbitrary expressions, rejection of unknown sidecar names, missing `RENDER_CODE` / `DEFINITION`, oversized render code.
- Validation — non-dict `DEFINITION`, non-list `PIPELINE_SCHEMAS`, `TEMPLATE["key"]` mismatch vs workflow `template_type`, version conflicts on each of the two version counters.
- Write ordering — happy path applies all three; pipeline-schema failure mid-sequence leaves definition and render_code written and returns `PARTIAL_SYNC` with the right details payload.
- `dry_run` — no writes, diff payload computed correctly.
- Fixture templates — round-trip the four real sidecar templates (`mbw_auditing_v4`, `mbw_monitoring_v2`, `mbw_monitoring_v3`, plus one literal-only template) through the parser and assert extracted artifacts equal the in-tree TEMPLATE dict.

## Risks and open questions

- **Constrained AST walker rigidity.** If a future template author uses a different file-loading idiom (e.g. `open(...).read()`, helper functions), the walker will reject it with `INVALID_TEMPLATE`. Mitigation: error message names the expected pattern and points at an example; we extend the walker when a real second pattern shows up.
- **PARTIAL_SYNC ergonomics.** Callers (Claude Code) need to know how to recover. The skill update should include a one-liner: "On `PARTIAL_SYNC`, call `workflow_get` to see current state, fix the failing piece, and re-run." If this happens often in practice, we revisit and add a server-side rollback (write the prior render_code back).
- **Template-key match check.** Some workflows in the wild may have `template_type` set to a key that no longer exists in the registry (template renamed/removed). The check should be against the `TEMPLATE["key"]` in the *supplied* source, not the registry — so renaming the template in git and syncing into the rename works.
- **Pipeline identity for schema updates.** `PIPELINE_SCHEMAS` entries carry a `name`, but live pipelines are linked to a workflow via `definition.pipeline_sources[*].alias`. Implementation needs to confirm the mapping rule used at template-creation time (likely `schema["name"] == source["alias"]`, but worth verifying against `create_workflow_from_template`) and apply the same rule on sync. If the rule is fragile, fall back to positional matching with a clear error when counts diverge.
