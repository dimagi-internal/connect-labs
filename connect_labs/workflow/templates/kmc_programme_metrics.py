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
        {
            "name": "baby_case_id",
            "paths": ["form.case.@case_id", "entity_id"],
            "aggregation": "first",
            "description": (
                "The KMC beneficiary case, falling back to entity_id for sources with no case "
                "block (synthetic clones). entity_id is per-VISIT on real Connect data, so "
                "grouping on it alone scattered each baby across one row per visit and stranded "
                "every registration-form field \u2014 connect-labs#1224."
            ),
        },
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
        {
            "name": "baby_case_id",
            "paths": ["form.case.@case_id", "entity_id"],
            "aggregation": "first",
            "description": (
                "The KMC beneficiary case, falling back to entity_id for sources with no case "
                "block (synthetic clones). entity_id is per-VISIT on real Connect data, so "
                "grouping on it alone scattered each baby across one row per visit and stranded "
                "every registration-form field \u2014 connect-labs#1224."
            ),
        },
    ],
    "data_source": {"type": "connect_csv"},
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
SNAPSHOT_INPUTS = {
    "pipelines": [],
    "workers": False,
    "state_keys": ["snapshot"],
    # `snapshot` is not optional here the way `worker_states` is for a performance
    # review: every number this dashboard publishes lives under it, so a snapshot
    # without it is not an early snapshot, it is an empty one — and completion
    # cannot be re-opened. An API/MCP caller that completes a run nobody has
    # opened would otherwise get a 200 and a permanently blank published run.
    # Refuse instead, until this template grows a server-side build_snapshot hook.
    "require_state_keys": True,
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
    "version": 2,
    "keys": {
        "state.snapshot.programInd": "Programme-wide indicator results (C01-C31) as published",
        "state.snapshot.byLLO": "Per-LLO indicator results, with each LLO's opportunities nested",
        "state.snapshot.byOpp": "Per-opportunity indicator results",
        "state.snapshot.byFLW": (
            "Per-FLW indicator results, keyed (opportunity, username) — `key` is that pair "
            "joined by '::' and is the render's selection identity. `rows` carries INTEGER "
            "POSITIONS into `state.snapshot.cases`, not case records: a saved run has no "
            "live pipeline behind it, so an empty `rows` would end the drill at the worker, "
            "but storing the records here as well as in `cases` stored every case twice and "
            "put the payload over the 5 MB cap. The render rehydrates on load"
        ),
        "state.snapshot.cases": (
            "Flat index of every case in the snapshot, and the ONLY copy of the case "
            "records — `byFLW[].rows` indexes into it. Referenced by position rather than "
            "`entity_id` because the synthetic cohort reuses entity ids across cloned "
            "opportunities. Slim by design: identity, dates, weights, visit count. The "
            "per-visit weight SERIES is deliberately absent — it would not fit the 5 MB "
            "cap, and the longitudinal workflow fetches it live for the one case a user opens"
        ),
        "state.snapshot.cMeasures": (
            "The display contract these values were graded with — titles, units, directions "
            "and bands as published, so a later threshold change cannot silently re-grade a "
            "saved run"
        ),
        "state.snapshot.mortalityCredible": "Which LLOs record deaths credibly, as published",
        "state.snapshot.monthly": "Programme monthly trend series",
        "state.snapshot.monthlyByScope": (
            "Monthly series precomputed per drill scope (all / llo:<name> / opp:<id>) so a "
            "saved run still supports the LLO and opportunity drill without live pipelines"
        ),
        "state.snapshot.nSeries": "The SQL tab's rows when that tab was run; null otherwise",
        "state.snapshot.schema": "Payload version, independent of this manifest's version",
        "state.snapshot.generated_at": "When the snapshot was built",
        "state.snapshot.meta": (
            "Cohort size as published: cases, visits, opportunities, llos — plus "
            "`synthetic`, which the render reads to show the 'built on synthetic clones' "
            "disclaimer. A saved run can only know what was captured, so an absent flag "
            "publishes a synthetic cohort with no disclaimer at all"
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
    "name": "KMC Programme Metrics (Layer 2 + rollups)",
    "description": (
        "The kmc_metrics_framework registry evaluated live. Programme topline, per-LLO "
        "rollup across each LLO's opportunities, per-FLW aggregation, and a per-case table "
        "carrying every indicator. Indicators an app does not collect render as n/a."
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
    },
    "pipeline_sources": [],
    "snapshot_inputs": SNAPSHOT_INPUTS,
}

TEMPLATE = {
    "key": "kmc_programme_metrics",
    "name": "KMC Programme Metrics (Layer 2 + rollups)",
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
}


def build_snapshot(*, pipelines, state, opportunity_id, **context):
    """Server-side snapshot, so a saved run needs no browser.

    Before this, the only thing that could produce a KMC snapshot was the render:
    an agent could create a run over the API and not complete it, which is the
    opposite of what the workflow framework is for. Numbers come from the same
    `evaluate()` the live dashboard calls, through the same binding
    (semantic/workflow_binding.py), so a saved run and the live view cannot disagree.

    Falls back to whatever the render staged into `state["snapshot"]` when a live
    evaluation is not possible — a caller that already has a good snapshot should
    never be punished for our inability to recompute one.
    """
    import logging

    logger = logging.getLogger(__name__)

    staged = (state or {}).get("snapshot")

    definition_id = context.get("definition_id")
    opportunity_ids = [int(o) for o in (context.get("opportunity_ids") or [opportunity_id])]
    request = context.get("request")
    access_token = context.get("access_token")
    program_id = context.get("program_id")

    # EVERY data accessor below carries the run's scope. On the web path `request`
    # supplies it; on the MCP path there is no request, and an accessor built from a
    # token alone is unscoped — `get_definition` then cannot see the very workflow it
    # was called for ("workflow 5456 could not be read"). Same defect as the registry
    # binding's unscoped read, and the reason it is stated once here rather than at
    # four call sites.
    scope = {"opportunity_id": opportunity_id, "program_id": program_id}

    try:
        from connect_labs.semantic.runtime import evaluate, filter_to_series, measure_catalog
        from connect_labs.semantic.workflow_binding import build_evaluate_inputs, resolve_registry_for
        from connect_labs.workflow.data_access import (
            PipelineDataAccess,
            SemanticRegistryDataAccess,
            WorkflowDataAccess,
        )
        from connect_labs.workflow.templates import kmc_snapshot

        wda = WorkflowDataAccess(request=request, access_token=access_token, **scope)
        try:
            definition = wda.get_definition(definition_id)
        finally:
            wda.close()
        if definition is None:
            raise RuntimeError(f"workflow {definition_id} could not be read")

        pipeline_config, extra_fields = build_evaluate_inputs(
            definition, lambda: PipelineDataAccess(request=request, access_token=access_token, **scope)
        )

        # The registry this WORKFLOW is bound to, not a hardcoded one. That binding is
        # the point of registries-as-records: indicators become editable without a
        # deploy. Hardcoding it here would compute a saved run from the on-disk copy
        # while the dashboard computed from the record — silently, and only once
        # someone actually made the indicators dynamic.
        props_doc, full_registry, llo_map, reg_settings, _source = resolve_registry_for(
            definition,
            registry_access_factory=lambda: SemanticRegistryDataAccess(
                request=request, access_token=access_token, **scope
            ),
        )
        # Every scope a saved run can drill to. ONE pass: GROUPING SETS exist
        # precisely because per-scope calls re-run the whole Layer 1 extraction.
        scopes = [
            "programme",
            "llo",
            "opportunity",
            "flw",
            "month",
            "llo_month",
            "opportunity_month",
        ]
        rows = evaluate(
            pipeline_config,
            opportunity_ids,
            extra_fields=extra_fields,
            registry_documents=(props_doc, full_registry),
            series="C",
            scopes=scopes,
            scope=scopes[0],
            llo_map=llo_map or None,
            settings=reg_settings or None,
        )
        measures = measure_catalog(filter_to_series(full_registry, "C"))
        # Is this cohort synthetic? The render decides with `Number(opp) >= 10000`, a
        # threshold that happens to match LABS_ONLY_OPP_ID_FLOOR. Server-side the
        # registry that OWNS the answer is right here, so ask it rather than port the
        # heuristic — a real opp above the floor would read as synthetic, and a
        # fixture-backed real opp below it (labs_only=False) would read as real.
        from connect_labs.labs.synthetic.models import SyntheticOpportunity

        synthetic_ids = set(
            SyntheticOpportunity.objects.filter(opportunity_id__in=opportunity_ids, enabled=True).values_list(
                "opportunity_id", flat=True
            )
        )
        # ALL of them, matching the render's `opps.every(isSyntheticOpp)`: a mixed
        # cohort is not "synthetic data" and must not carry the disclaimer.
        is_synthetic = bool(opportunity_ids) and all(int(o) in synthetic_ids for o in opportunity_ids)
        llo_by_opp = {int(k): v for k, v in (llo_map or {}).items()}
        cases = kmc_snapshot.case_rows(pipelines, llo_by_opp)
        visits = ((pipelines or {}).get("visits") or {}).get("rows") or []

        return {
            "snapshot": kmc_snapshot.build(
                rows=rows,
                measures=measures,
                llo_map=llo_by_opp,
                credible_sets={
                    "C14": (reg_settings or {}).get("mortality_recording_credible") or {},
                    "C18": (reg_settings or {}).get("completion_recording_credible") or {},
                    "C22": (reg_settings or {}).get("completion_recording_credible") or {},
                },
                cases=cases,
                synthetic=is_synthetic,
                meta={
                    "cases": len(cases),
                    "visits": len(visits),
                    "opportunities": len(opportunity_ids),
                    "llos": len({c.get("llo") for c in cases if c.get("llo")}),
                },
            )
        }
    except Exception:
        if staged:
            # The render already computed a good one; recomputing is an optimisation,
            # not a precondition.
            logger.warning("kmc_programme_metrics: live snapshot failed; keeping the staged one", exc_info=True)
            return {"snapshot": staged}
        raise
