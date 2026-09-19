"""KMC Opportunity Report: one opportunity's own report.

The programme report (`kmc_programme_metrics`) answers "how is the programme
doing", across twelve opportunities, for the people who run the programme. This
answers a different question for a different reader: an LLO opening ITS OWN
opportunity and wanting three things at once —

  1  its own figures, on the same indicators the programme is judged on,
  2  ITS OWN FIELD WORKERS, a row each, a column per indicator, banded,
  3  where it sits among peers it is not allowed to name.

WHY THIS IS NOT A VIEW ON THE PROGRAMME REPORT. The programme report is
multi-opp and its drill goes programme -> LLO -> opportunity -> worker; reaching
a worker means holding the whole cohort's payload, which is exactly what a
single partner must not be handed. This page reads ONE opportunity's scope and
nothing else, and the only cross-opportunity figures it can reach are the
already-anonymised ones the benchmark store serves.

THE DISCLOSURE LINE RUNS THROUGH THE MIDDLE OF THIS PAGE, and it is the reason
sections 2 and 3 have different sources:

  section 2  real usernames, read straight from the semantic layer. An
             opportunity owns its own workers' data, so naming them to the
             people running it is correct and deliberate.
  section 3  anonymous peers, read from /labs/benchmarks/. That store exists to
             CROSS an opportunity boundary, and FLW identity must never cross
             one — so nothing in the worker table is sourced from it, and the
             render's tests pin that.

Two shapes in the data constrain the design and are not worth rediscovering:

  * There is no per-FLW monthly series anywhere. `monthlyByScope` carries `all`,
    `llo:<name>` and `opp:<id>`. The worker table is point-in-time and says so
    on the page; a per-worker trend line would have to be invented.
  * Only the C family has monthly trends at all — `monthlyByScope` is graded
    with the primary catalog only — so the N scorecard is point-in-time
    everywhere, including in a published benchmark's series.

NO SAVED RUNS. A drill view has no moment of completion: it is opened, read and
closed. The thing that DOES have one is the programme report, and it is the
programme report's completed run that a benchmark is published from.
"""

from pathlib import Path

from connect_labs.workflow.templates.kmc_programme_metrics import CASE_PROPERTIES_SCHEMA
from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_INPUTS as PROGRAMME_SNAPSHOT_INPUTS
from connect_labs.workflow.templates.kmc_programme_metrics import WEIGHT_SERIES_SCHEMA

_RENDER = (Path(__file__).parent / "kmc_opp_report_render.js").read_text()

DEFINITION = {
    "name": "KMC Opportunity Report",
    "description": (
        "One opportunity's own KMC report: its indicator scorecard, a row per field worker "
        "with a column per indicator, and anonymous peer bars from its benchmark cohorts. "
        "Worker figures are read directly and shown identified — an opportunity owns its own "
        "workers' data; peer figures are anonymous and never carry a worker."
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
        # The page paints immediately and fetches its own three payloads, so the
        # framework must not hold the render back for a pipeline stream...
        "renderWhileLoading": True,
        # ...and must not stream every pipeline row to the browser either. Every
        # figure here is computed server-side by the semantic endpoint; the
        # pipelines exist to fill the visit cache it reads, not to be shipped.
        "noPipelineStream": True,
        # ...so it fills the visit cache itself. The semantic endpoint only READS
        # the cache, and the cache expires; without this the page reads "no cached
        # visits" whenever nobody has opened the programme report lately. One
        # opportunity, so a cold load costs one opportunity's download.
        "warm_cache_on_read": True,
        # The render's fallback for a measure that declares no min_denominator
        # of its own, matching the programme report's `var MIN_DEN = 25`.
        "min_denominator_default": 25,
        # WHICH INDICATORS ARE GATED ON RECORDING CREDIBILITY, taken from the
        # programme report's own credibility map so there is ONE copy of the
        # fact in the repo, and carried on the definition so it is patchable
        # through `workflow_update_definition` with no deploy.
        #
        # Two mechanisms reach the render, covering different indicators:
        #
        #   * the registry's own `suppression:` rules compile to a
        #     `<measure>_suppressed` column that the live semantic endpoint
        #     returns on every row. Today that is C14, and only in the C series
        #     -- `filter_to_series` drops the C measures, and with them the
        #     rule's target, when the N scorecard is asked for.
        #   * this list, which is what the render falls back to when a gated
        #     indicator arrives with no flag. It cannot say WHETHER the figure
        #     is credible, only that nothing established it -- so the render
        #     withholds rather than bands. Today that bites N13, the scorecard's
        #     mortality metric, which is C14 under another name.
        #
        # C18 and C22 are in the map and are not computed by this registry at
        # all; they are carried so the two surfaces cannot drift if they are.
        "credibility_gated_indicators": sorted(PROGRAMME_SNAPSHOT_INPUTS["credibility"]),
    },
    "pipeline_sources": [],
}

TEMPLATE = {
    "key": "kmc_opp_report",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-hospital-user",
    "color": "green",
    "multi_opp": False,
    # A drill view, not a periodic report: there is no moment of completion.
    "supports_saved_runs": False,
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
