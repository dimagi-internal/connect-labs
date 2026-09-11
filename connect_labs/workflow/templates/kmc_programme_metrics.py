"""KMC Programme Metrics (Layer 2 + rollups).

A direct port of the `kmc_metrics_framework` workbook: the Case-indicators tab
(C01-C33) evaluated live, rolled up Programme -> LLO -> opportunity -> FLW -> case.

Two things in here are load-bearing and easy to lose, which is why this template
exists as a file rather than only as a DB row:

1. **The A/B/C coalesce unions.** Three app generations are live across the 11 KMC
   opportunities. The Gen-1 pilots (523 Nama, 524 PIPN, 675 GHI) predate the
   hospital-discharge and self-referral blocks and put DOB at `form.child_DOB`
   rather than `form.mothers_details.child_DOB`. Every `paths` list below is a
   union across all three generations; dropping an entry silently blanks an LLO.

2. **APP_ASKS in the render code.** Derived from each opportunity's
   `app_structure.json` — the app's ACTUAL question set — NOT from observed data.
   A blank column has three causes and only one is benign:

     not-in-app      the app never asks it              -> n/a, benign
     never-recorded  it asks and nothing was ever filled -> data-quality flag
     normal          asked and answered                  -> score it

   Deriving this from data collapses the middle case into the first, which turns a
   collection failure into a benign n/a. Two live examples: NAMA-523 and PIPN-524
   both ASK for birth weight and recorded it zero times, and all 11 apps ask for
   reg_date and for kmc discharge with not one value recorded between them.

The render layer derives only the weight-series triple (what SQL cannot express);
everything else is computed in the entity pipeline.
"""

from pathlib import Path

_RENDER = (Path(__file__).parent / "kmc_programme_metrics_render.js").read_text()

# NEAL'S RULE 0, on both pipelines: only approved and over_limit visits are valid
# data. over_limit is paid work mislabelled by a platform glitch (excluding it
# undercounts visits and weight series by 40-130%); rejected, pending, duplicate
# and trial are not. Declared here as a pipeline filter so the case index, the
# drill and the indicators all see the same visits -- semantic Layer 1 used to
# drop a pipeline's row filters, which would have applied this to the pipeline's
# own rows and to no indicator (fixed alongside this).
VALID_VISIT_FILTER = {"status": ["approved", "over_limit"]}

# WHO THE BABY IS, shared by both pipelines so they cannot disagree about it.
#
# Neal's compute spec, section 1, keys a baby by a case id whose path depends on
# the FORM, and a single "first present" list cannot express it: both candidate
# paths are present on both registration designs and mean opposite things.
#
#   Register KMC Beneficiary (design A)  form.case.@case_id = the baby
#                                        subcase_0          = another case
#   Child Registration Form  (design B)  form.case.@case_id = the MOTHER
#                                        subcase_0          = the baby
#   Record Visit Details                 kmc_beneficiary_case_id (A) /
#                                        child_case_id (B)  = the baby, and a
#                                        fresh subcase_0 on every visit
#
# Observed on production 2026-09-11 (opps 523, 675, 1487, 1790). Each ordering of
# one list fixed one design and split the other -- registration and visits on
# different ids, so no baby carried both a birthweight and a weight series. GHI
# opp 675 had zero qualifying babies; BERI counted every baby twice and blanked
# the discharge metric. `conditional_paths` routes design B's registration to its
# subcase; everything else takes the default list. entity_id stays last for
# sources with no case block (synthetic clones) -- it is per-VISIT on real Connect
# data, which is why it is only ever the fallback (connect-labs#1224).
BABY_CASE_ID_FIELD = {
    "name": "baby_case_id",
    "paths": ["form.kmc_beneficiary_case_id", "form.child_case_id", "form.case.@case_id", "entity_id"],
    "conditional_paths": [
        {
            "when_path": "form.@name",
            "when_value": "Child Registration Form",
            "paths": ["form.subcase_0.case.@case_id"],
        }
    ],
    "aggregation": "first",
    "description": (
        "The baby's case id, by form design (Neal's compute spec section 1): the visit's "
        "kmc_beneficiary_case_id / child_case_id; design B's registration subcase; design A's "
        "form.case. entity_id only as the last resort -- it is per-visit on real data."
    ),
}

# Per-baby properties, computed in SQL at entity stage. Terminal stage `entity`
# groups by linking_field=entity_id, so one row per baby.
CASE_PROPERTIES_SCHEMA = {
    "fields": [
        {
            "name": "reg_date",
            "paths": [
                "form.subcase_0.case.update.reg_date",
                "form.reg_date",
                "form.case.update.reg_date",
                "form.grp_kmc_beneficiary.reg_date",
            ],
            "transform": "date",
            "aggregation": "first",
        },
        {
            "name": "dob",
            "paths": [
                "form.subcase_0.case.update.child_DOB",
                "form.child_details.child_DOB",
                "form.mothers_details.child_DOB",
                "form.case.update.child_DOB",
                "form.child_DOB",
            ],
            "transform": "date",
            "aggregation": "first",
        },
        {
            "name": "gender",
            "paths": [
                "form.subcase_0.case.update.child_gender",
                "form.child_details.child_gender",
                "form.case.update.child_gender",
            ],
            "aggregation": "first",
        },
        {
            "name": "birth_weight_g",
            "paths": [
                "form.child_details.birth_weight_group.child_weight_birth",
                "form.case.update.child_weight_birth",
                "form.child_weight_birth",
                "form.case.update.birth_weight",
                "form.child_details.birth_weight",
            ],
            "transform": "kg_to_g",
            "aggregation": "first",
        },
        {
            "name": "enrollment_weight_g",
            "paths": [
                "form.subcase_0.case.update.child_weight_reg",
                "form.child_details.birth_weight_reg.child_weight_reg",
                "form.case.update.child_weight_reg",
                "form.child_details.child_weight_reg",
            ],
            "transform": "kg_to_g",
            "aggregation": "first",
        },
        {
            "name": "weights",
            "paths": [
                "form.anthropometric.child_weight_visit",
                "form.subcase_0.case.update.child_weight_visit",
                "form.anthropometric.child_weight",
                "form.case.update.child_weight",
                "form.case.update.child_weight_last_visit",
                "form.case.update.child_weight_visit",
            ],
            "transform": "kg_to_g",
            "aggregation": "list",
        },
        {
            "name": "n_weights",
            "paths": [
                "form.anthropometric.child_weight_visit",
                "form.subcase_0.case.update.child_weight_visit",
                "form.anthropometric.child_weight",
                "form.case.update.child_weight",
                "form.case.update.child_weight_last_visit",
                "form.case.update.child_weight_visit",
            ],
            "transform": "float",
            "aggregation": "count",
        },
        {
            "name": "first_weight_g",
            "paths": [
                "form.anthropometric.child_weight_visit",
                "form.subcase_0.case.update.child_weight_visit",
                "form.anthropometric.child_weight",
                "form.case.update.child_weight",
                "form.case.update.child_weight_last_visit",
                "form.case.update.child_weight_visit",
            ],
            "transform": "kg_to_g",
            "aggregation": "first",
        },
        {
            "name": "last_weight_g",
            "paths": [
                "form.anthropometric.child_weight_visit",
                "form.subcase_0.case.update.child_weight_visit",
                "form.anthropometric.child_weight",
                "form.case.update.child_weight",
                "form.case.update.child_weight_last_visit",
                "form.case.update.child_weight_visit",
            ],
            "transform": "kg_to_g",
            "aggregation": "last",
        },
        {
            "name": "death_visits",
            "paths": ["form.child_alive", "form.case.update.child_alive"],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": ["form.child_alive", "form.case.update.child_alive"],
            "filter_value": "no",
        },
        {"name": "alive_last", "paths": ["form.child_alive", "form.case.update.child_alive"], "aggregation": "last"},
        {
            "name": "alive_readings",
            "paths": ["form.child_alive", "form.case.update.child_alive"],
            "aggregation": "count",
        },
        {
            "name": "hospital_discharge_date",
            "paths": [
                "form.hosp_lbl.date_hospital_discharge",
                "form.subcase_0.case.update.date_hospital_discharge",
                "form.case.update.date_hospital_discharge",
            ],
            "transform": "date",
            "aggregation": "first",
            "description": "Actual discharge date (Design A/B registration form)",
        },
        {
            "name": "gestational_age_wks",
            "paths": [
                "form.case.update.gestational_age_at_birth_lmp",
                "form.mothers_details.gestational_age_at_birth_lmp",
                "form.subcase_0.case.update.gestational_age_at_birth_lmp",
            ],
            "transform": "float",
            "aggregation": "first",
            "description": (
                "Gestational age at birth in weeks. Layer 1 never carried it, so the "
                "median-gestational-age metric had no input at all. Paths per the demo "
                "compute spec section 1 (Design A and B)."
            ),
        },
        {
            "name": "days_discharge_to_reg",
            "paths": [
                "form.child_details.child_age_at_reg_discharge_date",
                "form.case.update.child_age_at_reg_discharge_date",
                "form.subcase_0.case.update.child_age_at_reg_discharge_date",
            ],
            "transform": "float",
            "aggregation": "first",
            "description": "Days between hospital discharge and registration \u2014 C16/C17 numerator input",
        },
        {
            "name": "danger_visits",
            "paths": [
                "form.child_details.Danger_Signs_Checklist.jaundice_grp.jaundice",
                "form.child_details.Danger_Signs_Checklist.jaundice",
                "form.danger_signs_checklist.jaundice_grp.jaundice",
                "form.danger_signs_checklist.jaundice",
                "form.child_details.Danger_Signs_Checklist.conv_grp.Convulsions_or_seizures",
                "form.child_details.Danger_Signs_Checklist.Convulsions_or_seizures",
                "form.danger_signs_checklist.Convulsions_or_seizures",
                "form.Danger_Signs_Checklist.Convulsions_or_seizures",
                "form.child_details.danger_signs_checklist.convulsions_or_seizures",
                "form.danger_signs_checklist.convulsions_or_seizures",
            ],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": [
                "form.child_details.Danger_Signs_Checklist.jaundice_grp.jaundice",
                "form.child_details.Danger_Signs_Checklist.jaundice",
                "form.danger_signs_checklist.jaundice_grp.jaundice",
                "form.danger_signs_checklist.jaundice",
                "form.child_details.Danger_Signs_Checklist.conv_grp.Convulsions_or_seizures",
                "form.child_details.Danger_Signs_Checklist.Convulsions_or_seizures",
                "form.danger_signs_checklist.Convulsions_or_seizures",
                "form.Danger_Signs_Checklist.Convulsions_or_seizures",
                "form.child_details.danger_signs_checklist.convulsions_or_seizures",
                "form.danger_signs_checklist.convulsions_or_seizures",
            ],
            "filter_value": "yes",
        },
        {
            "name": "referral_visits",
            "paths": [
                "form.child_details.Danger_Signs_Checklist.child_referred",
                "form.danger_signs_checklist.child_referred",
                "form.child_referred",
                "form.referral_status",
                "form.case.update.referral_status",
                "form.Danger_Signs_Checklist.child_referred",
            ],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": [
                "form.child_details.Danger_Signs_Checklist.child_referred",
                "form.danger_signs_checklist.child_referred",
                "form.child_referred",
                "form.referral_status",
                "form.case.update.referral_status",
                "form.Danger_Signs_Checklist.child_referred",
            ],
            "filter_value": "yes",
        },
        {
            "name": "self_referral_visits",
            "paths": [
                "form.self-referral_check.self_referral_child_taken_to_the_hospital_1",
                "form.self-referral_check.self_referral_child_taken_to_the_hospital_2",
            ],
            "filter_op": "contains_word",
            "aggregation": "count",
            "filter_paths": [
                "form.self-referral_check.self_referral_child_taken_to_the_hospital_1",
                "form.self-referral_check.self_referral_child_taken_to_the_hospital_2",
            ],
            "filter_value": "yes",
        },
        {
            "name": "ebf_visits",
            "paths": [
                "form.feeding_checklist.direct_breastfeeding",
                "form.feeding_checklist.direct_breastfeed_grp.direct_breastfeeding",
            ],
            "aggregation": "count",
        },
        {
            "name": "kmc_hours_mean",
            "paths": [
                "form.kmc_24-hour_recall.kmc_hours",
                "form.KMC_24-Hour_Recall.kmc_hours",
                "form.kmc_24-hour_recall.kmc_hours_secondary",
                "form.case.update.kmc_hours",
            ],
            "transform": "float",
            "aggregation": "avg",
        },
        {
            "name": "last_kmc_status",
            "paths": [
                "form.kmc_status_entered",
                "form.case.update.kmc_status",
                "form.grp_kmc_beneficiary.kmc_status",
                "form.kmc_status",
                "form.continue_kmc",
            ],
            "aggregation": "last",
        },
        {
            "name": "discharge_visits",
            "paths": ["form.kmc_discontinuation.kmc_status_discharged", "form.kmc_discontinuation.discharged_logic"],
            "aggregation": "count",
        },
        BABY_CASE_ID_FIELD,
        {
            "name": "form_names",
            "path": "form.@name",
            "aggregation": "list",
            "description": (
                "Every form name in this baby's series. Separates REGISTERED (has a registration "
                "form) from STARTED (has a follow-up visit) \u2014 without it C01/C02/C05 were all "
                "identical because 'started' was defined as having >=1 visit, which every case "
                "has by construction."
            ),
        },
    ],
    "data_source": {"type": "connect_csv"},
    "filters": VALID_VISIT_FILTER,
    "grouping_key": "username",
    "linking_field": "entity_id",
    "terminal_stage": "entity",
}

# The one series SQL cannot fold: every weight reading with its visit date, so the
# render layer can compute the 21-35 day growth window per baby.
WEIGHT_SERIES_SCHEMA = {
    "fields": [
        {
            "name": "weight_g",
            "paths": [
                "form.anthropometric.child_weight_visit",
                "form.subcase_0.case.update.child_weight_visit",
                "form.anthropometric.child_weight",
                "form.case.update.child_weight",
                "form.case.update.child_weight_last_visit",
            ],
            "transform": "kg_to_g",
            "aggregation": "first",
        },
        BABY_CASE_ID_FIELD,
    ],
    "data_source": {"type": "connect_csv"},
    "filters": VALID_VISIT_FILTER,
    "grouping_key": "username",
    "linking_field": "entity_id",
    "terminal_stage": "visit_level",
}

# ---------------------------------------------------------------------------
# Saved runs
# ---------------------------------------------------------------------------
# pipelines is EMPTY on purpose. This cohort's two pipelines are 8,656 case rows +
# 34,737 visit rows = 21.6 MB of JSON — four times the framework's 5 MB hard cap, and
# WORKFLOW_REFERENCE is explicit that verbatim pipeline capture is the failure mode
# that OOM-killed a web worker on a 102k-visit opp. The render instead computes the
# aggregates it displays and saves THOSE into `snapshot` (~300 KB) in the
# onUpdateState write that precedes view.complete().
#
# That is also the right thing to preserve: a published figure should be the numbers
# as published, not a re-derivation that silently moves when the pipeline or the
# clone behind it changes.
# The snapshot is DECLARED, not coded. `builder` selects the framework's generic
# semantic-snapshot builder (workflow/snapshot_builders.py) and everything else here
# is its spec, so this dashboard's saved-run shape can be changed by patching the
# workflow definition — no deploy.
#
# What this replaced: `state_keys: ["snapshot"]` + `require_state_keys: True`, which
# meant the payload was whatever the RENDER staged from a browser, and then a
# 351-line `kmc_snapshot.py` hand-port of that render's JavaScript once the browser
# requirement was removed. Neither is needed: every threshold below is registry
# data, and the builder grades whatever registry this workflow is bound to.
#
# `require_state_keys` is gone with the cause — there is no staged state to be
# missing when the server computes the numbers.
SNAPSHOT_INPUTS = {
    "builder": "semantic_snapshot",
    # C is the headline registry (Neal's workbook, banded). N is his demo compute
    # spec -- the 15-metric scorecard -- graded from the same rows.
    "series": ["C", "N"],
    # Every scope a saved run can drill to, in ONE evaluate pass: GROUPING SETS
    # exist precisely because per-scope calls re-run the whole Layer 1 extraction.
    "scopes": [
        "programme",
        "llo",
        "opportunity",
        "flw",
        "month",
        "llo_month",
        "opportunity_month",
    ],
    # Which cached pipelines completion must load. Read by workflow_save_snapshot
    # before the builder runs, so a missing warm is refused by name.
    "pipelines": ["children", "visits"],
    # The per-case index the FLW drill and the longitudinal hand-off read.
    # Deliberately SLIM: the per-visit weight SERIES is absent because a snapshot
    # has a 5 MB hard cap and ~9,000 cases only fit at this width — the
    # longitudinal workflow fetches the series live for the one case a user opens.
    "case_index": {
        "pipeline": "children",
        "fields": [
            "entity_id",
            "username",
            "opportunity_id",
            "reg_date",
            "dob",
            "gender",
            "birth_weight_g",
            "first_weight_g",
            "last_weight_g",
            "total_visits",
            "first_visit_date",
            "last_visit_date",
            "last_kmc_status",
        ],
    },
    "visits_pipeline": "visits",
    # indicator -> the registry settings table that says which LLOs record it
    # credibly. Was three indicator ids and three settings keys baked into the hook.
    "credibility": {
        "C14": "mortality_recording_credible",
        "C18": "completion_recording_credible",
        "C22": "completion_recording_credible",
        # The scorecard's mortality is the same human judgement.
        "N13": "mortality_recording_credible",
    },
    # The render's own fallback (`var MIN_DEN = 25`), for measures that declare no
    # `min_denominator` of their own.
    "min_denominator_default": 25,
    "workers": False,
}

# One word throughout: SNAPSHOT — matching snapshot_inputs, snapshot_schema,
# build_snapshot, workflow_save_snapshot and run.data["snapshot"].
#
# This template used to say `frozen`, which was never a second concept: it was a
# leftover from when the dashboard computed the whole thing in the browser and
# "froze" what it had in hand. The server builds it now, so the artifact went with
# the artifact's cause. Runs saved under the old key are not migrated — by decision
# (Jon, 2026-09-08), there were two and they predate every fix in this file.
SNAPSHOT_SCHEMA = {
    "version": 3,
    "keys": {
        "state.snapshot.programInd": "Programme-wide indicator results (C01-C31) as published",
        "state.snapshot.byLLO": "Per-LLO indicator results, with each LLO's opportunities nested",
        "state.snapshot.byOpp": "Per-opportunity indicator results",
        "state.snapshot.byFLW": (
            "Per-FLW indicator results. `key` is (opportunity, username) joined by '::' and is "
            "the render's selection identity; `flw` is the name it displays; `reds`/`yellows` "
            "are its badge counts. `rows` carries INTEGER POSITIONS into "
            "`state.snapshot.cases`, not case records — a saved run has no live pipeline "
            "behind it, so an empty `rows` would end the drill at the worker, but holding the "
            "records here as well as in `cases` stored every case twice and put the payload "
            "over the 5 MB cap. The render rehydrates on load"
        ),
        "state.snapshot.cases": (
            "Flat index of every case in the snapshot, and the ONLY copy of the case records — "
            "`byFLW[].rows` indexes into it. Referenced by position rather than `entity_id` "
            "because a synthetic cohort reuses entity ids across cloned opportunities. Slim by "
            "design: identity, dates, weights, visit count. The per-visit weight SERIES is "
            "deliberately absent — it would not fit the 5 MB cap, and the longitudinal "
            "workflow fetches it live for the one case a user opens"
        ),
        "state.snapshot.cMeasures": (
            "The display contract these values were graded with — titles, units, directions, "
            "bands, min-denominators, gate inputs and coverage floors as published, so a later "
            "threshold change cannot silently re-grade a saved run. This is the same "
            "`measure_catalog` the live view grades with, so the two cannot diverge"
        ),
        "state.snapshot.credibility": (
            "indicator -> which LLOs record it credibly, as published. Resolved from the "
            "builder spec's `credibility` mapping onto the registry's settings tables. "
            "Replaces the single-purpose `mortalityCredible`, which could only carry C14"
        ),
        "state.snapshot.deployment": (
            "The availability facts the gates graded with (`llo_map`, `app_asks`), so a saved "
            "run can explain WHY a cell reads 'not in this app' without the repo it was built "
            "from. These were static dicts in semantic/gates.py and are now registry data"
        ),
        "state.snapshot.pooledOverCredible": (
            "indicator -> {ind, llos, of}: for each credibility-gated indicator, the figure "
            "POOLED over the recorders the workbook accepts, which LLOs those were, and how "
            "many there were in total. The programme row pools every LLO, so on mortality it "
            "reads lower than reality — non-recorders contribute denominator without deaths. "
            "A saved run cannot rebuild this from its graded cells: a row banded "
            "'insufficient' still contributes to the pool while storing no value"
        ),
        "state.snapshot.series": (
            "Further indicator families graded from the same evaluation, keyed by series. "
            "`N` is the 15-metric scorecard from the demo compute spec: its catalog and its "
            "programme / LLO / opportunity / worker cells, in the same {id, n, value, band} "
            "shape as the headline series"
        ),
        "state.snapshot.weekly": (
            "Activity by ISO week per drill scope (all / llo:<name> / opp:<id>): visits that "
            "happened and babies registered, cut at the run's as-of date. The indicator lines "
            "of the trend are NOT in the snapshot -- they are the series of saved runs, one "
            "point per run computed as of its period end, served by the run-history API"
        ),
        "state.snapshot.monthly": (
            "Programme monthly trend series: per cohort month, the graded indicators, cohort "
            "size, the count of visits that HAPPENED that month (activity, from the visit rows "
            "— a different grouping from the cohort month, so not derivable from the "
            "indicators) and, per credibility-gated indicator, the figure pooled over that "
            "month's credible recorders"
        ),
        "state.snapshot.monthlyByScope": (
            "Monthly series precomputed per drill scope (all / llo:<name> / opp:<id>) so a "
            "saved run still supports the LLO and opportunity drill without live pipelines"
        ),
        "state.snapshot.nSeries": "The SQL tab's rows when that tab was run; null otherwise",
        "state.snapshot.schema": "Payload version, independent of this manifest's version",
        "state.snapshot.generated_at": "When the snapshot was built",
        "state.snapshot.meta": (
            "Cohort size as published: cases, visits, opportunities, llos — plus `as_of`, the "
            "date every figure is reported as of (the run's period end), and `synthetic`, "
            "which the render reads to show the 'built on synthetic clones' disclaimer. "
            "Resolved from the SyntheticOpportunity registry, and ABSENT rather than false "
            "when that cannot be determined, since a confident false would claim real "
            "programme data"
        ),
    },
}

# ---------------------------------------------------------------------------
# Drill-to-action config, derived from kmc_image_audit's hardware map.
#
# The map is keyed by SOURCE opportunity id there; this dashboard runs on the
# synthetic clones, which have different ids. LLO is what both carry, so the
# routing is collapsed onto it here rather than restated -- if OPP_META gains an
# opportunity or a scale type is corrected, this follows automatically.
# ---------------------------------------------------------------------------
WEIGHT_IMAGE_PATH = "anthropometric/upload_weight_image"
WEIGHT_VALUE_PATH = "anthropometric/child_weight_visit"


def _scale_agent_by_llo() -> tuple[dict[str, str], set[str]]:
    from connect_labs.workflow.templates.kmc_image_audit import AGENT_FOR_SCALE, OPP_META

    by_llo: dict[str, str] = {}
    conflicting: set[str] = set()
    for meta in OPP_META.values():
        llo, agent = meta.get("llo"), AGENT_FOR_SCALE.get(meta.get("scale"))
        if not llo or not agent:
            continue
        if by_llo.setdefault(llo, agent) != agent:
            # An LLO running both hardware types cannot be routed by LLO alone.
            # Recording it is the point: silently picking one would attach the
            # wrong reader to half its photos.
            conflicting.add(llo)
    return by_llo, conflicting


SCALE_AGENT_BY_LLO, _CONFLICTING_SCALE_LLOS = _scale_agent_by_llo()

# GHI-KE and Kikapu are provisionally treated as digital in OPP_META and flagged
# there as UNCONFIRMED; carry that through so the UI can say so rather than let a
# green verdict read as settled. An LLO with mixed hardware is unverified too.
UNVERIFIED_SCALE_LLOS = {"GHI", "Kikapu"} | _CONFLICTING_SCALE_LLOS


DEFINITION = {
    "name": "KMC Programme Metrics",
    "description": (
        "Kangaroo Mother Care programme report, one page per saved weekly run: five headline "
        "indicators with a week-on-week delta, the 15-metric scorecard by organisation with "
        "last visit and attention, activity by week and indicator trends across saved reports. "
        "An organisation opens to its opportunities and workers; a worker opens to the KMC "
        "Worker Review. Every figure comes off the semantic-snapshot payload; indicators an "
        "app does not collect render as not in this app."
    ),
    "version": 1,
    "templateType": "kmc_programme_metrics",
    "statuses": [
        {"id": "active", "label": "Active", "color": "green"},
        {"id": "discharged", "label": "Discharged", "color": "blue"},
        {"id": "lost_to_followup", "label": "Lost to Follow-up", "color": "red"},
    ],
    "config": {
        "multi_opp": True,
        "showFilters": False,
        "showSummaryCards": True,
        "templateType": "kmc_programme_metrics",
        # --- drill-to-action -------------------------------------------------
        # The dashboard already drills programme -> LLO -> opportunity -> FLW ->
        # case. What it could not do was ACT on what the drill found: a worker
        # reading red had to be carried by hand into a separate workflow. These
        # let the FLW panel open an audit on that one worker directly.
        "audit_enabled": True,
        "weight_image_path": WEIGHT_IMAGE_PATH,
        "weight_value_path": WEIGHT_VALUE_PATH,
        # Which scale reviewer to attach, keyed by LLO rather than by source opp
        # id: this dashboard runs on the SYNTHETIC clones, whose ids are not the
        # source ids OPP_META is keyed by, and the LLO is carried on every row.
        # Derived from kmc_image_audit's OPP_META so the hardware map has one
        # home -- PIPN digital, NAMA/EHA/BERI dial, GHI/Kikapu unconfirmed.
        "scale_agent_by_llo": SCALE_AGENT_BY_LLO,
        "scale_unverified_llos": sorted(UNVERIFIED_SCALE_LLOS),
        "audit_count_per_flw": 25,
        # Where a worker row opens: the KMC Worker Review workflow and its
        # long-lived run, `{"workflow_id": ..., "run_id": ...}`. Written on the
        # instance by the `companions` entry below the moment this template is
        # created, so a hand-created report links from the first render. None
        # here, never a real id: an id in the template would point every new
        # instance at one review workflow in one scope.
        "flw_review": None,
    },
    "pipeline_sources": [],
    "snapshot_inputs": SNAPSHOT_INPUTS,
}

TEMPLATE = {
    "key": "kmc_programme_metrics",
    "name": "KMC Programme Metrics",
    "description": DEFINITION["description"],
    "icon": "fa-chart-line",
    "color": "indigo",
    "multi_opp": True,
    "supports_saved_runs": True,
    "snapshot_inputs": SNAPSHOT_INPUTS,
    "snapshot_schema": SNAPSHOT_SCHEMA,
    "definition": DEFINITION,
    "render_code": _RENDER,
    "pipeline_schemas": [
        {"alias": "children", "name": "KMC Case Properties (SQL)", "schema": CASE_PROPERTIES_SCHEMA},
        {"alias": "visits", "name": "KMC Weight Series", "schema": WEIGHT_SERIES_SCHEMA},
    ],
    # The drill's second page. Creating this report also creates the KMC Worker
    # Review in the same scope over the same opportunities, on the SAME two
    # pipeline records (one cache), mints the review's long-lived run, and
    # cross-links the two: this report's `config.flw_review` and the review's
    # `config.source_workflow_id`. One "Create" gives the whole feature — the
    # four-call runbook that used to follow it is gone.
    "companions": [
        {
            "template_key": "kmc_flw_review",
            "config_key": "flw_review",
            "share_pipelines": True,
            "mint_run": True,
            "back_reference": "source_workflow_id",
        }
    ],
}
