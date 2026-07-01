# Workflow-delete backup (labs DB)

**Date:** 2026-07-01
**Status:** Approved design — ready for implementation planning
**Author:** jjackson (with Claude)

## Problem

Deleting a workflow is irreversible and leaves no trail. Workflow definition
`4644` (opp 1973) was created and deleted within a day this week; the only
surviving artifact was an orphaned run (`4667`) carrying `definition_id=4644`
and an empty state — not enough to reconstruct anything. The definition JSON and
its render-code JSX (the actual body of the workflow) were hard-deleted and are
unrecoverable. `WorkflowDataAccess.delete_definition` emits no log line either,
so there is no forensic trace of what was deleted or when.

We want a safety net: **whenever a workflow is deleted, first save a restorable
copy of the workflow itself (definition + render code) into the labs DB.** Runs
are explicitly out of scope.

## Goals

- On every workflow-definition delete, persist a backup of the definition +
  its render code to the labs Django DB **before** the delete executes.
- Cover all delete entry points automatically (UI delete view + MCP
  `workflow_delete`) via a single hook.
- Fail-closed: if the backup cannot be written, abort the delete and raise.
- Make deleted workflows human-recoverable today via Django admin (read the
  backup row, re-create via the API), without building dedicated restore
  tooling yet.

## Non-goals (YAGNI)

- **No restore command / MCP tool.** Recovery is manual for now. A dedicated
  restore path can be added later if the manual route proves painful.
- **No backup of runs, audit sessions, or chat history.** Only the workflow
  definition and its render code.
- **No retention/pruning.** Keep backups forever; volume is low.
- **No UI surface** beyond Django admin.

## Design

### Trigger — single choke point

Add the backup step inside `WorkflowDataAccess.delete_definition`
(`commcare_connect/workflow/data_access.py:568`), executed **before** the
`self.labs_api.delete_records(ids_to_delete)` batch. Both existing callers flow
through this method:

- `commcare_connect/workflow/views.py:2276` and `:2332` (UI delete view)
- `commcare_connect/mcp/tools/workflows.py:1160` (`workflow_delete` MCP tool)

Hooking the data-access method (not each caller) makes the behavior automatic
and future-proof for any new caller.

### What gets captured

From the definition being deleted:

- `definition.data` — the full definition JSON (name, description, statuses,
  config, pipeline_sources, opportunity_ids, snapshot_inputs, …)
- The `workflow_render_code` body — fetched via the existing
  `self.get_render_code(definition_id)`; store its `component_code` (the JSX).
  A workflow may legitimately have no render code yet; in that case store an
  empty/None render body (still a valid backup of the definition).

Derived/denormalized columns for easy lookup:

- `definition_id` — the prod (or labs-local) record id being deleted
- `opportunity_id` — from the definition record (fall back to
  `self.opportunity_id`)
- `name` — `definition.data.get("name", "")`
- `template_type` — `definition.template_type` (property backed by
  `data.config.templateType`)
- `deleted_by` — `self.user.username` when `self.user` is set, else `""`
- `deleted_at` — set on write

### Storage — new dedicated model

New model `DeletedWorkflowBackup` in `commcare_connect/labs/models.py`,
alongside the existing labs-DB model `UserConnectToken`, with a new migration
in `commcare_connect/labs/migrations/`. The `workflow/` app is pure
API-backed (no `models.py`, no migrations), so it cannot host a Django model;
the core labs app already owns labs-DB tables and is the correct home. A
dedicated table is chosen over reusing `LabsLocalRecord` so backups never mix
into the synthetic-opp ORM dispatch path.

Fields:

| field             | type              | notes                                   |
| ----------------- | ----------------- | --------------------------------------- |
| `definition_id`   | IntegerField (idx)| the deleted record's id                 |
| `opportunity_id`  | IntegerField (idx)| from the definition                     |
| `name`            | CharField         | denormalized for admin scanning         |
| `template_type`   | CharField (blank) | denormalized                            |
| `definition_data` | JSONField         | full definition JSON                    |
| `render_code`     | TextField (blank) | the JSX body (may be empty)             |
| `deleted_by`      | CharField (blank) | username if available                   |
| `deleted_at`      | DateTimeField     | `auto_now_add`                          |

Ships with a Django migration. Registered in Django admin (read-oriented list:
`definition_id`, `opportunity_id`, `name`, `template_type`, `deleted_by`,
`deleted_at`) so a human can find and re-create a deleted workflow.

### Fail-closed behavior

The backup write happens before the delete batch. If building or saving the
backup raises, `delete_definition` propagates the exception and does **not**
call `delete_records` — the workflow is left intact. Rationale: the entire
purpose is to prevent silent loss; a rare "delete failed, retry" is preferable
to deleting without a backup.

Note the ordering trade-off: backup-before-delete means a delete that fails
*after* a successful backup can leave a backup row for a workflow that still
exists. That is harmless (a stale/duplicate backup, not data loss) and
acceptable. On a later successful delete, a new backup row is written; both
rows are retained (no dedup, per no-pruning).

### Logging

Emit `logger.info` on a successful backup (e.g.
`[WorkflowBackup] backed up definition=<id> opp=<id> name=<...> by=<user>`),
giving deletes the audit trail they lack today.

### Scope: all opportunity types

Back up regardless of whether the opportunity is a real prod opp or a
labs-only synthetic opp. Simpler (no branching) and harmless — synthetic
workflow definitions are equally worth a safety copy.

## Components

1. **`DeletedWorkflowBackup` model + migration** — the labs-DB table, added to
   `commcare_connect/labs/models.py` with a migration in
   `commcare_connect/labs/migrations/`.
2. **Admin registration** — read-oriented list + detail for manual recovery.
3. **Backup step in `WorkflowDataAccess.delete_definition`** — fetch definition
   + render code, write the backup row, fail-closed, log. Runs before the
   existing delete batch; the rest of the method (render/chat/optional
   runs+audits deletion) is unchanged.

## Data flow

```
caller (UI view | MCP workflow_delete)
  -> WorkflowDataAccess.delete_definition(definition_id, delete_linked)
       1. definition   = get_definition(definition_id)      # already fetched paths exist
       2. render_code  = get_render_code(definition_id)
       3. DeletedWorkflowBackup.objects.create(...)          # FAIL-CLOSED
          - on error: raise; no delete happens
       4. logger.info("[WorkflowBackup] ...")
       5. (existing) collect ids_to_delete (+ runs/audits if delete_linked)
       6. (existing) labs_api.delete_records(ids_to_delete)
```

## Error handling

- Backup write failure → exception propagates, delete aborted (fail-closed).
- Missing render code → not an error; store empty render body.
- Missing definition → `get_definition` returns None. Current code tolerates a
  missing definition in the non-linked path; the backup step must guard
  `definition is None` and skip backup for a definition that no longer exists
  (nothing to lose), rather than raise. Confirm exact behavior during planning
  against the existing method's handling.

## Testing

- Unit: deleting a workflow with definition + render code writes a
  `DeletedWorkflowBackup` row with the correct `definition_data`, `render_code`,
  `name`, `template_type`, `deleted_by`.
- Unit: workflow with no render code → backup row written with empty render
  body, delete proceeds.
- Unit (fail-closed): backup `.create()` raising → `delete_definition` raises
  and `labs_api.delete_records` is **not** called (assert with a mock).
- Unit: `delete_linked=True` still backs up only the definition + render code
  (no run/audit data captured), and still deletes runs/audits as before.
- MCP: existing `workflow_delete` tests continue to pass (behavior additive).

## Open items for planning

- Confirm the exact `definition is None` guard against the current method's
  handling (the non-linked path already fetches the definition lazily; the
  backup step adds an explicit fetch up front).
