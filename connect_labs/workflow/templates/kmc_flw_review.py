"""KMC Worker Review: the programme drill's second workflow.

The programme dashboard drills programme -> LLO -> opportunity -> worker, and it
must stop there or become the everything-page. This is where a worker's row
opens: their indicators and scorecard, their cases, a growth chart per case, and
an audit of their recent images.

It computes NOTHING. The figures are the programme report's own rows for that
worker, read from the run the page was opened from (`?source_run=<run_id>`)
through the same preview endpoint the programme page reads -- a saved report
returns its stored snapshot, a live one its preview -- or, opened on its own, the
newest saved programme report (`config.source_workflow_id`). What it adds is the
per-case weight series from the two pipelines both workflows share.

Linking is configuration, not code: the programme workflow's
`config.flw_review = {workflow_id, run_id}` names the review workflow and its
long-lived run, and each worker row becomes a link carrying `flw` and
`source_run`. No run is created per click.
"""

from pathlib import Path

from connect_labs.workflow.templates.kmc_programme_metrics import (
    CASE_PROPERTIES_SCHEMA,
    SCALE_AGENT_BY_LLO,
    UNVERIFIED_SCALE_LLOS,
    WEIGHT_IMAGE_PATH,
    WEIGHT_SERIES_SCHEMA,
    WEIGHT_VALUE_PATH,
)

_RENDER = (Path(__file__).parent / "kmc_flw_review_render.js").read_text()

DEFINITION = {
    "name": "KMC Worker Review",
    "description": (
        "One worker from the KMC programme report: their indicators and scorecard, their "
        "cases with a growth chart per case, and an audit of their recent images. Opened "
        "from a worker row on the programme report."
    ),
    "version": 1,
    "templateType": "kmc_flw_review",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "discharged", "label": "Discharged", "color": "blue"},
        {"id": "lost_to_followup", "label": "Lost to Follow-up", "color": "red"},
    ],
    "config": {
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "kmc_flw_review",
        # The headline comes from the saved report, not the pipelines, so the page
        # renders at once and the runner shows the pipeline stream's progress as a
        # strip; only the weighings and photos wait for it. The render tolerates
        # `pipelines[alias]` being absent until then.
        "renderWhileLoading": True,
        # The programme workflow whose report this reads when opened without a
        # `source_run`. Set on the instance (workflow_update_definition).
        "source_workflow_id": None,
        # The audit action, routed exactly as the programme page routes it.
        "audit_enabled": True,
        "weight_image_path": WEIGHT_IMAGE_PATH,
        "weight_value_path": WEIGHT_VALUE_PATH,
        "scale_agent_by_llo": SCALE_AGENT_BY_LLO,
        "scale_unverified_llos": sorted(UNVERIFIED_SCALE_LLOS),
        "audit_count_per_flw": 25,
        # "Recent" is this many days back from the worker's latest visit.
        "audit_recent_days": 60,
        # Restrict the audit to visits that carry a weight photo. Off by default:
        # a cohort with no photos would otherwise audit nothing.
        "audit_images_only": False,
    },
    "pipeline_sources": [],
}

TEMPLATE = {
    "key": "kmc_flw_review",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-user-nurse",
    "color": "teal",
    "multi_opp": True,
    # A drill view, not a periodic report: there is no moment of completion.
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": _RENDER,
    # The same two pipelines as the programme report, so an instance created
    # beside one shares its cache; an instance may also be pointed at the
    # programme workflow's own pipeline records.
    "pipeline_schemas": [
        {"alias": "children", "name": "KMC Case Properties (SQL)", "schema": CASE_PROPERTIES_SCHEMA},
        {"alias": "visits", "name": "KMC Weight Series", "schema": WEIGHT_SERIES_SCHEMA},
    ],
}
