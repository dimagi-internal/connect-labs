"""
MBW pipeline configuration for GPS analysis.

Extracts GPS coordinates and case linking information for distance analysis.
"""

from commcare_connect.labs.analysis import AnalysisPipelineConfig, CacheStage, FieldComputation


def _safe_parse_int(x):
    """Safe int parse; SQL backend matches 'simple_int' pattern from source inspection."""
    try:
        return int(x) if x else None  # int(x) if x else None
    except (ValueError, TypeError):
        return None


MBW_GPS_PIPELINE_CONFIG = AnalysisPipelineConfig(
    grouping_key="username",
    experiment="mbw_gps",
    terminal_stage=CacheStage.VISIT_LEVEL,  # Visit-level for GPS analysis
    linking_field="entity_id",  # Use entity_id for linking visits
    fields=[
        # GPS location - extract from form metadata (path-based, not Python extractor,
        # to avoid loading form_json for all visits into memory)
        FieldComputation(
            name="gps_location",
            paths=[
                "form.meta.location.#text",  # dict location with #text key
                "form.meta.location",  # string location fallback
            ],
            aggregation="first",
            description="GPS location string (lat lon altitude accuracy)",
        ),
        # Case ID - the visit's direct case
        FieldComputation(
            name="case_id",
            path="form.case.@case_id",
            aggregation="first",
            description="Direct case ID for this visit",
        ),
        # Parent/Mother case ID - for linking related visits
        FieldComputation(
            name="mother_case_id",
            path="form.parents.parent.case.@case_id",
            aggregation="first",
            description="Parent/mother case ID for linking",
        ),
        # Form name - to identify visit type
        FieldComputation(
            name="form_name",
            path="form.@name",
            aggregation="first",
            description="Form name (visit type)",
        ),
        # Visit datetime - for ordering and daily grouping
        FieldComputation(
            name="visit_datetime",
            path="form.meta.timeEnd",
            aggregation="first",
            description="Visit datetime for ordering",
        ),
        # Entity ID from deliver unit
        FieldComputation(
            name="entity_id_deliver",
            paths=[
                "form.mbw_visit.deliver.entity_id",
                "form.visit_completion.mbw_visit.deliver.entity_id",
            ],
            aggregation="first",
            description="Entity ID from deliver unit",
        ),
        # Entity name from deliver unit
        FieldComputation(
            name="entity_name",
            paths=[
                "form.mbw_visit.deliver.entity_name",
                "form.visit_completion.mbw_visit.deliver.entity_name",
            ],
            aggregation="first",
            description="Entity name (mother name + phone)",
        ),
        # Parity from ANC visit form
        FieldComputation(
            name="parity",
            path="form.confirm_visit_information.parity__of_live_births_or_stillbirths_after_24_weeks",
            aggregation="first",
            description="Parity from ANC visit form",
        ),
        # ANC completion date from ANC visit form
        FieldComputation(
            name="anc_completion_date",
            path="form.visit_completion.anc_completion_date",
            aggregation="first",
            description="ANC completion date from ANC visit form",
        ),
        # PNC completion date from PNC visit form
        FieldComputation(
            name="pnc_completion_date",
            path="form.pnc_completion_date",
            aggregation="first",
            description="PNC completion date from PNC visit form",
        ),
        # Baby date of birth from PNC form
        FieldComputation(
            name="baby_dob",
            path="form.capture_the_following_birth_details.baby_dob",
            aggregation="first",
            description="Baby date of birth from PNC form",
        ),
        # App build version - for filtering GPS metrics by app version
        FieldComputation(
            name="app_build_version",
            path="form.meta.app_build_version",
            transform=_safe_parse_int,
            aggregation="first",
            description="App build version (integer)",
        ),
        # Breastfeeding status (multi-choice; "ebf" = exclusive breastfeeding)
        FieldComputation(
            name="bf_status",
            paths=[
                "form.feeding_history.pnc_current_bf_status",
                "form.feeding_history.oneweek_current_bf_status",
                "form.feeding_history.onemonth_current_bf_status",
                "form.feeding_history.threemonth_current_bf_status",
                "form.feeding_history.sixmonth_current_bf_status",
            ],
            aggregation="first",
            description="Current breastfeeding status (multi-choice, space-separated)",
        ),
    ],
    histograms=[],
    filters={},
)
