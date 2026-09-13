"""MBW Auditing V6 — SQL-native audit workflow, mother/FLW rollups computed in SQL.

v5 pushed compute out of the Python job handler and into JSX (`useMemo` maps
over raw pipeline rows) — see mbw_auditing_v5.py. That worked, but left the
single most expensive part of the job — a chronological scan over every visit
the opportunity has ever recorded, building mother→FLW attribution, ANC
eligibility, and per-visit-type completion — running in the BROWSER on every
run creation. A real MBW opp has 100k+ visits (mbw_auditing_v5.py's snapshot
comment: a verbatim capture measured 112 MB and OOM-killed a web worker on opp
765); that's the dataset the browser re-fetches and re-groups from scratch
every time someone opens the dashboard to start an audit.

v6 moves that scan into a SQL entity-stage pipeline, one row per MOTHER
(`mothers`, below) — collapsing what was a full chronological pass over every
visit into a pre-aggregated table the pipeline cache computes once and reuses,
the same split KMC's `kmc_programme_metrics` uses for its per-baby `children`
entity pipeline (see kmc_programme_metrics.py). Concretely, per mother:

  attributed_flw   — `"aggregation": "last"` on the base `username` column.
                     Entity-stage first/last is ALREADY ordered by
                     (visit_date, visit_id) — see WORKFLOW_REFERENCE.md §2,
                     "first / last semantics at entity stage" — so this is
                     the exact same last-visit-wins attribution v5 computed
                     by sorting visits chronologically in JS and doing
                     `motherToFlw[mid] = username` in a loop.
  anc_ok_count     — count of visits where antenatal_visit_completion == "ok".
  <type>_completed — one filtered count per non-ANC visit type in the
                     schedule (Postnatal Delivery / 1/3/6-month), used as a
                     presence check the same way v5's `!!motherVisits[type]`
                     was, since Tab 1 has no task_filters and therefore no
                     "as of" date to compare against — presence is all Tab 1
                     ever needed for these.

What did NOT move to SQL, and why: GPS distance (lag_haversine), visit
duration, and inter-visit gap. All three depend on a LAG-style window
function, and `window_fields` — confirmed by reading
labs/analysis/backends/sql/query_builder.py — is wired into
`build_visit_extraction_query` (the visit_level path) only; neither
`build_flw_aggregation_query` (aggregated) nor `build_entity_aggregation_query`
(entity) reference it. Folding these into SQL would mean extending the
window-function engine itself, not writing a template — out of scope here.
They stay JS-computed, off the same `visits` pipeline v5 already fetched, but
the JS loop over it now does far less: no more mother→FLW attribution, no
ANC/eligibility bookkeeping, no per-visit-type presence tracking. Just GPS
distance / duration / gap lists per FLW, plus which mothers each FLW has ever
touched (`motherSetsByFlw`, kept because `num_eligible_mothers_visited` is a
many-FLWs-per-mother count and genuinely differs from `attributed_flw`'s
single last-writer — see the render code's `v6_processVisitsSlim` docstring
for why this one field could not move to the `mothers` pipeline).

Tab 2 (task_filters — computing a BASELINE follow-up rate as of each flagged
FLW's own audit-launch date) is a per-run, per-FLW-date computation the
`mothers` pipeline can't answer (it's presence-as-of-now, not date-scoped),
and only ever concerns the handful of FLWs actually being audited in that
batch — not the whole cohort. KMC's worker review took the equivalent problem
to a `pipeline-rows` server endpoint (perf(kmc) #1764: "a worker review
fetches its worker's rows instead of the whole cohort") that fetches one
worker's slice on demand instead of streaming the whole cohort. That pattern
doesn't transfer here as a NETWORK optimization: Tab 1 already needs the full
`visits`/`registrations` fetch (for GPS/duration/gap and for every FLW's
follow-up rate), so both are already resident in the browser by the time Tab 2
runs — fetching them again, scoped, would just be a second round trip for data
already in hand. What v6 borrows from #1764 is the PRINCIPLE (scope the work
to who's actually being audited), applied client-side: the render filters
`visits`/`registrations` down to just the flagged FLWs' rows before running
the full chronological scan, so that scan's cost is proportional to the
audit's size, not the cohort's.

Pipeline aliases (must match pipeline_sources in DEFINITION):
  visits        — unchanged from v5: per-visit rows with GPS coords,
                  bf_status, form_name, lag_haversine distance. Read now only
                  for GPS/duration/gap and the visited-by-any-FLW mother set.
  visits_agg    — per-FLW aggregated counts: num_mothers, bf_count, ebf_count
                  (unchanged from v5), plus visits_completed (NEW in v6).
  mothers       — NEW in v6. Entity-stage, one row per mother: attributed_flw,
                  anc_ok_count, per-visit-type completion counts.
  registrations — unchanged from v5: per-mother schedules + eligibility.
  gs_forms      — unchanged from v5: per-FLW max GS score.
"""

from pathlib import Path

DEFAULT_GS_APP_ID = "2ca67a89dd8a2209d75ed5599b45a5d1"

DEFINITION = {
    "name": "MBW Auditing V6",
    "description": (
        "SQL-native MBW audit dashboard. Mother/FLW rollups (attribution, ANC eligibility, "
        "visit-type completion) computed in a SQL entity pipeline instead of a browser-side scan "
        "over every visit; GPS/duration/gap stay JS-computed (need a window function the "
        "aggregated/entity SQL stages don't support yet). Snapshots saved on completion."
    ),
    "version": 1,
    "templateType": "mbw_auditing_v6",
    "statuses": [
        {"id": "in_progress", "label": "In Progress", "color": "blue"},
        {"id": "completed", "label": "Completed", "color": "green"},
    ],
    "config": {
        # No job_type / server_fetch_pipelines — same as v5, no Python job handler.
        "auth_requires": ["connect", "commcare_hq"],
    },
    "pipeline_sources": [],
}

# ---------------------------------------------------------------------------
# Pipeline schemas
# ---------------------------------------------------------------------------

_BF_STATUS_PATHS = [
    "form.feeding_history.pnc_current_bf_status",
    "form.feeding_history.oneweek_current_bf_status",
    "form.feeding_history.onemonth_current_bf_status",
    "form.feeding_history.threemonth_current_bf_status",
    "form.feeding_history.sixmonth_current_bf_status",
]

# Unchanged from v5 — see mbw_auditing_v5.py's VISITS_GPS_SCHEMA for the
# per-field rationale (lean 8-field schema, gps transforms, lag_haversine).
VISITS_GPS_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "visit_level",
    "fields": [
        {"name": "mother_case_id", "path": "form.parents.parent.case.@case_id", "aggregation": "first"},
        {"name": "visit_datetime", "path": "form.meta.timeEnd", "aggregation": "first"},
        {"name": "time_start", "path": "form.meta.timeStart", "aggregation": "first"},
        {"name": "form_name", "path": "form.@name", "aggregation": "first"},
        {"name": "bf_status", "paths": _BF_STATUS_PATHS, "aggregation": "first"},
        {
            "name": "antenatal_visit_completion",
            "path": "form.visit_completion.antenatal_visit_completion",
            "aggregation": "first",
        },
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
    "window_fields": [
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
        {
            "name": "eligible_full_intervention_bonus",
            "path": "form.eligible_full_intervention_bonus",
            "aggregation": "first",
        },
        {"name": "schedules", "extractor": "mbw_visit_schedules", "aggregation": "first"},
    ],
}

# Unchanged shape from v5, PLUS visits_completed (NEW): a plain per-FLW visit
# count, in SQL, replacing the browser's `visitsCompletedByFlw[username]++`
# tally that used to require a pass over every visit row.
VISITS_AGG_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "num_mothers",
            "path": "form.parents.parent.case.@case_id",
            "aggregation": "count_distinct",
        },
        {
            "name": "bf_count",
            "paths": _BF_STATUS_PATHS,
            "aggregation": "count",
        },
        {
            "name": "ebf_count",
            "paths": _BF_STATUS_PATHS,
            "aggregation": "count",
            "filter_paths": _BF_STATUS_PATHS,
            "filter_value": "ebf",
            "filter_op": "contains_word",
        },
        {
            # NEW in v6. Any always-present per-visit field works as the count
            # target; form.@name is present on every submission by construction.
            "name": "visits_completed",
            "path": "form.@name",
            "aggregation": "count",
        },
    ],
}

# NEW in v6. Entity-stage, one row per MOTHER — replaces the chronological
# JS scan over every visit that used to build mother->FLW attribution, ANC
# eligibility, and per-visit-type completion (v5's `v5_processVisits` +
# `v5_computeFollowupRates`). See this file's module docstring for why each
# field is shaped the way it is.
#
# Presence, not dates: Tab 1 (no task_filters) only ever needed "has this
# mother completed visit type X yet", not "on what date" — the date-scoped
# baseline (Tab 2, per-FLW audit-launch trigger dates) is handled separately,
# by the render's full chronological scan pre-filtered to just the FLWs being
# audited (see mbw_auditing_v6_render.js's computeTab2ByUser), not by this
# pipeline. Extending this schema to also carry dates remains an option if a
# later requirement resurrects a Tab-1 date-scoped view.
MOTHERS_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",  # bookkeeping only; entity stage groups by linking_field
    "terminal_stage": "entity",
    "linking_field": "mother_case_id",
    "fields": [
        {"name": "mother_case_id", "path": "form.parents.parent.case.@case_id", "aggregation": "first"},
        # Last-visit-wins attribution: entity-stage `last` on the base
        # `username` column is ordered by (visit_date, visit_id) already, so
        # this is the SQL equivalent of v5's chronologically-sorted
        # `motherToFlw[mid] = username` loop.
        {"name": "attributed_flw", "path": "username", "aggregation": "last"},
        {
            "name": "anc_ok_count",
            "path": "form.visit_completion.antenatal_visit_completion",
            "aggregation": "count",
            "filter_path": "form.visit_completion.antenatal_visit_completion",
            "filter_value": "ok",
        },
        # The five non-ANC visit_type completions used by v5's follow-up-rate
        # and "5+ visit types" logic (V5_NON_ANC_VISIT_TYPES in the render).
        # Postnatal Delivery Visit gets a second field for the known form-name
        # alias ("Post delivery visit") some CommCare deployments submit —
        # see V5_FORM_NAME_ALIASES in the render code — coalesced client-side
        # since that's cheap over a few thousand mother rows.
        {
            "name": "postnatal_delivery_completed",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "Postnatal Delivery Visit",
        },
        {
            "name": "postnatal_delivery_completed_alias",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "Post delivery visit",
        },
        {
            "name": "one_week_completed",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "1 Week Visit",
        },
        {
            "name": "one_month_completed",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "1 Month Visit",
        },
        {
            "name": "three_month_completed",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "3 Month Visit",
        },
        {
            "name": "six_month_completed",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "6 Month Visit",
        },
    ],
}

GS_FORMS_SCHEMA = {
    "data_source": {
        "type": "cchq_forms",
        "form_name": "Gold Standard Visit Checklist",
        "app_id_source": "opportunity",
        "gs_app_id": DEFAULT_GS_APP_ID,
    },
    "grouping_key": "username",
    "terminal_stage": "aggregated",
    "fields": [
        {
            "name": "gs_score",
            "paths": ["form.gs_score", "form.checklist_percentage"],
            "aggregation": "max",
        },
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
        "name": "MBW Visit Forms (V6)",
        "description": "Per-visit rows with GPS coords and lag_haversine distance. Read for GPS/duration/gap only.",
        "schema": VISITS_GPS_SCHEMA,
    },
    {
        "alias": "visits_agg",
        "name": "MBW Visit Forms — Aggregated (V6)",
        "description": "Per-FLW aggregated counts: distinct mothers, BF/EBF visits, total visits completed",
        "schema": VISITS_AGG_SCHEMA,
    },
    {
        "alias": "mothers",
        "name": "MBW Mother Index (V6)",
        "description": "Per-mother rollup: attributed FLW (last visit wins), ANC-ok, per-visit-type completion",
        "schema": MOTHERS_SCHEMA,
    },
    {
        "alias": "registrations",
        "name": "CCHQ Registration Forms (V6)",
        "description": "Per-mother registration rows with visit schedules and intervention eligibility",
        "schema": REGISTRATIONS_SCHEMA,
    },
    {
        "alias": "gs_forms",
        "name": "CCHQ Gold Standard Forms (V6)",
        "description": "Gold Standard visit checklist forms with FLW scores",
        "schema": GS_FORMS_SCHEMA,
    },
]

RENDER_CODE = (Path(__file__).parent / "mbw_auditing_v6_render.js").read_text(encoding="utf-8")

# Saved-runs snapshot manifest — unchanged in shape from v5 (see that file for
# the "why no raw pipeline rows" rationale, which applies identically here:
# the render still saves what it rendered, not a re-derivation).
SNAPSHOT_INPUTS = {
    "pipelines": [],
    "workers": True,
    "state_keys": [
        "selected_workers",
        "worker_results",
        "task_states",
        "audit_statuses",
        "previous_metrics",
        "previous_categories",
        "concluded_summaries",
        "concluded_prev_categories",
        "concluded_tab2",
    ],
}

SNAPSHOT_SCHEMA = {
    "version": 2,
    "keys": {
        "workers": "FLW list at completion (with opportunity_id tags for multi-opp)",
        "state.selected_workers": "FLWs selected at run launch",
        "state.worker_results": "Per-FLW performance category decisions",
        "state.task_states": "Per-FLW task creation/closure tracking",
        "state.audit_statuses": "Per-FLW audit-required / audit-not-required gate",
        "state.previous_metrics": "Per-FLW metric snapshot captured at conclude time",
        "state.previous_categories": "Per-FLW category snapshot captured at conclude time",
        "state.concluded_summaries": "Computed per-FLW dashboard rows (flw_summaries) as rendered at conclude",
        "state.concluded_prev_categories": "The baseline categories the Prev Category column showed at conclude",
        "state.concluded_tab2": "Per-flagged-FLW baseline-rate (Tab 2) results as of conclude",
    },
}

TEMPLATE = {
    "key": "mbw_auditing_v6",
    "name": "MBW Auditing V6",
    "description": (
        "SQL-native MBW audit. Mother/FLW attribution + ANC eligibility computed in a SQL entity "
        "pipeline instead of a browser-side scan over every visit; GPS/duration/gap stay "
        "JS-computed. Tab 2's date-scoped baseline scan is pre-filtered to just the FLWs "
        "being audited, not the whole cohort."
    ),
    "icon": "fa-clipboard-check",
    "color": "blue",
    "supports_saved_runs": True,
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
