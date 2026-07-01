"""
KMC Timeline Configuration.

This file defines all KMC-specific widget configurations, layout, and field extractors.
The same generic framework code is used - only this configuration changes per program.
"""

from connect_labs.labs.configurable_ui.linking import LinkingConfig
from connect_labs.labs.configurable_ui.widgets import FieldExtractor, TimelineLayoutConfig, WidgetConfig

# Linking Configuration: How to link visits to children
KMC_LINKING_CONFIG = LinkingConfig(
    identifier_field="kmc_beneficiary_case_id",
    identifier_paths=[
        "form.case.@case_id",  # Registration visit
        "form.kmc_beneficiary_case_id",  # Follow-up visits
    ],
    opportunities=[523],
)

# Widget Definitions
KMC_WIDGETS = {
    "visit_history": WidgetConfig(
        widget_id="visit_history",
        widget_type="visit_history",
        title="Visit History",
        field_extractors={
            "form_name": FieldExtractor(
                "Form Name",
                ["form.@name"],  # "Register KMC Beneficiary" or "Record Visit Details"
            ),
            "visit_type": FieldExtractor(
                "Visit Type",
                ["form.grp_kmc_visit.visit_type"],  # "KMC Visit" or "Additional Visit"
            ),
            "visit_number": FieldExtractor(
                "Visit Number",
                ["form.grp_kmc_visit.visit_number"],  # "1", "2", "Additional", etc.
            ),
            "date": FieldExtractor("Date", ["form.grp_kmc_visit.visit_date", "form.reg_date"], transform="date"),
            "time_end": FieldExtractor("Time End", ["form.meta.timeEnd", "timeEnd"]),
            "weight": FieldExtractor(
                "Weight",
                ["form.anthropometric.child_weight_visit", "form.child_details.birth_weight_reg.child_weight_reg"],
                transform="kg_to_g",
            ),
            "photo_url": FieldExtractor(
                "Photo",
                ["form.anthropometric.upload_weight_image", "form.child_details.upload_weight_image"],
            ),
        },
    ),
    "weight_chart": WidgetConfig(
        widget_id="weight_chart",
        widget_type="line_chart",
        title="Weight Progression (grams)",
        field_extractors={
            "date": FieldExtractor("Date", ["form.grp_kmc_visit.visit_date", "form.reg_date"], transform="date"),
            "weight": FieldExtractor(
                "Weight",
                ["form.anthropometric.child_weight_visit", "form.child_details.birth_weight_reg.child_weight_reg"],
                transform="kg_to_g",
            ),
        },
        options={
            "y_axis_label": "Weight (grams)",
            "birth_weight_path": "form.child_weight_birth",
            "color_zones": [
                {"max": 2500, "color": "#fef3c7", "label": "< 2.5kg"},  # Yellow zone
                {"min": 2500, "color": "#d1fae5", "label": ">= 2.5kg"},  # Green zone
            ],
        },
    ),
    "map": WidgetConfig(
        widget_id="map",
        widget_type="map",
        title="Visit Locations",
        field_extractors={
            "gps": FieldExtractor("GPS", ["form.visit_gps_manual", "form.reg_gps", "metadata.location"]),
            "visit_number": FieldExtractor("Visit", ["form.grp_kmc_visit.visit_number"]),
        },
        options={
            "marker_colors": {
                "registration": "#3b82f6",  # Blue
                "visit": "#10b981",  # Green
                "discharge": "#ef4444",  # Red
            },
        },
    ),
    "detail_panel": WidgetConfig(
        widget_id="detail_panel",
        widget_type="detail_panel",
        title="Visit Details",
        field_extractors={
            # Anthropometric
            "weight": FieldExtractor("Weight (g)", ["form.anthropometric.child_weight_visit"], transform="kg_to_g"),
            "height": FieldExtractor("Height (cm)", ["form.anthropometric.child_height"], transform="float"),
            "birth_weight": FieldExtractor("Birth Weight (g)", ["form.child_weight_birth"], transform="kg_to_g"),
            # KMC Practice
            "kmc_hours": FieldExtractor("KMC Hours (24h)", ["form.KMC_24-Hour_Recall.kmc_hours"]),
            "kmc_providers": FieldExtractor("KMC Provider", ["form.KMC_24-Hour_Recall.kmc_providers"]),
            "baby_position": FieldExtractor("Baby Position", ["form.kmc_positioning_checklist.baby_position"]),
            # Feeding
            "feeding_provided": FieldExtractor("Feeding Type", ["form.KMC_24-Hour_Recall.feeding_provided"]),
            "successful_feeds": FieldExtractor(
                "Successful Feeds (24h)",
                ["form.danger_signs_checklist.successful_feeds_in_last_24_hours"],
            ),
            # Danger Signs
            "temperature": FieldExtractor(
                "Temperature (C)",
                ["form.danger_signs_checklist.svn_temperature"],
                transform="float",
            ),
            "breath_count": FieldExtractor(
                "Respirations/min",
                ["form.danger_signs_checklist.child_breath_count"],
            ),
            "danger_signs": FieldExtractor("Danger Signs", ["form.danger_signs_checklist.danger_sign_list"]),
            # Visit Info
            "visit_location": FieldExtractor("Location", ["form.visit_location", "form.reg_location"]),
            "visit_timeliness": FieldExtractor("Timeliness", ["form.grp_kmc_visit.visit_timeliness"]),
            "kmc_status": FieldExtractor(
                "Status",
                ["form.grp_kmc_beneficiary.kmc_status", "form.kmc_status"],
            ),
        },
        options={
            "sections": [
                {"title": "Anthropometric", "fields": ["weight", "height", "birth_weight"]},
                {"title": "KMC Practice", "fields": ["kmc_hours", "kmc_providers", "baby_position"]},
                {"title": "Feeding", "fields": ["feeding_provided", "successful_feeds"]},
                {"title": "Vital Signs", "fields": ["temperature", "breath_count", "danger_signs"]},
                {"title": "Visit Info", "fields": ["visit_location", "visit_timeliness", "kmc_status"]},
            ],
        },
    ),
}

# Layout Configuration: Which widgets in which columns
KMC_LAYOUT = TimelineLayoutConfig(
    left_widgets=["visit_history"],
    center_widgets=["weight_chart", "map"],
    right_widgets=["detail_panel"],
)

# Child Header Fields: Data shown in the header banner
KMC_HEADER_FIELDS = {
    "child_name": FieldExtractor("Child Name", ["form.child_details.child_name", "form.svn_name"]),
    "child_dob": FieldExtractor("DOB", ["form.child_DOB", "form.child_details.child_DOB"], transform="date"),
    "child_gender": FieldExtractor("Gender", ["form.child_details.child_gender"]),
    "birth_weight": FieldExtractor(
        "Birth Weight",
        ["form.child_details.birth_weight_group.child_weight_birth", "form.child_weight_birth"],
        transform="kg_to_g",
    ),
    "reg_date": FieldExtractor("Registration Date", ["form.reg_date"], transform="date"),
    "kmc_status": FieldExtractor("KMC Status", ["form.kmc_status", "form.case.update.kmc_status"]),
    "mother_name": FieldExtractor(
        "Mother",
        ["form.mothers_details.mother_name", "form.kmc_beneficiary_name"],
    ),
    "mother_phone": FieldExtractor(
        "Phone",
        ["form.mothers_details.mothers_phone_number", "form.deduplication_block.mothers_phone_number"],
    ),
    "mother_age": FieldExtractor("Mother Age", ["form.mothers_details.mother_age", "form.mother_age"]),
    "village": FieldExtractor("Village", ["form.mothers_details.village"]),
    "subcounty": FieldExtractor("Subcounty", ["form.mothers_details.subcounty", "form.subcounty"]),
}
