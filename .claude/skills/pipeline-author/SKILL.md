---
name: pipeline-author
description: Use this skill when iterating on a live pipeline in labs — editing schemas, previewing sample rows, saving changes — via the connect_labs MCP. Triggers on phrases like "add a field to pipeline X", "change the aggregation on", "preview pipeline", "why is pipeline N returning wrong data". Do NOT use for workflow JSX edits (use workflow-author for that).
---

# Authoring Live Pipelines

Use the `connect_labs` MCP tools to iterate on a pipeline schema with a fast preview-then-save loop, without copy-pasting through the browser.

**Prereq:** same as workflow-author — user must have logged into labs in a browser at least once (creates `UserConnectToken`) and configured a PAT per `docs/MCP_SETUP.md`.

## Basic iteration loop

1. **Pull the pipeline.** Call `pipeline_get(pipeline_id, opportunity_id)`. You get the full schema (fields, aggregations, transforms, groupings) and the current version number.

2. **If adding new fields from a form, discover the JSON paths first.** Use the local `commcare_hq_mcp` tools (this is the one place the two MCPs cross): `get_opportunity_apps(opportunity_id)` → `get_app_structure(domain, app_id)` → `get_form_json_paths(xmlns, domain, app_id)`. You get the exact paths like `form.anthropometric.child_weight_visit` to use in new `src` values on fields.

3. **Propose a schema diff in chat.** Show the user what you're changing before you preview. Short list: "Adding field `visits_per_flw` (count of `form.visit_id`, agg `count_distinct`), changing `children_seen` from `sum` to `count_distinct`." Wait for user confirmation or iteration — it's cheap to talk before running SQL.

4. **Preview without persisting.** `pipeline_preview(pipeline_id, opportunity_id, schema_override=new_schema, sample_size=10)` runs the proposed schema against real data and returns sample rows. `schema_override` is the key — it never persists, so you can iterate freely.

5. **Iterate.** Read the rows with the user. If counts look wrong, if a field is null when it shouldn't be, if the aggregation produced garbage — refine the schema and preview again. Tight loop.

6. **Save.** When the preview is right, `pipeline_update_schema(pipeline_id, opportunity_id, schema=new_schema, expected_version=V)`. Report the new version number.

7. **If a workflow depends on this pipeline,** suggest pivoting to `workflow-author` to verify the UI still renders correctly — a schema change can invalidate JSX that reads fields that no longer exist.

## Error handling

- **VERSION_CONFLICT:** pipeline changed on the server between your read and your write. Call `pipeline_get` again, reapply changes on top, retry.
- **INVALID_SCHEMA:** your schema has an unknown aggregation. Valid ones: `sum`, `count`, `count_distinct`, `avg`, `min`, `max`, `first`, `last`. The error message will tell you which field + which bad aggregation.
- **UPSTREAM_ERROR from pipeline_preview:** the pipeline ran but the backend returned a SQL error. Read the `metadata.error` in the error details — it typically names a missing table or field. Often fixable by correcting a field `src` path.
- **PERMISSION_DENIED:** user's `UserConnectToken` is missing or expired. Tell them to log into labs in a browser and retry.

## Debugging with SQL

If a preview produces rows you don't understand, call `pipeline_sql(pipeline_id, opportunity_id, schema_override=new_schema)` to see the generated SQL. It returns a dict with per-stage queries (`visit_extraction_sql`, `flw_aggregation_sql`, per-field expressions, etc.). Read that SQL alongside the sample rows to trace where data transforms happen.

## Anti-patterns

- **Do NOT** `pipeline_update_schema` without previewing first. The allow-list catches aggregation typos, but not semantic errors that only show up in the data.
- **Do NOT** edit the schema and the dependent workflow's JSX in the same turn. Verify the pipeline first, then pivot to `workflow-author` for the workflow — that way if the JSX breaks, you know it's about your render changes, not about a concurrent pipeline change.
- **Do NOT** retry on `VERSION_CONFLICT` without re-reading.
