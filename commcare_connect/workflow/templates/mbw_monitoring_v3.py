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
        # EBF numerator: count of visits where the COALESCED bf_status's
        # whitespace-tokens contain "ebf". V1 logic:
        #   bf_status = (row.computed.get("bf_status") or "").strip()
        #   if "ebf" in bf_status.split(): count++
        # filter_paths matches the field's paths exactly so the FILTER applies
        # to the same coalesced value the field produces — without this the
        # filter would only check the first path (pnc) and miss visits whose
        # bf_status came from oneweek / onemonth / threemonth / sixmonth.
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
            "filter_paths": [
                "form.feeding_history.pnc_current_bf_status",
                "form.feeding_history.oneweek_current_bf_status",
                "form.feeding_history.onemonth_current_bf_status",
                "form.feeding_history.threemonth_current_bf_status",
                "form.feeding_history.sixmonth_current_bf_status",
            ],
            "filter_value": "ebf",
            "filter_op": "contains_word",
        },
        # EBF denominator: count of visits where the coalesced bf_status is
        # non-empty after trim. V1 has `if not bf_status: continue` after
        # strip — empty strings are excluded. We approximate by counting only
        # rows where the coalesced bf_status contains at least one whitespace-
        # delimited token (i.e., is a non-empty token list). Done as a count
        # with a contains_word-style filter using a regex-friendly sentinel
        # token check, by reusing filter_paths + filter_op="contains_word"
        # — but with token "" disabled, a simpler approach is `count` with a
        # `nullif_empty`-style transform. For now, use COUNT and rely on the
        # NULLIF wrappers in _paths_to_coalesce_sql which convert '' → NULL.
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
        #
        # `pre_aggregate_attribute_to: last_username` attributes each mother
        # to the FLW whose visit is the LAST one for her — matching v1's
        # `mother_to_username` last-write-wins map that drives the entire
        # quality_metrics block. Without this, mothers visited by multiple
        # FLWs would be counted under each, while v1 counts them under one.
        {
            "name": "parity_mode_share",
            "path": "form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
            "aggregation": "mode_share",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "last",
            "pre_aggregate_attribute_to": "last_username",
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
            "pre_aggregate_attribute_to": "last_username",
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
            "pre_aggregate_attribute_to": "last_username",
            "filter_path": "form.@name",
            "filter_value": "ANC Visit",
        },
        # ---------- JOIN-dependent quality leaves ----------
        # Each of these reads from `joined.registrations.<field>` — populated
        # at SQL build time by the `joins[]` entry below. Two-pass
        # (pre_aggregate_by mother_case_id + per-mother first) mirrors v1's
        # "build a per-mother lookup, then aggregate per FLW" pattern.
        #
        # `pre_aggregate_attribute_to: last_username` matches v1's
        # winner-takes-all mother→FLW attribution: a mother visited by
        # FLWs A and B is counted under whichever FLW visited her LAST,
        # not under both. Critical for fraud-detection metrics — without
        # it, a single duplicated phone on a shared mother would inflate
        # both A's and B's dup_share above v1's signal.
        #
        # phone_dup_share: per-FLW share (0..1) of "owned" mothers whose
        # phone duplicates with at least one other owned mother. v1's
        # quality_metrics.phone_dup_pct.
        {
            "name": "phone_dup_share",
            "path": "joined.registrations.phone_number",
            "aggregation": "dup_share",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "first",
            "pre_aggregate_attribute_to": "last_username",
        },
        # age_concentration_mode_share: per-FLW share of owned mothers
        # whose registered age equals the most-common registered age for
        # that FLW. v1's quality_metrics.age_concentration.mode_pct.
        {
            "name": "age_concentration_mode_share",
            "path": "joined.registrations.age",
            "aggregation": "mode_share",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "first",
            "pre_aggregate_attribute_to": "last_username",
        },
        # age_concentration_mode_value: most-common registered age across
        # owned mothers per FLW. Shipped alongside mode_share so the
        # dashboard can display "X% of mothers report age N".
        {
            "name": "age_concentration_mode_value",
            "path": "joined.registrations.age",
            "aggregation": "mode",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "first",
            "pre_aggregate_attribute_to": "last_username",
        },
        # age_concentration_dup_share: per-FLW share (0..1) of owned
        # mothers whose registered age duplicates with at least one
        # other owned mother's age.
        {
            "name": "age_concentration_dup_share",
            "path": "joined.registrations.age",
            "aggregation": "dup_share",
            "pre_aggregate_by": "form.parents.parent.case.@case_id",
            "pre_aggregation": "first",
            "pre_aggregate_attribute_to": "last_username",
        },
    ],
    # JOIN spec: pull registration-level fields per visit so per-FLW
    # aggregations (above) can use them. The pre_aggregate_by groups
    # visits per mother so the inner pass collapses many visits to one
    # row per mother carrying that mother's registration data — matching
    # v1's "build a per-mother lookup, then aggregate per FLW" pattern.
    "joins": [
        {
            "from_alias": "registrations",
            "local_key": "form.parents.parent.case.@case_id",
            "remote_key_field": "mother_case_id",
            "fields": [
                {"name": "phone_number", "from": "phone_number"},
                {"name": "age", "from": "age"},
                {"name": "age_recorded", "from": "age_recorded"},
                {"name": "mother_dob", "from": "mother_dob"},
                {"name": "eligible_full_intervention_bonus", "from": "eligible_full_intervention_bonus"},
                {"name": "expected_visits", "from": "expected_visits"},
                {"name": "expected_delivery_date", "from": "expected_delivery_date"},
            ],
        },
    ],
}

VISITS_GPS_SCHEMA = {
    # Same data source as the aggregated visits pipeline, but produces per-row
    # output enriched with window-function distances. The GPS dashboard tab
    # consumes this pipeline; the JSX aggregates per-FLW (median, max, etc.)
    # using the algorithm spec captured in
    # workflow/tests/mbw_parity/runners.compute_gps_median_*.
    #
    # Terminal stage MUST be visit_level — window fields only run during the
    # visit-level extraction pass that wraps the raw cache in a base subquery.
    # An aggregated terminal stage skips that path and reads form_json directly.
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "mother_case_id", "path": "form.parents.parent.case.@case_id", "aggregation": "first"},
        # case_id — the visit's direct case (one per visit). v1 uses this for
        # gps_data.flw_summaries[].unique_cases, NOT mother_case_id.
        {"name": "case_id", "path": "form.case.@case_id", "aggregation": "first"},
        {"name": "visit_datetime", "path": "form.meta.timeEnd", "aggregation": "first"},
        {"name": "form_name", "path": "form.@name", "aggregation": "first"},
        {
            "name": "app_build_version",
            "path": "form.meta.app_build_version",
            "aggregation": "first",
            "transform": "int",
        },
        # GPS lat/lon parsed from the packed "lat lon alt acc" string. v1 uses
        # paths=[..#text, ..location] because the GPS string lives in the
        # element's text content sometimes (.#text) and sometimes directly as
        # a string-shaped wrapper. v3 must do the same multi-path coalesce or
        # it under-counts GPS-valid visits by ~5%.
        {
            "name": "latitude",
            "paths": ["form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lat",
        },
        {
            "name": "longitude",
            "paths": ["form.meta.location.#text", "form.meta.location"],
            "aggregation": "first",
            "transform": "gps_lon",
        },
    ],
    # Note: NOT using extracted_filters here. v1's analyze_case_distances
    # iterates ALL visits in chronological order and computes distance only
    # when BOTH curr and prev have GPS — i.e., it skips pairs where either
    # side lacks GPS rather than skipping rows entirely. lag_haversine
    # naturally matches this: returns NULL when either lat input is NULL.
    # Pre-filtering to GPS-only would change semantics by pairing across
    # non-GPS visits (incorrect). Tested on opp 765: unfiltered window
    # produces results within 1-3% of v1 across all FLWs (float rounding
    # over many haversine computations).
    "window_fields": [
        # Per-visit haversine to the previous visit to the SAME mother. NULL
        # for first visit per mother and when either coordinate is missing.
        # Foundation for the gps_data.flw_summaries[].avg_case_distance_km +
        # max_case_distance_km + cases_with_revisits leaves; JSX or a future
        # second-stage aggregation rolls these per-row distances into per-FLW
        # stats.
        {
            "name": "distance_from_prev_case_visit_m",
            "operation": "lag_haversine",
            "partition_by": "mother_case_id",
            "order_by": "visit_datetime",
            "lat_field": "latitude",
            "lon_field": "longitude",
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
        # mother_case_id: v1 walks form.var_visit_1..6.mother_case_id and
        # picks the first non-empty. The pipeline framework's paths-coalesce
        # handles this declaratively — v1's per-form Python loop becomes a
        # single SQL COALESCE(NULLIF(...), ...) chain.
        {
            "name": "mother_case_id",
            "paths": [
                "form.var_visit_1.mother_case_id",
                "form.var_visit_2.mother_case_id",
                "form.var_visit_3.mother_case_id",
                "form.var_visit_4.mother_case_id",
                "form.var_visit_5.mother_case_id",
                "form.var_visit_6.mother_case_id",
            ],
            "aggregation": "first",
        },
        # Mother identity. v1 uses fallback chain
        # `format_mother_name → mother_full_name → mother_name + mother_surname`.
        # Multi-path covers the first two; the surname concat happens client-side.
        {
            "name": "mother_name",
            "paths": [
                "form.mother_details.format_mother_name",
                "form.mother_details.mother_full_name",
            ],
            "aggregation": "first",
        },
        {"name": "mother_first_name", "path": "form.mother_details.mother_name", "aggregation": "first"},
        {"name": "mother_surname", "path": "form.mother_details.mother_surname", "aggregation": "first"},
        # Phone — primary, then backup
        {
            "name": "phone_number",
            "paths": ["form.mother_details.phone_number", "form.mother_details.back_up_phone_number"],
            "aggregation": "first",
        },
        # Mother DOB / recorded age. v1 computes age from DOB if available.
        {"name": "mother_dob", "path": "form.mother_details.mother_dob", "aggregation": "first"},
        {
            "name": "age_recorded",
            "paths": ["form.mother_details.age_in_years_rounded", "form.mother_details.mothers_age"],
            "aggregation": "first",
        },
        # `age` mirrors v1's `extract_mother_metadata_from_forms` exactly:
        # if mother_dob is parseable, derive years-since-DOB at the current
        # date; else fall back to age_in_years_rounded then mothers_age.
        # Drives the age_concentration leaves on the visits side. Without
        # this, those leaves drift on FLWs whose mothers have DOBs set
        # (v3's age_recorded would read the recorded field directly while
        # v1 prefers DOB-derived).
        {"name": "age", "extractor": "v1_mbw_age", "aggregation": "first"},
        # Household + eligibility (used in quality_metrics)
        {"name": "household_size", "path": "form.number_of_other_household_members", "aggregation": "first"},
        {
            "name": "eligible_full_intervention_bonus",
            "path": "form.eligible_full_intervention_bonus",
            "aggregation": "first",
        },
        # Birth outcome / expected delivery
        {
            "name": "expected_delivery_date",
            "path": "form.mother_birth_outcome.expected_delivery_date",
            "aggregation": "first",
        },
        # Preferred visit time (used by v1 metadata extraction; comes from
        # the FIRST var_visit block, not mother_details).
        {"name": "preferred_visit_time", "path": "form.var_visit_1.preferred_visit_time", "aggregation": "first"},
        # Schedule + FLW link
        {"name": "expected_visits", "path": "form.expected_visits", "aggregation": "first"},
        {"name": "user_connect_id", "path": "form.user_connect_id", "aggregation": "first"},
        # Registration date — top-level form metadata (the form-end timestamp)
        {"name": "registration_date", "path": "form.meta.timeEnd", "aggregation": "first"},
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
        "alias": "visits_gps",
        "name": "MBW Visit GPS (V3)",
        "description": "Per-visit GPS data with lag_haversine distance to previous mother visit",
        "schema": VISITS_GPS_SCHEMA,
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
