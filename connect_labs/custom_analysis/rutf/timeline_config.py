"""
RUTF (Ready-to-Use Therapeutic Food) Timeline Configuration.

This file defines all RUTF-specific widget configurations, layout, and field extractors
for SAM (Severe Acute Malnutrition) follow-up visits.
"""

from connect_labs.labs.configurable_ui.linking import LinkingConfig
from connect_labs.labs.configurable_ui.widgets import FieldExtractor, TimelineLayoutConfig, WidgetConfig

# Linking Configuration: How to link visits to children
RUTF_LINKING_CONFIG = LinkingConfig(
    identifier_field="rutf_case_id",
    identifier_paths=[
        "form.case.@case_id",  # Case ID for linking all follow-up visits
    ],
    opportunities=[879],  # RUTF opportunity ID
)

# Widget Definitions
RUTF_WIDGETS = {
    "visit_history": WidgetConfig(
        widget_id="visit_history",
        widget_type="visit_history",
        title="Visit History",
        field_extractors={
            "form_name": FieldExtractor(
                "Form Name",
                ["form.@name"],  # "SAM Follow up"
            ),
            "visit_type": FieldExtractor(
                "Visit Type",
                ["form.@name"],  # Form name serves as visit type
            ),
            "visit_number": FieldExtractor(
                "Visit Number",
                ["form.followup_number"],  # "1", "2", "3", etc.
            ),
            "date": FieldExtractor(
                "Date",
                ["form.fu_visit_date"],
                transform="date",
            ),
            "time_end": FieldExtractor(
                "Time End",
                ["form.meta.timeEnd", "metadata.timeEnd"],
            ),
            # Named 'weight' for compatibility with generic timeline template
            # first_followup_muac for visit 1, next_followup for subsequent visits
            "weight": FieldExtractor(
                "MUAC (cm)",
                [
                    # First visit - top level and nested paths
                    "form.first_followup_muac.soliciter_muac",
                    "form.first_followup_muac.muac_display_group_1.soliciter_sam_followup_muac_cm",
                    # Subsequent visits
                    "form.next_followup.followup_muac_display_group_1.followup_soliciter_sam_followup_muac_cm",
                ],
                transform="float",
            ),
            "photo_url": FieldExtractor(
                "Photo",
                [
                    "form.first_followup_muac.muac_display_group_1.followup_muac_photo",
                    "form.next_followup.followup_muac_display_group_1.followup_muac_photo",
                ],
            ),
        },
    ),
    "weight_chart": WidgetConfig(
        widget_id="weight_chart",
        widget_type="line_chart",
        title="MUAC Progression (cm)",
        field_extractors={
            "date": FieldExtractor(
                "Date",
                ["form.fu_visit_date"],
                transform="date",
            ),
            # Named 'weight' for compatibility with generic timeline template
            # first_followup_muac for visit 1, next_followup for subsequent visits
            "weight": FieldExtractor(
                "MUAC (cm)",
                [
                    # First visit - top level and nested paths
                    "form.first_followup_muac.soliciter_muac",
                    "form.first_followup_muac.muac_display_group_1.soliciter_sam_followup_muac_cm",
                    # Subsequent visits
                    "form.next_followup.followup_muac_display_group_1.followup_soliciter_sam_followup_muac_cm",
                ],
                transform="float",
            ),
        },
        options={
            "y_axis_label": "MUAC (cm)",
            "color_zones": [
                {"max": 11.5, "color": "#fecaca", "label": "< 11.5cm (SAM)"},  # Red zone
                {"min": 11.5, "max": 12.5, "color": "#fef3c7", "label": "11.5-12.5cm (MAM)"},  # Yellow zone
                {"min": 12.5, "color": "#d1fae5", "label": ">= 12.5cm (Normal)"},  # Green zone
            ],
        },
    ),
    "visit_image": WidgetConfig(
        widget_id="visit_image",
        widget_type="visit_image",
        title="MUAC Photo",
        field_extractors={},  # Images come from visit.images array, not field extractors
        options={
            "image_question_patterns": [
                "followup_muac_photo",
                "muac_photo",
            ],
            "placeholder_text": "No MUAC photo available for this visit",
        },
    ),
    "map": WidgetConfig(
        widget_id="map",
        widget_type="map",
        title="Visit Locations",
        field_extractors={
            "gps": FieldExtractor(
                "GPS",
                [
                    "form.location_blocks.gps_block.normalized_location",
                    "metadata.location",
                    "form.meta.location.#text",
                ],
            ),
            "visit_number": FieldExtractor(
                "Visit",
                ["form.followup_number"],
            ),
        },
        options={
            "marker_colors": {
                "registration": "#3b82f6",  # Blue
                "visit": "#10b981",  # Green
                "recovered": "#22c55e",  # Bright green
            },
        },
    ),
    "detail_panel": WidgetConfig(
        widget_id="detail_panel",
        widget_type="detail_panel",
        title="Visit Details",
        field_extractors={
            # MUAC Measurement (named 'weight' for compatibility with generic timeline template)
            # first_followup_muac for visit 1, next_followup for subsequent visits
            "weight": FieldExtractor(
                "MUAC (cm)",
                [
                    # First visit - top level and nested paths
                    "form.first_followup_muac.soliciter_muac",
                    "form.first_followup_muac.muac_display_group_1.soliciter_sam_followup_muac_cm",
                    # Subsequent visits
                    "form.next_followup.followup_muac_display_group_1.followup_soliciter_sam_followup_muac_cm",
                ],
                transform="float",
            ),
            "muac_color": FieldExtractor(
                "MUAC Status",
                ["form.final_muac_color"],  # Red, Yellow, Green
            ),
            "child_status": FieldExtractor(
                "Child Status",
                [
                    # First visit
                    "form.first_followup_muac.muac_display_group_1.child_status_reported",
                    # Subsequent visits
                    "form.next_followup.followup_muac_display_group_1.followup_child_status_reported",
                ],
            ),
            "child_recovered": FieldExtractor(
                "Recovered",
                ["form.child_recovered"],
            ),
            # Facility Visit Info
            "visited_facility": FieldExtractor(
                "Visited Facility",
                ["form.next_followup.followup_visited_facility"],
            ),
            "facility_name": FieldExtractor(
                "Facility Name",
                ["form.next_followup.followup_with_visit_facility.followup_facility_visited"],
            ),
            "treatment_received": FieldExtractor(
                "Treatment Received",
                ["form.next_followup.followup_with_visit_facility.followup_treatment_received"],
            ),
            "treatment_adherence": FieldExtractor(
                "Treatment Adherence",
                ["form.next_followup.followup_with_visit_facility.followup_treatment_adherence"],
            ),
            # Child Availability
            "child_available": FieldExtractor(
                "Child Available",
                ["form.consent_followup_visit.child_available"],
            ),
            "consent_next_followup": FieldExtractor(
                "Consent Next Visit",
                ["form.consent_followup_visit.next_followup_consent"],
            ),
            # Visit Info
            "visit_location": FieldExtractor(
                "Location (DU)",
                ["form.du_name"],
            ),
        },
        options={
            "sections": [
                {"title": "MUAC Assessment", "fields": ["weight", "muac_color", "child_status", "child_recovered"]},
                {
                    "title": "Facility Visit",
                    "fields": ["visited_facility", "facility_name", "treatment_received", "treatment_adherence"],
                },
                {"title": "Visit Info", "fields": ["child_available", "consent_next_followup", "visit_location"]},
            ],
        },
    ),
}

# Layout Configuration: Which widgets in which columns
RUTF_LAYOUT = TimelineLayoutConfig(
    left_widgets=["visit_history"],
    center_widgets=["weight_chart", "visit_image"],
    right_widgets=["detail_panel", "map"],
)

# Child Header Fields: Data shown in the header banner
RUTF_HEADER_FIELDS = {
    "child_name": FieldExtractor(
        "Child Name",
        ["form.additional_case_info.child_name", "form.sam_followup.deliver.entity_name"],
    ),
    "child_dob": FieldExtractor(
        "DOB",
        ["form.additional_case_info.childs_dob"],
        transform="date",
    ),
    "child_gender": FieldExtractor(
        "Gender",
        ["form.additional_case_info.childs_gender"],
    ),
    "child_age_months": FieldExtractor(
        "Age (months)",
        ["form.additional_case_info.childs_age_in_month"],
    ),
    "household_name": FieldExtractor(
        "Household",
        ["form.additional_case_info.household_name"],
    ),
    "household_phone": FieldExtractor(
        "Phone",
        ["form.additional_case_info.household_phone"],
    ),
    "village": FieldExtractor(
        "Village",
        ["form.additional_case_info.hh_village_name"],
    ),
    "muac_status": FieldExtractor(
        "Current MUAC Status",
        ["form.final_muac_color"],
    ),
    "child_recovered": FieldExtractor(
        "Recovered",
        ["form.child_recovered"],
    ),
}

# Aliases for generic view compatibility (matches KMC naming convention)
KMC_WIDGETS = RUTF_WIDGETS
KMC_LAYOUT = RUTF_LAYOUT
KMC_HEADER_FIELDS = RUTF_HEADER_FIELDS
