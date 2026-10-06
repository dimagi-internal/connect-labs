"""Indicator Programme Report: the KMC programme report, for ANY registry.

`kmc_programme_metrics` drills programme -> organisation -> opportunity -> worker
-> case over the KMC registry, and holds its presentation in page code: five
hand-picked headline tiles, a fifteen-column scorecard, "babies" throughout. This
template is the same cascade with nothing programme-specific in it. Everything a
reader sees comes from the workflow's bound semantic registry:

  * the indicators, their bands and gates           (measures, as for KMC)
  * headline tiles, targets, labels, order          (`meta.headline`, `meta.target`,
                                                    `meta.label`, `meta.order`)
  * the scorecard columns and their category groups (`meta.scorecard`, `category`,
                                                    `display.categories`)
  * nouns -- "baby", "community", "beneficiary"     (`display.entity/worker/organisation`)
  * the organisation level                          (`deployment.llo_map`)
  * the case table's columns                        (`display.case_fields`)

See `semantic/display.py` for every key and its default, and
`user_docs/semantic-layer.md` for how to author them.

THE CASCADE, one Create:
  * this report -- saved weekly runs graded by the `semantic_snapshot` builder;
  * its COMPANION, `indicator_worker_review`, on the same pipeline records and the
    same registry record, with a long-lived run; worker rows open it;
  * each saved run HANDED DOWN to every `indicator_opp_report` naming this report
    (`config.source_workflow_id`) -- `benchmarks_create_opp_reports` makes those.

Every instance FOLLOWS the deployed template (`follow_template`): render and config
come from here, so a deploy updates all of them. The snapshot spec states only the
builder; the builder derives scopes, case index, visit pipeline and credibility
from the registry (`snapshot_builders.resolve_spec_defaults`).

PIPELINES. By default one visit-level pipeline, `visits`, over the columns every
Connect visit carries -- enough for any registry that counts over them (the on-disk
`visit_quality` seed). A registry that reads form fields names its own pipelines
(`properties.pipelines`); create with `pipelines_from` to reference existing
pipeline records, or edit the created `visits` pipeline's schema.
"""

from pathlib import Path

from connect_labs.workflow.templates.kmc_programme_metrics import SNAPSHOT_SCHEMA as _KMC_SNAPSHOT_SCHEMA

_RENDER = (Path(__file__).parent / "indicator_report_render.js").read_text()

# The whole spec. Everything registry-shaped is derived at build time from the
# bound registry, so one template serves every programme -- and an instance may
# still state any key (via workflow_update_definition) to override the derivation.
SNAPSHOT_INPUTS = {"builder": "semantic_snapshot", "workers": False}

SNAPSHOT_SCHEMA = {
    "version": _KMC_SNAPSHOT_SCHEMA["version"],
    "keys": {
        **_KMC_SNAPSHOT_SCHEMA["keys"],
        "state.snapshot.display": (
            "How the report reads, resolved from the registry when the run was built "
            "(semantic/display.py): entity / worker / organisation nouns, headline indicator "
            "ids in tile order, category order, per-indicator label / plain / target / order / "
            "scorecard / credibility, the case table's columns, the reading series to chart, "
            "and the visit pipeline alias"
        ),
    },
}

# One visit-level pipeline over Connect's base visit columns (entity_id, username,
# visit_date, status, flagged, ...). No form paths: a registry needing them brings
# its own pipelines. The alias is the one the visit_quality seed names.
VISITS_SCHEMA = {
    "fields": [],
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
}
VISITS_PIPELINE = {"alias": "visits", "name": "Visits (base columns)", "schema": VISITS_SCHEMA}

DEFINITION = {
    "name": "Indicator Programme Report",
    "description": (
        "A programme's indicator report, one page per saved weekly run, for any semantic "
        "registry: headline indicators with the change since the last report and their "
        "targets, a scorecard by organisation grouped by indicator category, workers with "
        "peer cohorts, activity by week, indicator trends across saved reports and the "
        "definition behind every number. Drills programme, organisation, opportunity, "
        "worker, case; a worker opens the Indicator Worker Review. Everything programme-"
        "specific comes from the bound registry, none from page code."
    ),
    "version": 1,
    "templateType": "indicator_programme_report",
    "statuses": [],
    "config": {
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": False,
        "templateType": "indicator_programme_report",
        # Every figure is graded server-side into one payload; the page streams no
        # pipeline rows, paints at once, and a live preview fills a cold cache.
        "renderWhileLoading": True,
        "noPipelineStream": True,
        "warm_cache_on_read": True,
        # Days without a visit before a row's last-visit cell reads stale.
        "stale_after_days": 14,
        # {workflow_id, run_id} of the companion worker review: written at create.
        "worker_review": None,
    },
    "pipeline_sources": [],
    "snapshot_inputs": SNAPSHOT_INPUTS,
}

TEMPLATE = {
    "key": "indicator_programme_report",
    "name": DEFINITION["name"],
    "description": DEFINITION["description"],
    "icon": "fa-chart-column",
    "color": "indigo",
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "hands_down_to_opportunity_reports": True,
    # Seeded from the on-disk visit_quality registry when no record is named, so a
    # bare Create renders; name a record (registry_source) for a real programme.
    "semantic_registry": "visit_quality",
    "follow_template": True,
    "definition": DEFINITION,
    "render_code": _RENDER,
    "pipeline_schemas": [VISITS_PIPELINE],
    "companions": [
        {
            "template_key": "indicator_worker_review",
            "config_key": "worker_review",
            "share_pipelines": True,
            "mint_run": True,
            "back_reference": "source_workflow_id",
        }
    ],
}
