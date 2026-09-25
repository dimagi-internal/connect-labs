"""KMC Opportunity Report: one opportunity's own report.

The programme report (`kmc_programme_metrics`) answers "how is the programme
doing", across a dozen opportunities, for the people who run the programme. This
is the same report for ONE opportunity, for the people who run that opportunity
-- its network manager -- plus where it sits among anonymous peers:

  * the programme report's headline tiles, weekly activity and trends across
    saved reports, for this opportunity;
  * ITS OWN FIELD WORKERS, a row each on the programme's scorecard columns, with
    their last visit and their cases;
  * where it sits among peers it is not allowed to name (/labs/benchmarks/).

Drawn with the shared report library (components/workflow/report), so it looks
like the programme report and cannot drift from it.

SAVED RUNS. A run is graded by the same `semantic_snapshot` builder as the
programme report, over this one opportunity, so the page opens on saved figures
instead of recomputing them. Runs arrive two ways:

  * HANDED DOWN. When the programme report saves a week, each opportunity report
    that follows it receives that opportunity's slice as a completed run
    (workflow/hand_down.py). The network manager never reads the programme report
    -- they may not have access to it -- and does not have to save anything.
  * SAVED HERE. A report whose programme report saves nothing (or none at all)
    saves its own weeks, exactly like the programme report does.

A live, unsaved view is still available as an in-progress run: its preview fills
this opportunity's visit cache itself (`warm_cache_on_read`).

THE DISCLOSURE LINE. Worker names come from this opportunity's own snapshot: an
opportunity owns its workers' data, so naming them to the people running it is
correct. Peer figures come only from the benchmark store, which is anonymous and
never carries a worker. A handed-down slice carries nothing of any other
opportunity.
"""

import copy
from pathlib import Path

from connect_labs.workflow.templates.kmc_programme_metrics import (
    CASE_PROPERTIES_SCHEMA,
    SCORECARD_COLUMNS,
    SCORECARD_GROUPS,
)
from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS as PROGRAMME_SNAPSHOT_INPUTS
from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_SCHEMA, WEIGHT_SERIES_SCHEMA

_RENDER = (Path(__file__).parent / "kmc_opp_report_render.js").read_text()

# The programme report's snapshot, over this workflow's one opportunity: the same
# builder, the same scopes and case index, so a handed-down slice and a run saved
# here are the same shape and the page reads both the same way.
SNAPSHOT_INPUTS = copy.deepcopy(PROGRAMME_SNAPSHOT_INPUTS)

DEFINITION = {
    "name": "KMC Opportunity Report",
    "description": (
        "One opportunity's own KMC report, saved weekly: the programme report's headline "
        "figures, activity and trends for this opportunity, a row per field worker on the "
        "programme's scorecard, and where it sits among anonymous peers. Worker figures are "
        "shown identified -- an opportunity owns its own workers' data; peer figures are "
        "anonymous and never carry a worker."
    ),
    "version": 1,
    "templateType": "kmc_opp_report",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "discharged", "label": "Discharged", "color": "blue"},
        {"id": "lost_to_followup", "label": "Lost to Follow-up", "color": "red"},
    ],
    "config": {
        # ONE opportunity. This is the whole point of the page: the scope it
        # reads is the scope it is allowed to read.
        "multi_opp": False,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "kmc_opp_report",
        # The page paints immediately and reads its own payload, so the framework
        # must not hold the render back for a pipeline stream...
        "renderWhileLoading": True,
        # ...and must not stream every pipeline row to the browser either. Every
        # figure here is graded server-side; the pipelines exist to fill the
        # visit cache a live preview reads, not to be shipped.
        "noPipelineStream": True,
        # ...so a live preview fills that cache itself (views.preview_snapshot_api,
        # and the semantic endpoint). Without it an in-progress run reads "no
        # cached visits" whenever nobody has opened the programme report lately.
        # One opportunity, so a cold load costs one opportunity's download. A
        # saved run reads its stored snapshot and needs no cache at all.
        "warm_cache_on_read": True,
        # The render's fallback for a measure that declares no min_denominator
        # of its own: the KMC registry's `defaults.min_denominator` (spec
        # section 0). The live endpoint does not carry the registry default,
        # so the render needs it here.
        "min_denominator_default": 20,
        # The programme report's scorecard columns and their group banner, so the
        # worker table reads column for column like the programme's.
        "scorecard_columns": SCORECARD_COLUMNS,
        "scorecard_groups": SCORECARD_GROUPS,
    },
    "pipeline_sources": [],
    "snapshot_inputs": SNAPSHOT_INPUTS,
}

TEMPLATE = {
    "key": "kmc_opp_report",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-hospital-user",
    "color": "green",
    "multi_opp": False,
    "supports_saved_runs": True,
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    # Takes the programme report's saved weeks, cut to this opportunity
    # (workflow/hand_down.py).
    "receives_hand_down": True,
    # Creation binds the new workflow to a LIVE registry record rather than the
    # on-disk copy, so an indicator edit reaches this page without a deploy.
    # This page computes its own numbers (the worker review reads someone
    # else's), so the binding is load-bearing here rather than incidental.
    "semantic_registry": "kmc",
    "definition": DEFINITION,
    "render_code": _RENDER,
    # THE SAME TWO SCHEMA OBJECTS the programme report declares — imported, not
    # copied. An instance created beside a programme report can be pointed at
    # that report's own pipeline records (`home_scope`) and share one warm
    # cache, which only holds while both sides name the same two aliases over
    # the same two schemas.
    "pipeline_schemas": [
        {"alias": "children", "name": "KMC Case Properties (SQL)", "schema": CASE_PROPERTIES_SCHEMA},
        {"alias": "visits", "name": "KMC Weight Series", "schema": WEIGHT_SERIES_SCHEMA},
    ],
}
