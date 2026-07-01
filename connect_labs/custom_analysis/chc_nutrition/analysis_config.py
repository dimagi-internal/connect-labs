"""
Analysis configuration for CHC Nutrition project.

Based on SQL query for opportunity 575 - extracts nutrition and health metrics
from UserVisit form_json and aggregates at FLW level.

Supports multiple form structures:
- Opportunity 814: form.case.update.*, form.additional_case_info.*
- Opportunity 822: form.subcase_0.case.update.*, form.case_info.*, form.child_registration.*
"""

from connect_labs.labs.analysis import AnalysisPipelineConfig, CacheStage, FieldComputation, HistogramComputation


# Helper function for MUAC value validation
def _is_valid_muac(x) -> bool:
    """Check if value is a valid MUAC measurement (numeric string)."""
    if not x:
        return False
    s = str(x).strip()
    return s.replace(".", "").replace("-", "").isdigit()


CHC_NUTRITION_CONFIG = AnalysisPipelineConfig(
    grouping_key="username",
    # Pipeline metadata
    experiment="chc_nutrition",
    terminal_stage=CacheStage.AGGREGATED,
    fields=[
        FieldComputation(
            name="commcare_userid",
            path="form.meta.userID",
            aggregation="first",
            description="CommCare user ID from form metadata",
        ),
        # Gender counts for calculating gender split
        FieldComputation(
            name="male_count",
            path="form.additional_case_info.childs_gender",
            paths=[
                "form.additional_case_info.childs_gender",  # opp 814
                "form.child_registration.childs_gender",  # opp 822
                "form.subcase_0.case.update.childs_gender",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["male", "m", "boy", "male_child"] else None,
            description="Number of male children",
        ),
        FieldComputation(
            name="female_count",
            path="form.additional_case_info.childs_gender",
            paths=[
                "form.additional_case_info.childs_gender",  # opp 814
                "form.child_registration.childs_gender",  # opp 822
                "form.subcase_0.case.update.childs_gender",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["female", "f", "girl", "female_child"] else None,
            description="Number of female children",
        ),
        # MUAC Measurements - Counts and aggregations
        FieldComputation(
            name="muac_consent_count",
            path="form.case.update.muac_consent",
            paths=[
                "form.case.update.muac_consent",  # opp 814
                "form.subcase_0.case.update.muac_consent",  # opp 822
                "form.service_delivery.muac_group.muac_consent_group.muac_consent",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number of MUAC consents obtained",
        ),
        FieldComputation(
            name="muac_measurements_count",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",  # opp 814 (with _cm suffix)
                "form.subcase_0.case.update.soliciter_muac",  # opp 822 (no _cm suffix)
                "form.service_delivery.muac_group.soliciter_muac",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if _is_valid_muac(x) else None,
            description="Number of MUAC measurements taken",
        ),
        FieldComputation(
            name="avg_muac_cm",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",  # opp 814 (with _cm suffix)
                "form.subcase_0.case.update.soliciter_muac",  # opp 822 (no _cm suffix)
                "form.service_delivery.muac_group.soliciter_muac",  # opp 822 alt
            ],
            aggregation="avg",
            transform=lambda x: float(x) if _is_valid_muac(x) else None,
            description="Average MUAC measurement in cm",
        ),
        # SAM: Severe Acute Malnutrition (MUAC < 11.5 cm)
        FieldComputation(
            name="sam_count",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",  # opp 814
                "form.subcase_0.case.update.soliciter_muac",  # opp 822
                "form.service_delivery.muac_group.soliciter_muac",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if _is_valid_muac(x) and float(x) < 11.5 else None,
            description="Number of visits with SAM (MUAC < 11.5 cm)",
        ),
        # MAM: Moderate Acute Malnutrition (MUAC >= 11.5 and < 12.5 cm)
        FieldComputation(
            name="mam_count",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",  # opp 814
                "form.subcase_0.case.update.soliciter_muac",  # opp 822
                "form.service_delivery.muac_group.soliciter_muac",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if _is_valid_muac(x) and 11.5 <= float(x) < 12.5 else None,
            description="Number of visits with MAM (MUAC >= 11.5 and < 12.5 cm)",
        ),
        # Health Status Indicators - Counts
        FieldComputation(
            name="children_unwell_count",
            path="form.case.update.va_child_unwell_today",
            paths=[
                "form.case.update.va_child_unwell_today",  # opp 814
                "form.subcase_0.case.update.va_child_unwell_today",  # opp 822
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number of visits where child was unwell",
        ),
        FieldComputation(
            name="malnutrition_diagnosed_count",
            path="form.case.update.diagnosed_with_mal_past_3_months",
            paths=[
                "form.case.update.diagnosed_with_mal_past_3_months",  # opp 814
                "form.subcase_0.case.update.diagnosed_with_mal_past_3_months",  # opp 822
                "form.service_delivery.muac_group.muac_display_group_1.diagnosed_with_mal_past_3_months",  # 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number of children diagnosed with malnutrition in past 3 months",
        ),
        FieldComputation(
            name="under_malnutrition_treatment_count",
            path="form.case.update.under_treatment_for_mal",
            paths=[
                "form.case.update.under_treatment_for_mal",  # opp 814
                "form.subcase_0.case.update.under_treatment_for_mal",  # opp 822
                "form.service_delivery.muac_group.muac_display_group_1.under_treatment_for_mal",  # opp 822 alt
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number of children under malnutrition treatment",
        ),
        # Diligence Fields - Vitamin A
        FieldComputation(
            name="received_va_dose_before_count",
            path="form.case.update.received_va_dose_before",
            paths=[
                "form.case.update.received_va_dose_before",  # opp 814
                "form.subcase_0.case.update.received_va_dose_before",  # opp 822
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number of children who received VA dose before",
        ),
        FieldComputation(
            name="va_confirm_shared_knowledge_count",
            path="form.case.update.va_confirm_shared_knowledge",
            paths=[
                "form.case.update.va_confirm_shared_knowledge",  # opp 814
                "form.subcase_0.case.update.va_confirm_shared_knowledge",  # opp 822
            ],
            aggregation="count",
            # This field contains text like "va_benefits VA_side_effects", count non-empty values
            transform=lambda x: 1 if x and str(x).strip() else None,
            description="Number of times VA knowledge was shared and confirmed",
        ),
        # Diligence Fields - ORS (Oral Rehydration Solution)
        FieldComputation(
            name="ors_child_recovered_count",
            path="form.ors_group.did_the_child_recover",
            paths=[
                "form.ors_group.did_the_child_recover",  # opp 814
                "form.service_delivery.ors_group.did_the_child_recover",  # opp 822
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number of children who recovered with ORS",
        ),
        FieldComputation(
            name="ors_still_facing_symptoms_count",
            path="form.ors_group.still_facing_symptoms",
            paths=[
                "form.ors_group.still_facing_symptoms",  # opp 814
                "form.service_delivery.ors_group.still_facing_symptoms",  # opp 822
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number still facing symptoms after ORS",
        ),
        # Diligence Fields - Immunization/Vaccination
        FieldComputation(
            name="received_any_vaccine_count",
            path="form.pictures.received_any_vaccine",
            paths=[
                "form.pictures.received_any_vaccine",  # opp 814
                "form.service_delivery.pictures.received_any_vaccine",  # opp 822 guess
            ],
            aggregation="count",
            transform=lambda x: 1 if str(x).lower() in ["yes", "1", "true"] else None,
            description="Number who received any vaccine",
        ),
    ],
    histograms=[
        # MUAC distribution histogram
        # Bins: 9.5-10.5, 10.5-11.5, ..., 20.5-21.5 (12 bins of width 1.0)
        HistogramComputation(
            name="muac_distribution",
            path="form.case.update.soliciter_muac_cm",
            paths=[
                "form.case.update.soliciter_muac_cm",  # opp 814
                "form.subcase_0.case.update.soliciter_muac",  # opp 822
                "form.service_delivery.muac_group.soliciter_muac",  # opp 822 alt
            ],
            lower_bound=9.5,
            upper_bound=21.5,
            num_bins=12,
            bin_name_prefix="muac",
            transform=lambda x: float(x) if _is_valid_muac(x) else None,
            description="MUAC measurement distribution across bins (9.5-21.5 cm)",
        ),
    ],
    filters={},  # Include all visits regardless of status
)
