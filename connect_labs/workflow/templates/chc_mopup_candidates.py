"""CHC Mop-up Candidate Analysis — Program 217 ("CHC - NG - RCT - Aug 2026").

# TODO(phase 2): this file currently only defines the new WA-level
# `chc_mopup_visit_quality` pipeline (PIPELINE_SCHEMAS below), needed so the
# pipeline can be created and validated ahead of the dashboard build. The
# `DEFINITION`/`RENDER_CODE` here are placeholders -- a later phase owns the
# actual "CHC Mop-up Candidate Analysis" workflow: a multi-opp (all 4 LLOs on
# program 217) dashboard that lets a reviewer set thresholds across (a) EVC
# shortfall (delivered/expected visits, from the existing `CHC Work Areas`
# pipeline, id 12965), (b) NCF/inaccessible rate (also from 12965's closure
# fields), and (c) the five data-quality cutoffs computed here, then locks a
# candidate work-area set and hands it to the microplans mop-up endpoint
# (see connect_labs/microplans/core/mopup.py). That phase should attach the
# existing pipelines (work_areas=12965, wa_geometry=12971, approved_visits=
# 12968, audit_entries=13013 as optional FLW-week context) via
# `workflow_add_pipeline_source` -- they are NOT re-declared in
# PIPELINE_SCHEMAS here since this template doesn't own their schemas.

Pipeline: chc_mopup_visit_quality (alias "visit_quality")
-----------------------------------------------------------
WA-level (one row per work-area case), aggregated from raw "Health Service
Delivery" (HSD) visit forms. Exists because none of the program's existing
pipelines can drive a "which work areas need a mop-up re-survey" decision on
data-quality grounds:
  - `CHC Audit Report Entries` (13013) is FLW-week grained with no work-area
    or visit identity, so it can't support "just the WAs that failed".
  - `CHC Approved Visits` (12968) is visit-level (one row per visit) --
    useful for a per-WA *count* if something re-aggregates it, but nothing
    upstream of Phase 2 does that today, and Phase 2 wants pre-aggregated
    WA rows to filter against directly.

Grouping and stage
-------------------
This pipeline uses `terminal_stage="entity"` with `linking_field="wa_case_id"`
-- **not** `terminal_stage="aggregated"`, despite that being the more
"obvious" reading of "one aggregated row per work area". `"aggregated"` is
hardcoded in this codebase's SQL query builder to `GROUP BY username,
opportunity_id` (see `build_flw_aggregation_query` in
`connect_labs/labs/analysis/backends/sql/query_builder.py`) -- the schema's
own `grouping_key` value is NOT read to parameterize that GROUP BY, so
`grouping_key="wa_case_id"` with `terminal_stage="aggregated"` would silently
still group by FLW username, not work area. `terminal_stage="entity"` with
`linking_field="wa_case_id"` is the mechanism this codebase actually uses for
"one row per non-FLW key" (see `kmc_programme_metrics.py`, `sam_followup.py`,
`kmc_longitudinal.py` for prior art) -- confirmed against
`build_entity_aggregation_query`, which genuinely does `GROUP BY
({linking_field expression}), opportunity_id`. `grouping_key` is kept as
`"username"` below purely for bookkeeping/cache-key parity with those
existing entity-stage templates; it has no effect on the actual GROUP BY at
this stage.

Approved-only + HSD-form-only filtering (both required, not optional)
-----------------------------------------------------------------------
An approved visit can be an HSD form, a "No Children Found" (NCF) form, or an
Inaccessible-WA form -- deworming/MUAC/gender/age/vaccination fields only
mean anything on HSD forms, so letting NCF/Inaccessible rows into this
pipeline's counts would silently pollute every denominator with visits that
have no such data. `CHC Approved Visits` (12968) guards against exactly this
with an explicit `form.@name == "Health Service Delivery"` check; this
pipeline needs the same guard PLUS the entity-stage grouping 12968 doesn't
have.

Getting there took one small, additive fix to the shared analysis engine,
called out here because it's a real deviation from "just write a pipeline
schema": `build_entity_aggregation_query` previously ignored `config.filters`
entirely (unlike visit-level pipelines like 12968, where `filters={"status":
["approved"]}` genuinely restricts the SQL). Without a fix, an entity-stage
pipeline had no way to express "approved only" at all -- per-field
`filter_path`/`filter_value` can't substitute because it resolves against
`form_json` only and can't reach the real `status` column (see
`_aggregation_to_sql`). See `_entity_stage_filters_where` in
`connect_labs/labs/analysis/backends/sql/query_builder.py` for the fix
(additive: an entity-stage schema that leaves `filters={}`, as every
pre-existing entity-stage template does, gets a byte-identical query).

With that fix, `filters={"status": ["approved"]}` below handles the
"approved" half. The "HSD-form-only" half still only has ONE way to reach it
declaratively: `filter_path`/`filter_value` on a FieldComputation supports
exactly one equality condition, not two ANDed conditions -- so it's used on
the shared `hsd_visit_count` field (`form.@name == "Health Service
Delivery"`), which exists specifically to be the safe HSD-only-and-approved
denominator. Every other numerator field below (deworming/MUAC/gender/
vaccination) is NOT separately gated on `form.@name` -- it doesn't need to
be, because (per the field-path confirmation this plan was built on) those
form fields structurally only exist on the HSD form in the first place; an
NCF or Inaccessible-WA submission simply has no
`dw_meds_delivery_status`/`soliciter_muac_cm`/`childs_gender`/etc. value to
extract, so those rows silently contribute nothing to either side of any
metric's fraction. If that assumption ever turns out to be wrong for some
field (i.e. some other CHC deliver-unit form happens to reuse one of these
question IDs), that field's counts would need the same explicit
`filter_path="form.@name"` treatment as `hsd_visit_count` -- but since
`filter_path` only holds one condition, it would have to trade away its own
metric-specific equality check to do so, which would need a small filter_op
extension (e.g. an "and" of two paths) rather than a one-line fix.

Numerator/denominator fields (never bare rates)
--------------------------------------------------
Every metric below exposes a numerator AND a denominator count -- not a rate
-- per the plan: a reviewer (or Phase 2's UI) needs to be able to see "0 of
1" and treat it very differently from "0 of 20" (a `has_sufficient_data`-style
minimum-N floor is a Phase 2 UI concern, not computed here). FLW-level
rollups (Phase 2) must SUM these WA-level numerators/denominators across a
FLW's work areas and divide once -- never average the WA-level percentages,
which would misweight a 1-visit WA the same as a 30-visit WA.

    hsd_visit_count          -- denominator base: approved HSD visits at this WA
    deworming_given_count    -- numerator: dw_meds_delivery_status == "DW Delivered"
    muac_recorded_count      -- numerator: soliciter_muac_cm is non-null
    vaccination_given_count  -- numerator: received_any_vaccine == "yes"
    gender_recorded_count    -- denominator: childs_gender is non-null
    gender_male_count        -- numerator: childs_gender == "male"
    gender_female_count      -- numerator: childs_gender == "female"
    age_months_<N>_<N+1>_visits (x60, via the age_months histogram)
                              -- one bucket per single month of age 0-59,
                                 mirrors flw_audit_compute.py's own
                                 AGE_MONTH_BUCKETS/whipple_index convention,
                                 for an age-heaping check
    age_months_count         -- denominator: count of visits with age_months
                                 recorded (also usable, by summing the
                                 appropriate buckets client-side, as the
                                 age-gated eligible-count for deworming
                                 [>=12mo] / MUAC [>=6mo] if a stricter
                                 eligibility denominator than
                                 `hsd_visit_count` is wanted -- deliberately
                                 not precomputed here; see rationale above)
    age_months_mean          -- summary stat, free from the histogram

Real field paths (already confirmed against the live form and existing
Program-217 templates -- flw_weekly_audit_report.py, flw_daily_summary_report.py --
not re-derived here):
    wa_case_id:              form.work_area_info.wa_caseid, fallback form.wa_case_id
    form name:                form.@name
    deworming:                form.case.update.dw_meds_delivery_status
    MUAC:                     form.case.update.soliciter_muac_cm
    gender:                   form.additional_case_info.childs_gender,
                              fallback form.case.update.childs_gender
    age (months):             form.additional_case_info.childs_age_in_months,
                              fallback form.case.update.childs_age_in_months
    vaccination:              form.case.update.received_any_vaccine
"""
from __future__ import annotations

VISIT_QUALITY_SCHEMA = {
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",  # bookkeeping only -- entity stage groups by linking_field, see module docstring
    "terminal_stage": "entity",
    "linking_field": "wa_case_id",
    "filters": {"status": ["approved"]},
    "fields": [
        {
            "name": "wa_case_id",
            "paths": ["form.work_area_info.wa_caseid", "form.wa_case_id"],
            "aggregation": "first",
            "description": "Work-area case UUID -- same coalesce order as pipeline 12968 (CHC Approved "
            "Visits): HSD visits carry it at form.work_area_info.wa_caseid, the No Children Found form "
            "stores it separately at the top-level form.wa_case_id. Used as this pipeline's linking_field "
            "(GROUP BY column), not just an output field.",
        },
        {
            "name": "hsd_visit_count",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "Health Service Delivery",
            "description": "Denominator base: count of approved Health Service Delivery visits at this "
            "work area. Approved-only comes from this schema's filters={'status': ['approved']}; "
            "form-type comes from this field's own filter_path/filter_value -- the one place in this "
            "pipeline that explicitly excludes No-Children-Found / Inaccessible-WA approved submissions, "
            "which would otherwise inflate every rate's denominator with visits that carry no "
            "deworming/MUAC/gender/vaccination data at all. See module docstring for why the other "
            "fields below don't repeat this filter.",
        },
        {
            "name": "deworming_given_count",
            "path": "form.case.update.dw_meds_delivery_status",
            "aggregation": "count",
            "filter_path": "form.case.update.dw_meds_delivery_status",
            "filter_value": "DW Delivered",
            "description": "Numerator: visits where a deworming dose was actually administered "
            "('DW Delivered' -- distinct from all_service_del_checks, which also passes on a valid "
            "exemption). Denominator is hsd_visit_count (or an age>=12mo bucket sum from the age_months "
            "histogram, if a stricter eligible-only denominator is wanted -- see module docstring).",
        },
        {
            "name": "muac_recorded_count",
            "path": "form.case.update.soliciter_muac_cm",
            "transform": "float",
            "aggregation": "count",
            "description": "Numerator: visits with a MUAC value recorded. COUNT() already skips NULLs, "
            "and non-HSD approved forms have no soliciter_muac_cm value to extract, so this needs no "
            "separate form-type filter. Denominator is hsd_visit_count (or an age>=6mo bucket sum, see "
            "module docstring).",
        },
        {
            "name": "vaccination_given_count",
            "path": "form.case.update.received_any_vaccine",
            "aggregation": "count",
            "filter_path": "form.case.update.received_any_vaccine",
            "filter_value": "yes",
            "description": "Numerator: visits where the child received a vaccine. Denominator is " "hsd_visit_count.",
        },
        {
            "name": "gender_recorded_count",
            "paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
            "aggregation": "count",
            "description": "Denominator for the gender split: visits with a gender recorded.",
        },
        {
            "name": "gender_male_count",
            "paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
            "aggregation": "count",
            "filter_paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
            "filter_value": "male",
            "description": "Numerator: visits recorded as male. Denominator is gender_recorded_count.",
        },
        {
            "name": "gender_female_count",
            "paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
            "aggregation": "count",
            "filter_paths": ["form.additional_case_info.childs_gender", "form.case.update.childs_gender"],
            "filter_value": "female",
            "description": "Numerator: visits recorded as female. Denominator is gender_recorded_count.",
        },
    ],
    "histograms": [
        {
            "name": "age_months",
            "paths": ["form.additional_case_info.childs_age_in_months", "form.case.update.childs_age_in_months"],
            "transform": "float",
            "lower_bound": 0,
            "upper_bound": 60,
            "num_bins": 60,
            "bin_name_prefix": "age_months",
            "include_out_of_range": True,
            "description": "One bucket per single month of age (0-59), mirroring "
            "flw_audit_compute.py's AGE_MONTH_BUCKETS/whipple_index convention -- lets Phase 2 compute "
            "an age-heaping check (and, by summing the appropriate buckets, an age-gated eligible-count "
            "for deworming/MUAC) client-side from real counts rather than a pre-baked index. Also "
            "produces age_months_count (denominator: visits with age recorded) and age_months_mean "
            "for free.",
        },
    ],
}

PIPELINE_SCHEMAS = [
    {
        "alias": "visit_quality",
        "name": "CHC Mop-up Visit Quality",
        "description": "One row per work area: approved Health-Service-Delivery-only visit counts and "
        "data-quality numerator/denominator pairs for deworming, MUAC, gender, age, and vaccination.",
        "schema": VISIT_QUALITY_SCHEMA,
    },
]

# --- TODO(phase 2): everything below is a placeholder ----------------------

DEFINITION = {
    "name": "CHC Mop-up Candidate Analysis",
    "description": "Placeholder -- Phase 2 builds the real candidate-analysis dashboard "
    "(threshold panel, ward/FLW aggregation tables, map, lock-and-handoff to microplans) on top of "
    "the visit_quality pipeline defined in this file. See module docstring.",
    "version": 1,
    "templateType": "chc_mopup_candidates",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
    ],
    "config": {
        "auth_requires": ["connect"],
    },
    "pipeline_sources": [],  # Populated at creation time from PIPELINE_SCHEMAS
}

RENDER_CODE = ""  # TODO(phase 2): candidate-analysis dashboard render code

TEMPLATE = {
    "key": "chc_mopup_candidates",
    "name": "CHC Mop-up Candidate Analysis",
    "description": "Placeholder template -- see module docstring. Currently only registers the "
    "chc_mopup_visit_quality pipeline for Phase 1 validation.",
    "icon": "fa-clipboard-list",
    "color": "orange",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
