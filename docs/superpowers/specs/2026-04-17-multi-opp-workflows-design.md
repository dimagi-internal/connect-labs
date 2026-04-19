# Multi-Opportunity Workflows — Design

**Status:** Draft
**Date:** 2026-04-17
**Owner:** jjackson@dimagi.com

## Problem

Today, every workflow run is bound to a single opportunity. Pipelines, workers, and record ownership all flow from one `opportunity_id` in `labs_context`. A user who wants to view merged FLW activity across multiple opps in a single workflow can't — they have to run the same workflow separately per opp and mentally stitch the results together.

We want a single workflow run to pull data from multiple opportunities and present it as one merged dataset to the React render code.

## Scope

**In scope**

- A workflow definition can declare a set of opportunities (`opportunity_ids`) whose data it aggregates.
- Templates opt in to multi-opp capability via a `multi_opp` flag.
- The create flow shows an opp picker for multi-opp templates; single-opp templates are unchanged.
- Pipelines and the workers list execute per-opp and merge rows, tagging each row with its source `opportunity_id`. No deduplication — templates handle that if they need to.
- Users can edit the opp set on an existing multi-opp workflow.

**Out of scope**

- Cross-opp row deduplication in the engine (left to template code).
- Parallel pipeline execution across opps (sequential for now; caching and small N make this fine).
- Cross-opp permission elevation — users can only pick opps they already have membership in.
- Changing the "primary" opp (record owner) after creation.
- Multi-program or multi-organization scoping (only opportunities are generalized).

## Background

### How workflows are bound to opps today

1. **Record scoping.** Every workflow definition and run is a `LabsRecord` created via `LabsRecordAPIClient`. The client is initialized with an `opportunity_id` (from `labs_context`), which is stored on the record for permission checks.
2. **Pipeline records are already opp-agnostic.** A `PipelineDefinitionRecord` stores only a schema (fields, filters, aggregations, data source type). The record has an `opportunity_id` column for permission scoping, but that value is not used during execution — the `opportunity_id` is a runtime parameter of `execute_pipeline(pipeline_id, opportunity_id)`. This means the execution layer is already parameterized; it just can't currently accept more than one opp.
3. **Workers** come from a single call to `/export/opportunity/{id}/user_data/` keyed on the current context opp.
4. **Runs** store one `opportunity_id` each and are matched by it.

### Pattern precedent

`commcare_connect/workflow/templates/mbw_monitoring/views.py` and `flw_api.py` contain bespoke multi-opp merging logic. It was written outside the generic workflow engine and is not used as a reference — this design starts fresh.

## Design

### Data model

**Workflow definition (`LabsRecord` with `type="workflow_definition"`)**

Adds one field to `data`:

```python
data: {
    ...existing fields,
    "opportunity_ids": list[int],  # opps this workflow pulls data from
}
```

- Absent or empty → engine falls back to `[primary_opportunity_id]` (the record owner), preserving existing single-opp behavior. No migration needed.
- May or may not include the primary opp. No special treatment.

**Primary opp semantics**

- The record's `opportunity_id` column (set at create time from `labs_context.opportunity_id`) is the "primary opp."
- Its only roles: (1) permission check on the record itself, and (2) which opp's workflow list this workflow appears in.
- It is **not** a member of `opportunity_ids` by any rule. Users can include or exclude it freely.

**Workflow template registration**

In `workflow/templates/<name>.py`, a template's `TEMPLATE` dict adds:

```python
TEMPLATE = {
    "key": "...",
    ...,
    "multi_opp": True,  # default False if absent
}
```

Surfaced through the template registry so the create flow can branch on it.

### Creation flow

**Single-opp templates (default).** Current flow, unchanged.

**Multi-opp templates.**

1. In the workflow list view, template cards marked `multi_opp` render a different CTA (e.g. "Configure & Create" instead of "Create"). Clicking opens a modal.
2. Modal contains a multi-select populated from `user_opportunities` (already in template context via `labs/context.py`). Primary opp (current `labs_context.opportunity_id`) is pre-selected by default.
3. On submit, POST to `/workflow/create/` with `template=<key>` and `opportunity_ids=[...]`.
4. `create_workflow_from_template` receives `opportunity_ids` and stores them in `definition.data`. The record itself is created under the primary opp (current context) as today — so record-level permission scoping is unchanged.

**Permission constraint.** The picker is sourced from `user_opportunities`, so users can only choose opps they already have membership in. No new permission surface. Backend still validates on submit.

### Editing the opp set

On a multi-opp workflow's run page, show a control near the header:

```
Opportunities: Opp A, Opp B, Opp C ✎
```

- Clicking opens the same multi-select modal, pre-populated with the current `opportunity_ids`.
- Save → `POST /workflow/api/<definition_id>/opportunity-ids/` with `{"opportunity_ids": [...]}`.
- Backend validates each ID against `user_opportunities`, then calls `WorkflowDataAccess.update_opportunity_ids`.
- After save, page reloads to re-stream pipeline data against the new set. (Cache keys in the pipeline layer are already keyed by `opportunity_id`, so per-opp caches are reused.)
- Single-opp workflows do not show this control.

### Execution flow

**Pipeline data (`WorkflowDataAccess.get_pipeline_data`):**

```python
opp_ids = definition.data.get("opportunity_ids") or [primary_opp_id]

results = {}
for source in definition.pipeline_sources:
    pipeline_id, alias = source["pipeline_id"], source["alias"]
    merged_rows = []
    per_opp_meta = {}
    for opp_id in opp_ids:
        try:
            result = pipeline_access.execute_pipeline(pipeline_id, opp_id)
            for row in result["rows"]:
                row["opportunity_id"] = opp_id
            merged_rows.extend(result["rows"])
            per_opp_meta[opp_id] = result["metadata"]
        except Exception as e:
            logger.exception("Pipeline %s failed for opp %s", pipeline_id, opp_id)
            per_opp_meta[opp_id] = {"error": str(e)}
    results[alias] = {
        "rows": merged_rows,
        "metadata": {
            "opportunity_ids": opp_ids,
            "per_opp": per_opp_meta,
            "row_count": len(merged_rows),
        },
    }
```

The SSE variant (`PipelineDataStreamView`) uses the same nested loop and yields per-opp progress events: `"Loading <alias> (opp N/M)…"`.

**Workers (`WorkflowRunView.get_context_data`):**

```python
all_workers = []
for opp_id in opp_ids:
    for w in data_access.get_workers(opp_id):
        w["opportunity_id"] = opp_id
        all_workers.append(w)
context["workers"] = all_workers
```

**Error handling.** If a single opp fails (permission revoked mid-session, API error), log it, record the error under `per_opp[opp_id]`, and continue. Partial data is strictly better than a total failure for a dashboard showing 5 opps where 1 is unavailable.

### React render code contract

```typescript
instance: {
  id: number;
  opportunity_id: number;          // primary (unchanged)
  opportunity_ids: number[];       // NEW — full opp set, or [primary] for legacy
  state: object;
  status: string;
  // ...
}

workers: [
  { username, name, opportunity_id, ... }   // opportunity_id now meaningful
]

pipelines: {
  [alias]: {
    rows: [{ ...fields, opportunity_id }],  // every row tagged
    metadata: {
      opportunity_ids: number[],
      // Keys are strings because JSON serialization coerces dict keys to strings.
      // JS access pattern: `metadata.per_opp[String(oppId)]`.
      per_opp: { [opp_id_as_string: string]: { row_count, from_cache, error? } },
      row_count: number,
    }
  }
}
```

Single-opp workflows see `opportunity_ids = [primary]` and every row tagged with the same opp — behavior identical to today. Multi-opp templates consume tagged rows and implement any grouping or deduplication in JSX.

### Backward compatibility

- Existing workflow definitions have no `opportunity_ids` → engine reads the fallback `[primary_opp_id]` → behaves exactly as today.
- Existing templates have no `multi_opp` flag → defaults False → create flow unchanged.
- Existing render code reads `instance.opportunity_id` — unchanged. `instance.opportunity_ids` and per-row tagging are additive.
- No LabsRecord migration. No data backfill.

## Files to change

- `commcare_connect/workflow/templates/__init__.py` — expose `multi_opp` through the registry; plumb `opportunity_ids` through `create_workflow_from_template`.
- `commcare_connect/workflow/data_access.py`
  - `WorkflowDefinitionRecord`: add `opportunity_ids` property.
  - `WorkflowDataAccess.get_pipeline_data`: nested per-opp loop with row tagging and per-opp metadata.
  - `WorkflowDataAccess.create_definition` (and `create_workflow_from_template`): accept `opportunity_ids`.
  - `WorkflowDataAccess.update_opportunity_ids`: new method.
- `commcare_connect/workflow/views.py`
  - `create_workflow_from_template_view`: accept and validate `opportunity_ids` in POST body.
  - `WorkflowRunView.get_context_data`: merge workers per-opp, tag each worker with `opportunity_id`.
  - `PipelineDataStreamView`: SSE generator loops over opp_ids per pipeline source, tags rows, yields per-opp progress events.
  - `UpdateOpportunityIdsView`: new endpoint for editing.
- `commcare_connect/workflow/urls.py` — route for `UpdateOpportunityIdsView`.
- `commcare_connect/templates/workflow/list.html` (and any React list component) — opp picker modal for multi-opp templates.
- `commcare_connect/templates/workflow/run.html` — "Opportunities: … ✎" control + edit modal for multi-opp workflows.
- At least one multi-opp reference template — validates the contract end-to-end. (New or adapted; decision during implementation.)

## Testing

- **Unit (`workflow/tests/test_data_access.py` or similar)**
  - `get_pipeline_data` with `opportunity_ids=[A, B]`, mocking `execute_pipeline`, asserts rows tagged with correct opp and merged.
  - `get_pipeline_data` with one opp raising, asserts partial-failure path: rows from surviving opps returned, `per_opp[failing].error` set.
  - `update_opportunity_ids` round-trips correctly.
  - Legacy fallback: definition without `opportunity_ids` uses `[primary_opp_id]` and returns single-opp result matching today's shape.
- **Integration (`workflow/tests/test_views.py`)**
  - POST `/workflow/create/` with `opportunity_ids=[A, B]` on a multi-opp template: definition persists with correct `opportunity_ids`; `permissions` check rejects opps not in `user_opportunities`.
  - GET run page: `workflow_data.instance.opportunity_ids` populated, `workers` merged with correct tags.
  - POST `UpdateOpportunityIdsView`: definition updates; rejects opps not in `user_opportunities`.
- **E2E (`workflow/tests/e2e/`, optional)**
  - Create multi-opp workflow with 2 opps, assert SSE stream yields per-opp events, final rows are correctly tagged.

## Open questions

- Which specific template gets converted to multi-opp for the reference implementation? Defer to implementation plan.
- Should cache invalidation on opp-set edit be explicit, or is a page reload sufficient? Current recommendation: reload only; revisit if stale-data surfaces in practice.

## Future work

- Cross-opp deduplication helpers in the engine if multiple templates end up reimplementing the same merge logic.
- Parallel per-opp pipeline execution if N gets large enough that sequential becomes a real bottleneck.
- Ability to change the primary opp after creation (today: fixed at create time).
