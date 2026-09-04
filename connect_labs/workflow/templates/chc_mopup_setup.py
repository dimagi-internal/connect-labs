"""CHC Mop-up Setup — Program 217 ("CHC - NG - RCT - Aug 2026").

A zero-pipeline, action-shaped "pick your opportunity first" front door for
the "CHC Mop-up Candidate Analysis" dashboard (`chc_mopup_candidates.py`).

Why this template exists
-------------------------
The dashboard is multi-opp (all 4 LLOs on program 217) and always fetches
every pipeline source's full data for every opportunity in scope before its
render code is even mounted -- confirmed directly in
`connect_labs/static/js/workflow-runner.tsx`: the page shell renders
`pipelineLoadingStatus ? <spinner> : <DynamicWorkflow {...workflowProps}
renderCode={renderCode} .../>`, so `DynamicWorkflow` (and the dashboard's own
`WorkflowUI`) is never mounted until every pipeline has finished fetching for
every opportunity currently in the running instance's scope. No trick inside
the dashboard's own render code can show a lightweight picker first -- the
fetch always happens before that render code gets control. A mop-up round is
always run for ONE LLO at a time, so a reviewer doesn't need (and shouldn't
have to wait for) all 4 opportunities' full pipeline data just to pick which
one to work on.

This template is deliberately zero-pipeline (`PIPELINE_SCHEMAS = []`,
`multi_opp: True`, no `supports_saved_runs`) -- the same "fast-loading,
pipeline-free, config-style" shape as `weekly_dual_track_audit.py`'s setup
UI, which is why that file (not `chc_mopup_candidates.py`) was read first as
the precedent for this one's conventions. Reading `instance.opportunity_ids`
/ `definition.opportunity_ids` to render one button per opportunity is free
-- those are always on the props already, no pipeline round-trip involved.

What a button click does
-------------------------
See `chc_mopup_setup_render.js`'s own module comment for the full mechanics.
In short: (1) POST the chosen single opportunity_id to
`UpdateOpportunityIdsView` (`/labs/workflow/api/<dashboard_definition_id>/
opportunity-ids/`) to narrow the DASHBOARD workflow's own opportunity scope,
then (2) POST to `start_run_api` (`/labs/workflow/api/<dashboard_definition_
id>/run/start/`) to create a fresh run against the now-narrowed dashboard,
then (3) navigate to that run. Both URLs are built directly from
`definition.config.dashboard_definition_id` -- a plain config value set
manually after this workflow instance is created (see "Manual follow-up"
below) -- NEVER from `window.WORKFLOW_API_ENDPOINTS`, which is scoped to
whichever workflow definition is currently on screen (i.e. THIS setup
workflow's own definition_id, not the dashboard's).

Why narrowing the dashboard's `opportunity_ids` needs Part 1 of this change
----------------------------------------------------------------------------
Before this template could exist, the dashboard's `work_areas` /
`wa_geometry` / `audit_entries` pipelines were reused, already-live pipelines
owned by opportunity 2156 (ISODAF) -- see `chc_mopup_candidates.py`'s module
docstring. That only worked because every instance of the dashboard template
was scoped to all 4 opportunities on program 217 at once:
`_resolve_pipeline_definition`'s cross-opp retry only searches opportunities
already in the *running instance's* `opportunity_ids`, so narrowing that
instance down to a single, different opportunity via this template's setup
flow would have made pipeline 2156's data invisible (silent 404) to it.
`chc_mopup_candidates.py` makes those three pipelines genuinely
template-owned (auto-created fresh per instance, exactly like
`visit_quality` always was) specifically so this setup flow's opportunity-
narrowing step actually works.

Manual follow-up (NOT done by this file -- see the workflow_* MCP tools)
---------------------------------------------------------------------------
This template's Python/JS is committed code only. Creating a live instance of
it, and setting its `config.dashboard_definition_id` to the real dashboard
workflow's definition_id, is a separate, deliberately manual step (the
dashboard workflow's definition_id already changed once this project when it
was recreated -- hardcoding it here would silently break the moment that
happens again).
"""

from pathlib import Path

PIPELINE_SCHEMAS = []

DEFINITION = {
    "name": "CHC Mop-up Setup",
    "description": "Pick which opportunity to run this round's CHC Mop-up Candidate Analysis for, then jump "
    "straight into a freshly-scoped dashboard run.",
    "version": 1,
    "templateType": "chc_mopup_setup",
    "statuses": [
        {"id": "active", "label": "Active", "color": "gray"},
    ],
    "config": {
        "auth_requires": ["connect"],
        # Set manually after this workflow instance is created -- see the
        # module docstring's "Manual follow-up" section. Left None here
        # (rather than a real id) so a never-configured instance fails loud
        # (an inline error in the render code) instead of silently pointing
        # at a stale or wrong dashboard.
        "dashboard_definition_id": None,
    },
    "pipeline_sources": [],
}

RENDER_CODE = (Path(__file__).parent / "chc_mopup_setup_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "chc_mopup_setup",
    "name": "CHC Mop-up Setup",
    "description": "Zero-pipeline opportunity picker: narrows the CHC Mop-up Candidate Analysis dashboard to "
    "one LLO and starts a fresh run against it, without waiting for every opportunity's pipeline data to load.",
    "icon": "fa-list-check",
    "color": "orange",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
