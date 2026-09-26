"""Indicator Worker Review: the generic programme report's drill page.

Opened from a worker row on `indicator_programme_report` (its companion) or on an
`indicator_opp_report`: the worker's indicators on the programme's scorecard,
their cases, and each case's visit timeline. If the registry declares a reading
series (`display.reading`, or its weight series), each case's readings are charted;
if the visit rows carry images, they are shown beside each visit.

It computes NOTHING. The figures are the source report's own saved (or previewed)
payload -- `?source_run=<run_id>`, or the newest saved run of
`config.source_workflow_id` -- and the visits come from the shared pipelines
through the `pipeline-rows` endpoint, one case at a time.
"""

from pathlib import Path

from connect_labs.workflow.templates.indicator_programme_report import VISITS_PIPELINE

_RENDER = (Path(__file__).parent / "indicator_worker_review_render.js").read_text()

DEFINITION = {
    "name": "Indicator Worker Review",
    "description": (
        "One worker from an indicator programme report: their indicators, their cases and "
        "each case's visits, with the registry's reading series charted where it declares "
        "one. Opened from a worker row; reads the report's saved figures."
    ),
    "version": 1,
    "templateType": "indicator_worker_review",
    "statuses": [],
    "config": {
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "indicator_worker_review",
        "renderWhileLoading": True,
        "noPipelineStream": True,
        # The report this reads when opened without ?source_run=. Stamped at create
        # when made as a companion.
        "source_workflow_id": None,
    },
    "pipeline_sources": [],
}

TEMPLATE = {
    "key": "indicator_worker_review",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-user-check",
    "color": "teal",
    "multi_opp": True,
    "supports_saved_runs": False,
    "follow_template": True,
    "definition": DEFINITION,
    "render_code": _RENDER,
    "pipeline_schemas": [VISITS_PIPELINE],
}
