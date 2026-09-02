"""CHC Mop-up Candidate Analysis — Program 217 ("CHC - NG - RCT - Aug 2026").

This file defines the new WA-level `chc_mopup_visit_quality` pipeline
(PIPELINE_SCHEMAS below) plus the real `DEFINITION`/`RENDER_CODE` for the
"CHC Mop-up Candidate Analysis" workflow: a multi-opp (all 4 LLOs on program
217) dashboard that lets a reviewer set thresholds across (a) EVC shortfall
(delivered/expected visits, from the reused `CHC Work Areas` pipeline),
(b) NCF/inaccessible rate (from this file's own `visit_quality` pipeline,
NOT `CHC Work Areas`' closure fields -- see the render code's module
docstring), and (c) the five data-quality cutoffs computed here, then locks a
candidate work-area set and hands it to the microplans mop-up endpoint (see
connect_labs/microplans/core/mopup.py).

`DEFINITION.pipeline_sources` is deliberately left empty -- `visit_quality` is
auto-created from PIPELINE_SCHEMAS at workflow-creation time, and the reused
`work_areas` / `wa_geometry` / `audit_entries` pipelines must be attached
afterward via `workflow_add_pipeline_source` (they are NOT re-declared here,
since this template doesn't own their schemas). **Gotcha hit live on first
deploy, worth repeating for the next person who touches this**: program 217
has MORE THAN ONE "CHC Work Areas" / "CHC Work Area Geometry" pipeline —
one per LLO, created at different times, not kept in sync with each other.
Picking the wrong one (an older, JHF-owned "CHC Work Areas" that had never
been exercised by any live dashboard) silently returned zero rows for every
opportunity with no error anywhere on the page — not a code bug, just the
wrong pipeline_id. The correct ones to reuse are whichever pipeline an
already-working dashboard on this program depends on (as of this writing,
the "Ward Progress Tracker" workflow's `work_areas`/`wa_geo` sources — check
`workflow_get` on a known-good workflow rather than trusting a same-named
pipeline found via a single opportunity's `pipeline_list`).

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

NCF / Inaccessible counts -- a Phase 2 design correction, not the original plan
--------------------------------------------------------------------------------
The plan (and this file's own earlier revision) sourced the "NCF/inaccessible"
mop-up criterion from `CHC Work Areas`' case-level closure fields
(`wa_checkout_remark`/`reason_for_inaccessible`/`case_closed`). The user
overrode this during Phase 2: those WA-case aggregate properties are not
trustworthy for precise approved/form-type-scoped counting -- the same
problem already solved for the 5 DQ metrics by reading straight from the
approved, form-filtered Connect visit pipeline instead of trusting a case
property (see the `delivered_visit_count` open question flagged in
`ProgramCreateMopupPlanView`'s docstring, which turned out to generalize).
So `ncf_visit_count` / `inaccessible_visit_count` are computed here the exact
same way as `hsd_visit_count` -- `filters={"status": ["approved"]}` at the
schema level plus a `form.@name` equality check per field -- rather than
touching any work_areas closure property. `work_areas.reason_for_inaccessible`
remains available to Phase 2's render code as a supplementary free-text
display detail only (e.g. a tooltip explaining *why* an FLW reported a WA
inaccessible) -- never as part of any threshold/inclusion computation.

Real, confirmed form names: `"Health Service Delivery"` (existing, see below),
`"No Children Found"` (confirmed against
`flw_daily_summary_compute.py:NO_CHILDREN_FOUND_FORM_NAME`), and
`"Inaccessible WA"` (confirmed against `templates/labs/docs/chc_content.html`'s
Work Area Management form table, which lists "No Children Found" and
"Inaccessible WA" as the two sibling deliver-unit forms alongside "Health
Service Delivery" -- the same document that already gave us `NCF_FORM_NAME`'s
parallel entry). Validated via `pipeline_preview` schema_override against
pipeline 12968 (real HSD-only field paths, same pipeline Phase 1 validated
`hsd_visit_count` against): summing `hsd_visit_count + ncf_visit_count +
inaccessible_visit_count` reconciles EXACTLY against the entity-stage's own
`total_visits` counter for every WA/FLW sampled (30 WA-level rows, then 200 of
227 FLW-aggregated rows spanning all 4 LLOs 2154-2157) -- if either form-name
string were wrong, some visits would fall through both nets and this sum
would fall short of `total_visits` for at least one row; it never did.
`inaccessible_visit_count` itself came back **zero for every single row
sampled across all 4 opportunities** -- i.e. this RCT's data, as of this
validation pass, has no APPROVED Inaccessible-WA submissions yet anywhere
(plausible: it's a rarer FLW action gated on NM/LLO review, and the program is
only ~3 weeks in). This is a real, load-bearing caveat for the live-deploy
review: the `inaccessible_visit_count` field and the "Inaccessible WA" string
it depends on are validated by exact arithmetic reconciliation, not by
observing a single real matching row -- worth a spot-check once real
inaccessible activity exists in the data.

Numerator/denominator fields (never bare rates)
--------------------------------------------------
Every metric below exposes a numerator AND a denominator count -- not a rate
-- per the plan: a reviewer (or Phase 2's UI) needs to be able to see "0 of
1" and treat it very differently from "0 of 20" (a `has_sufficient_data`-style
minimum-N floor is a Phase 2 UI concern, not computed here). FLW-level
rollups (Phase 2) must SUM these WA-level numerators/denominators across a
FLW's work areas and divide once -- never average the WA-level percentages,
which would misweight a 1-visit WA the same as a 30-visit WA. The same
sum-then-divide rule applies to `ncf_visit_count`/`inaccessible_visit_count`
now that they're visit_quality fields like everything else here.

    hsd_visit_count          -- denominator base: approved HSD visits at this WA
    ncf_visit_count          -- approved "No Children Found" visits at this WA
    inaccessible_visit_count -- approved "Inaccessible WA" visits at this WA
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

from pathlib import Path

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
            "name": "ncf_visit_count",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "No Children Found",
            "description": "Approved 'No Children Found' visits at this work area -- part of the "
            "NCF/inaccessible mop-up criterion (Phase 2 design correction: sourced from this visit "
            "pipeline, NOT from the work_areas case's wa_checkout_remark/case_closed properties, for the "
            "same approved/form-type precision reason hsd_visit_count exists). See module docstring.",
        },
        {
            "name": "inaccessible_visit_count",
            "path": "form.@name",
            "aggregation": "count",
            "filter_path": "form.@name",
            "filter_value": "Inaccessible WA",
            "description": "Approved 'Inaccessible WA' visits at this work area -- the other half of the "
            "NCF/inaccessible mop-up criterion (see ncf_visit_count and the module docstring). Validated "
            "by arithmetic reconciliation against total_visits, not by observing a nonzero real row -- "
            "came back 0 for every sampled WA/FLW across all 4 opportunities as of this validation pass.",
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

# --- Phase 2: the candidate-analysis dashboard ------------------------------
#
# `work_areas`, `wa_geometry`, and `audit_entries` are pre-existing,
# already-live pipelines this template does NOT own -- they are attached to
# the workflow definition at creation time via the `workflow_add_pipeline_source`
# MCP tool (alias -> pipeline_id), the same mechanism the module docstring
# above already called out (including the "more than one same-named pipeline
# on this program" gotcha -- verify against a known-good workflow's own
# pipeline_sources, don't just grab the first `pipeline_list` match). `visit_quality` is
# the only pipeline this template owns (via PIPELINE_SCHEMAS below); it is
# auto-created when the workflow is created from this template. Per the
# current build's scope, `approved_visits` (12968) is NOT attached as a
# separate source: `visit_quality.hsd_visit_count` already supersedes it for
# the EVC-shortfall math (validated HSD-only, see module docstring), and
# `ncf_visit_count`/`inaccessible_visit_count` now cover the NCF/inaccessible
# criterion the same way -- nothing in the render code needs 12968 directly.

DEFINITION = {
    "name": "CHC Mop-up Candidate Analysis",
    "description": "Threshold-tunable candidate-analysis dashboard for a CHC mop-up round: flags "
    "under-performing work areas on EVC shortfall, NCF/inaccessible rate, and 5 data-quality metrics, "
    "then hands a locked candidate set to the microplans mop-up endpoint.",
    "version": 1,
    "templateType": "chc_mopup_candidates",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
    ],
    "config": {
        "auth_requires": ["connect"],
    },
    "pipeline_sources": [],  # Populated at creation time: visit_quality from PIPELINE_SCHEMAS,
    # work_areas/wa_geometry/audit_entries via workflow_add_pipeline_source (see comment above).
}

RENDER_CODE = (Path(__file__).parent / "chc_mopup_candidates_render.js").read_text(encoding="utf-8")

TEMPLATE = {
    "key": "chc_mopup_candidates",
    "name": "CHC Mop-up Candidate Analysis",
    "description": "Program 217 CHC mop-up candidate analysis: EVC shortfall, NCF/inaccessible, and "
    "5 data-quality thresholds (deworming/MUAC/gender/age-heaping/vaccination), each with a WA-level-only "
    "vs. whole-FLW toggle, combined as a union. Locks a candidate work-area set and hands it to the "
    "microplans mop-up endpoint (connect_labs/microplans/core/mopup.py).",
    "icon": "fa-clipboard-list",
    "color": "orange",
    "multi_opp": True,
    "supports_saved_runs": False,
    "definition": DEFINITION,
    "render_code": RENDER_CODE,
    "pipeline_schemas": PIPELINE_SCHEMAS,
}
