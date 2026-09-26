"""Indicator Opportunity Report: the generic programme report cut to one opportunity.

The page an opportunity's network manager opens: the programme report's headline
tiles, worker scorecard, activity and trends for THIS opportunity, plus a
Benchmarks tab placing its organisation among the programme's other organisations,
anonymously (/labs/benchmarks/). Same payload shape, same library, same registry
display contract as `indicator_programme_report`; nothing programme-specific in
page code.

RUNS arrive handed down from the programme report named by
`config.source_workflow_id` (workflow/hand_down.py) whenever it saves a week, or
are saved here. `benchmarks_create_opp_reports(template_key="indicator_opp_report",
source_workflow_id=<programme report>)` fans one out per cohort opportunity,
referencing the programme report's pipelines and registry and stamping the source.
"""

from pathlib import Path

from connect_labs.workflow.templates.indicator_programme_report import SNAPSHOT_INPUTS as _PROGRAMME_INPUTS
from connect_labs.workflow.templates.indicator_programme_report import SNAPSHOT_SCHEMA, VISITS_PIPELINE

_RENDER = (Path(__file__).parent / "indicator_report_render.js").read_text()

SNAPSHOT_INPUTS = dict(_PROGRAMME_INPUTS)

DEFINITION = {
    "name": "Indicator Opportunity Report",
    "description": (
        "One opportunity's own indicator report, saved weekly or handed down from its "
        "programme report: headline indicators, a row per field worker on the programme's "
        "scorecard, activity and trends, and a Benchmarks tab placing its organisation among "
        "the programme's others anonymously. Any semantic registry; no page code."
    ),
    "version": 1,
    "templateType": "indicator_opp_report",
    "statuses": [],
    "config": {
        "multi_opp": False,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "indicator_opp_report",
        "renderWhileLoading": True,
        "noPipelineStream": True,
        "warm_cache_on_read": True,
        "stale_after_days": 14,
        # The programme report whose saved weeks this receives.
        "source_workflow_id": None,
        # {workflow_id, run_id} of a worker review to open worker rows in. Optional:
        # without it a worker row expands to its cases in place.
        "worker_review": None,
    },
    "pipeline_sources": [],
    "snapshot_inputs": SNAPSHOT_INPUTS,
}

TEMPLATE = {
    "key": "indicator_opp_report",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-building-user",
    "color": "green",
    "multi_opp": False,
    "supports_saved_runs": True,
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "receives_hand_down": True,
    "semantic_registry": "visit_quality",
    "follow_template": True,
    "definition": DEFINITION,
    "render_code": _RENDER,
    "pipeline_schemas": [VISITS_PIPELINE],
}
