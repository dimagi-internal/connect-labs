"""MBW Monitoring V3 — pipeline-native rewrite (parity-tested against v1).

V3 ships side-by-side with v1 and v2. V1 (templates/mbw_monitoring/) and v2
(mbw_monitoring_v2 + job_handlers/mbw_monitoring) are frozen — no edits, no
deletions — until v3 has been proven against v1's dashboard payloads via the
parity harness in tests/mbw_parity/. Once v3 holds parity in production for
two clean weeks, v1 and v2 are removed.

What v3 does differently:
- Every dashboard metric expressible as an aggregation lives in PIPELINE_SCHEMAS
  rather than in a Python job handler. The pipeline framework executes them as
  SQL against labs_computed_visit_cache.
- The job handler (job_handlers/mbw_monitoring.py) goes away once v3 covers
  every step it currently performs (mother counts, EBF%, follow-up, GPS, etc.).
- Cross-pipeline JOINs (planned PR #3), chained stages (PR #4), and window-
  function GPS (PR #5) replace the remaining job-handler logic.

This file is the scaffold: it declares the Connect/CCHQ pipelines and the
aggregations needed for the dashboard's overview block. The render code is
intentionally minimal — full UI lands once parity holds across all dashboard
sections. Until then, the template is loadable and the pipeline schemas can
be exercised directly via the analysis pipeline (which is what the parity
harness does).
"""

from pathlib import Path

# Default Gold Standard supervisor app — same value as v2 since the form is
# the same. State-driven per-run override is a known gap, tracked alongside v2.
DEFAULT_GS_APP_ID = "2ca67a89dd8a2209d75ed5599b45a5d1"

DEFINITION = {
    "name": "MBW Monitoring V3",
    "description": "Pipeline-native MBW monitoring (parity-tested against v1)",
    "version": 1,
    "templateType": "mbw_monitoring_v3",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        "showSummaryCards": False,
        "showFilters": False,
        # No job_type — all metrics are computed declaratively in the pipelines.
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------
#
# Three pipelines mirror v2's data inputs. The fields list is what differs:
# v2 extracts raw values and lets the job handler aggregate; v3 extracts AND
# aggregates declaratively. As JOIN, chained-stages, and window primitives
# land, more metrics move into the pipelines and the JSX shrinks.
#
# Aggregation choices below correspond directly to the in-memory v3 reference
# in tests/mbw_parity/runners.py::compute_v3_overview, which the parity
# harness gates against the v1 reference. If a schema field changes, update
# both at once.

VISITS_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        # Carry-through fields used by both pipeline aggregations and JSX.
        # `first` semantics let us preserve a representative value per FLW
        # without paying for full row materialization.
        {"name": "form_name", "path": "form.@name", "aggregation": "first"},
        {"name": "visit_datetime", "path": "form.meta.timeEnd", "aggregation": "first"},
        {
            "name": "mother_case_id",
            "path": "form.parents.parent.case.@case_id",
            "aggregation": "first",
        },
        # ---------- Overview-tab aggregations (PR #2) ----------
        # mother_counts{} — distinct mother_case_ids per FLW.
        {
            "name": "mother_count",
            "path": "form.parents.parent.case.@case_id",
            "aggregation": "count_unique",
        },
        # EBF numerator: count of visits where bf_status's whitespace-tokens
        # contain "ebf". V1 logic: `if "ebf" in bf_status.split()`.
        {
            "name": "ebf_count",
            "paths": [
                "form.feeding_history.pnc_current_bf_status",
                "form.feeding_history.oneweek_current_bf_status",
                "form.feeding_history.onemonth_current_bf_status",
                "form.feeding_history.threemonth_current_bf_status",
                "form.feeding_history.sixmonth_current_bf_status",
            ],
            "aggregation": "count",
            "filter_path": "form.feeding_history.pnc_current_bf_status",
            "filter_value": "ebf",
            "filter_op": "contains_word",
        },
        # EBF denominator: count of visits with non-empty bf_status. The
        # `count` aggregation already excludes NULL; the JSX divides numerator
        # by denominator and rounds.
        {
            "name": "bf_status_count",
            "paths": [
                "form.feeding_history.pnc_current_bf_status",
                "form.feeding_history.oneweek_current_bf_status",
                "form.feeding_history.onemonth_current_bf_status",
                "form.feeding_history.threemonth_current_bf_status",
                "form.feeding_history.sixmonth_current_bf_status",
            ],
            "aggregation": "count",
        },
        # ---------- Quality-tab aggregations (PR #3 partial slice) ----------
        # parity_mode_share: per-FLW concentration of parity. Two-pass:
        # collapse rows to one parity per mother (v1 overwrites in iteration
        # order, so `last` matches), then mode_share over the per-mother
        # parities. 1.0 = every mother reports identical parity (suspicious).
        # Filter on form_name="ANC Visit" mirrors v1's `if form_name == "ANC Visit"`
        # — only ANC visits report parity in the MBW data model.
        {
            "name": "parity_mode_share",
            "path": "form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
            "aggregation": "mode_share",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "last",
            "filter_path": "form.@name",
            "filter_value": "ANC Visit",
        },
        # parity_mode_value: per-FLW most-common parity (the "mode_value" that
        # accompanies mode_pct in v1's quality_concentration dict).
        {
            "name": "parity_mode_value",
            "path": "form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
            "aggregation": "mode",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "last",
            "filter_path": "form.@name",
            "filter_value": "ANC Visit",
        },
        # parity_dup_share: per-FLW share (0..1) of mothers whose parity matches
        # at least one other mother's parity for the same FLW. v1's
        # `pct_duplicate` — high values mean the FLW reports lots of repeated
        # parities across mothers, even if no single value dominates.
        {
            "name": "parity_dup_share",
            "path": "form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
            "aggregation": "dup_share",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "last",
            "filter_path": "form.@name",
            "filter_value": "ANC Visit",
        },
    ],
}

REGISTRATIONS_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Register Mother",
        "app_id_source": "opportunity",
    },
    "grouping_key": "case_id",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "expected_visits", "path": "form.expected_visits", "aggregation": "first"},
        {"name": "mother_name", "path": "form.mother_name", "aggregation": "first"},
        {"name": "user_connect_id", "path": "form.user_connect_id", "aggregation": "first"},
    ],
}

GS_FORMS_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Gold Standard Visit Checklist",
        "app_id_source": "opportunity",
        "gs_app_id": DEFAULT_GS_APP_ID,
    },
    "grouping_key": "case_id",
    "terminal_stage": "visit_level",
    "fields": [
        {
            "name": "gs_score",
            "paths": ["form.gs_score", "form.checklist_percentage"],
            "aggregation": "first",
        },
        {"name": "assessor_name", "path": "form.assessor_name", "aggregation": "first"},
        {"name": "assessment_date", "path": "form.meta.timeEnd", "aggregation": "first"},
        {
            "name": "user_connect_id",
            "paths": ["form.user_connect_id", "form.load_flw_connect_id"],
            "aggregation": "first",
        },
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "visits",
        "name": "MBW Visit Forms (V3)",
        "description": "Connect visits — pipeline-native aggregations",
        "schema": VISITS_SCHEMA,
    },
    {
        "alias": "registrations",
        "name": "CCHQ Registration Forms (V3)",
        "description": "CCHQ registration forms for mother data",
        "schema": REGISTRATIONS_SCHEMA,
    },
    {
        "alias": "gs_forms",
        "name": "CCHQ Gold Standard Forms (V3)",
        "description": "CCHQ Gold Standard visit checklist forms",
        "schema": GS_FORMS_SCHEMA,
    },
]


# Minimal render code — full UI builds out as parity coverage grows.
# Until then, this template is "loadable but nobody clicks it" — its value
# is that the PIPELINE_SCHEMAS execute via the analysis pipeline and the
# parity harness gates them against v1.
RENDER_CODE_PATH = Path(__file__).parent / "mbw_monitoring_v3_render.js"
if RENDER_CODE_PATH.exists():
    RENDER_CODE = RENDER_CODE_PATH.read_text(encoding="utf-8")
else:
    RENDER_CODE = """
function WorkflowUI({ definition, instance, workers, pipelines, links, actions, onUpdateState }) {
  // V3 scaffold render. The full UI lands once parity holds across every
  // dashboard section. Until then, this just shows the pipeline aggregation
  // results that the parity harness validates.
  var visitsRows = pipelines?.visits?.rows || [];
  var motherCounts = {};
  var ebfNum = {};
  var ebfDen = {};
  visitsRows.forEach(function (r) {
    var u = r.username;
    if (typeof r.mother_count === 'number') motherCounts[u] = r.mother_count;
    if (typeof r.ebf_count === 'number') ebfNum[u] = r.ebf_count;
    if (typeof r.bf_status_count === 'number') ebfDen[u] = r.bf_status_count;
  });
  var ebfPct = {};
  Object.keys(ebfDen).forEach(function (u) {
    if (ebfDen[u] > 0) ebfPct[u] = Math.round((ebfNum[u] || 0) / ebfDen[u] * 100);
  });

  return React.createElement(
    'div',
    { className: 'p-4' },
    React.createElement('h2', { className: 'text-lg font-bold mb-2' }, 'MBW V3 (scaffold)'),
    React.createElement('p', { className: 'text-sm text-gray-600 mb-4' },
      'Pipeline-native rewrite, parity-gated against V1. Full UI in a future PR.'),
    React.createElement('div', { className: 'space-y-2' },
      React.createElement('div', null,
        React.createElement('strong', null, 'Mother counts: '),
        JSON.stringify(motherCounts)
      ),
      React.createElement('div', null,
        React.createElement('strong', null, 'EBF %: '),
        JSON.stringify(ebfPct)
      ),
      React.createElement('div', null,
        React.createElement('strong', null, 'Total visits: '),
        (pipelines?.visits?.metadata?.row_count || 0).toString()
      ),
      React.createElement('div', null,
        React.createElement('strong', null, 'Total registrations: '),
        (pipelines?.registrations?.metadata?.row_count || 0).toString()
      )
    )
  );
}
""".strip()


TEMPLATE = {
    "key": "mbw_monitoring_v3",
    "name": "MBW Monitoring V3",
    "description": "Pipeline-native MBW monitoring (parity-tested; v1 stays canonical until v3 proven)",
    "icon": "fa-baby",
    "color": "pink",
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
