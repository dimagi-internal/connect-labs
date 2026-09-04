"""CHC Mop-up Setup — Program 217 ("CHC - NG - RCT - Aug 2026").

A zero-pipeline, action-shaped "pick your opportunity first" front door for
the "CHC Mop-up Candidate Analysis" dashboard (`chc_mopup_candidates.py`).

Why this template exists
-------------------------
The dashboard is multi-opp-capable and always fetches every pipeline
source's full data for every opportunity in scope before its render code is
even mounted -- confirmed directly in `connect_labs/static/js/workflow-
runner.tsx`: the page shell renders `pipelineLoadingStatus ? <spinner> :
<DynamicWorkflow {...workflowProps} renderCode={renderCode} .../>`, so
`DynamicWorkflow` (and the dashboard's own `WorkflowUI`) is never mounted
until every pipeline has finished fetching for every opportunity currently
in the running instance's scope. No trick inside the dashboard's own render
code can show a lightweight picker first -- the fetch always happens before
that render code gets control. A mop-up round is always run for ONE LLO at
a time, so a reviewer doesn't need (and shouldn't have to wait for) all 4
opportunities' full pipeline data just to pick which one to work on.

This template is deliberately zero-pipeline (`PIPELINE_SCHEMAS = []`,
`multi_opp: True`, no `supports_saved_runs`) -- the same "fast-loading,
pipeline-free, config-style" shape as `weekly_dual_track_audit.py`'s setup
UI. Reading `instance.opportunity_ids` / `definition.opportunity_ids` to
render one button per opportunity is free -- those are always on the props
already, no pipeline round-trip involved.

Design correction: one dedicated dashboard PER opportunity, not one shared
instance narrowed at click-time
--------------------------------------------------------------------------
The first version of this template narrowed a single SHARED dashboard
instance's `opportunity_ids` down to the chosen opportunity (via
`UpdateOpportunityIdsView`) before starting a run against it. That broke in
practice: a workflow definition's `pipeline_sources` point at specific
pipeline_ids created once, at the definition's original creation time, owned
by whichever opportunity was primary THEN (confirmed live: a shared
dashboard created with `opportunity_ids=[2154,2155,2156,2157]` auto-creates
its `work_areas`/`wa_geometry`/`audit_entries`/`visit_quality` pipelines
owned by 2154. `_resolve_pipeline_definition`'s cross-opp retry -- see
`chc_mopup_candidates.py`'s module docstring -- only searches opportunities
already in the *running instance's current* `opportunity_ids`. Narrowing
that same instance down to `[2155]` afterward EXCLUDES 2154, the pipelines'
actual owner, from scope -- so the lookup 404s silently and the dashboard
renders "0 work areas in scope" for every opportunity except whichever one
happened to be primary at creation. This is the exact same failure class
`chc_mopup_candidates.py`'s Part 1 fix was written to prevent, just
triggered by narrowing an EXISTING shared instance after the fact rather
than by borrowing a genuinely different template's pipeline.

The fix: this setup workflow's config holds one dashboard_definition_id PER
opportunity (`dashboard_definition_ids`, a dict keyed by opportunity_id as a
string -- JSON object keys are always strings), each one a SEPARATE
`chc_mopup_candidates` instance created with `opportunity_ids=[that single
opportunity]` from the start. Each such instance auto-creates its own
pipelines owned by its own (only) opportunity, so there is no cross-opp
resolution to break, and no narrowing step is needed at all. A button click
now does exactly one thing: POST to `start_run_api` for the dashboard
definition dedicated to the chosen opportunity, then navigate to the new
run. See `chc_mopup_setup_render.js`'s own module comment for the mechanics.

The original all-4-opportunities dashboard instance is unaffected by any of
this and remains valid on its own terms -- its pipelines are owned by 2154,
which is (and always will be, since nothing narrows it) a member of its own
`opportunity_ids=[2154,2155,2156,2157]`. It's kept around as an optional
"compare across all 4 LLOs at once" view for whoever wants that, separate
from this setup flow's fast, single-opportunity path.

Manual follow-up (NOT done by this file -- see the workflow_* MCP tools)
---------------------------------------------------------------------------
This template's Python/JS is committed code only. Someone still has to:
1. Create one `chc_mopup_candidates` instance per opportunity (each with
   `opportunity_ids=[that one opportunity]`).
2. Create an instance of this template.
3. Set its `config.dashboard_definition_ids` to `{"<opportunity_id>":
   <that opportunity's dedicated dashboard definition_id>, ...}` for all 4.
Left as `{}` here (rather than real ids) so a never-configured instance, or
one missing an entry for a given opportunity, fails loud (an inline error in
the render code naming the specific opportunity) instead of silently
pointing at a stale, wrong, or shared-and-therefore-broken dashboard.
"""

from pathlib import Path

PIPELINE_SCHEMAS = []

DEFINITION = {
    "name": "CHC Mop-up Setup",
    "description": "Pick which opportunity to run this round's CHC Mop-up Candidate Analysis for, then jump "
    "straight into a dashboard run dedicated to just that opportunity.",
    "version": 1,
    "templateType": "chc_mopup_setup",
    "statuses": [
        {"id": "active", "label": "Active", "color": "gray"},
    ],
    "config": {
        "auth_requires": ["connect"],
        # {"<opportunity_id>": <dedicated dashboard definition_id>, ...} --
        # set manually after this workflow instance AND one dashboard
        # instance per opportunity are created (see the module docstring's
        # "Manual follow-up" section). Keys are strings because JSON object
        # keys always are; the render code stringifies before looking up.
        "dashboard_definition_ids": {},
    },
    "pipeline_sources": [],
}

RENDER_CODE = (Path(__file__).parent / "chc_mopup_setup_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "chc_mopup_setup",
    "name": "CHC Mop-up Setup",
    "description": "Zero-pipeline opportunity picker: jumps straight into a CHC Mop-up Candidate Analysis "
    "dashboard dedicated to one LLO, without waiting for every opportunity's pipeline data to load.",
    "icon": "fa-list-check",
    "color": "orange",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
