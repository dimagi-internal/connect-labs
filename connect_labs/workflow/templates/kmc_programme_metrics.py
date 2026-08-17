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
        }
    ],
    "data_source": {"type": "connect_csv"},
    "grouping_key": "username",
    "linking_field": "entity_id",
    "terminal_stage": "visit_level",
}

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
    },
    "pipeline_sources": [],
}

TEMPLATE = {
    "key": "kmc_programme_metrics",
    "name": "KMC Programme Metrics (Layer 2 + rollups)",
    "description": DEFINITION["description"],
    "icon": "fa-chart-line",
    "color": "indigo",
    "multi_opp": True,
    "definition": DEFINITION,
    "render_code": _RENDER,
    "pipeline_schemas": [
        {"alias": "children", "name": "KMC Case Properties (SQL)", "schema": CASE_PROPERTIES_SCHEMA},
        {"alias": "visits", "name": "KMC Weight Series", "schema": WEIGHT_SERIES_SCHEMA},
    ],
}
