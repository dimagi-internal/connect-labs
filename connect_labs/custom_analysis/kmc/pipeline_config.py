"""
KMC pipeline configuration for extracting visit-level data.

Uses the labs analysis pipeline to extract weight, photos, and other metrics
from KMC visits at the visit level (not child level - aggregation happens in views).
"""

# Import timeline config to reuse field definitions (DRY principle)
from connect_labs.custom_analysis.kmc.timeline_config import KMC_HEADER_FIELDS, KMC_WIDGETS
from connect_labs.labs.analysis import AnalysisPipelineConfig, CacheStage, FieldComputation

# Keys to skip when traversing form_json (metadata, not question data)
SKIP_KEYS = frozenset({"@xmlns", "@name", "@uiVersion", "@version", "meta", "#type", "attachments"})


def _is_valid_weight(x) -> bool:
    """Check if value is a valid weight (numeric)."""
    if not x:
        return False
    s = str(x).strip()
    return s.replace(".", "").replace("-", "").isdigit()


def _get_transform_function(transform_name: str):
    """
    Convert timeline config transform name to pipeline transform function.

    Args:
        transform_name: String like "kg_to_g", "float", "date"

    Returns:
        Transform function for FieldComputation
    """
    if transform_name == "kg_to_g":
        return lambda x: int(float(x)) if _is_valid_weight(x) else None
    elif transform_name == "float":
        return lambda x: float(x) if x else None
    elif transform_name == "date":
        return None  # No transform needed, keep as string
    return None


def _create_field_computation_from_extractor(field_name: str, extractor, description: str = None):
    """
    Create a FieldComputation from a timeline FieldExtractor.

    This eliminates duplication between timeline_config and pipeline_config.
    """
    return FieldComputation(
        name=field_name,
        path=extractor.form_paths[0] if extractor.form_paths else None,
        paths=extractor.form_paths if len(extractor.form_paths) > 1 else None,
        aggregation="first",
        transform=_get_transform_function(extractor.transform) if extractor.transform else None,
        description=description or f"{extractor.display_name} from timeline config",
    )


def _build_filename_map(data: dict, path: str = "") -> dict[str, str]:
    """
    Build a map of all string values to their paths in a single traversal.

    Args:
        data: Form data dict to traverse
        path: Current path prefix (for recursion)

    Returns:
        Dict mapping string values (filenames) to their question paths
    """
    result = {}
    if not isinstance(data, dict):
        return result

    for key, value in data.items():
        if key in SKIP_KEYS:
            continue

        current_path = f"{path}/{key}" if path else key

        if isinstance(value, str):
            # Map this string value to its path
            result[value] = current_path
        elif isinstance(value, dict):
            # Recurse into nested dict
            result.update(_build_filename_map(value, current_path))

    return result


def extract_images_with_question_ids(visit_data: dict) -> list[dict]:
    """
    Extract images with question IDs from a visit.

    This is a custom extractor for FieldComputation - receives the full visit dict
    and extracts the images array enriched with question_ids from form_json.

    Args:
        visit_data: Full visit dict with 'form_json' and 'images' fields

    Returns:
        List of image dicts with blob_id, name, question_id
    """
    form_json = visit_data.get("form_json", {})
    images = visit_data.get("images", [])

    if not images:
        return []

    # Build filename->path map in a single traversal
    form_data = form_json.get("form", form_json)
    filename_map = _build_filename_map(form_data)

    # Map each image to its question ID
    result = []
    for image in images:
        if not isinstance(image, dict):
            continue

        filename = image.get("name", "")
        question_id = filename_map.get(filename) if filename else None

        result.append(
            {
                "blob_id": image.get("blob_id", ""),
                "name": filename,
                "question_id": question_id,
            }
        )

    return result


# Build field computations from timeline widget configs (DRY - single source of truth)
def _build_widget_fields():
    """Generate FieldComputation objects from timeline widget field extractors."""
    field_computations = []
    seen_fields = set()

    # Extract fields from all widgets
    for widget_id, widget_config in KMC_WIDGETS.items():
        for field_name, extractor in widget_config.field_extractors.items():
            if field_name not in seen_fields:
                field_computations.append(
                    _create_field_computation_from_extractor(
                        field_name, extractor, f"{extractor.display_name} for {widget_id}"
                    )
                )
                seen_fields.add(field_name)

    return field_computations


# Build header fields from timeline header config
def _build_header_fields():
    """Generate FieldComputation objects from timeline header fields."""
    return [
        _create_field_computation_from_extractor(field_name, extractor, extractor.display_name)
        for field_name, extractor in KMC_HEADER_FIELDS.items()
    ]


KMC_PIPELINE_CONFIG = AnalysisPipelineConfig(
    grouping_key="username",
    experiment="kmc_timeline",
    terminal_stage=CacheStage.VISIT_LEVEL,  # Visit-level, not aggregated
    # Use beneficiary_case_id for linking instead of entity_id
    # entity_id is mother_name+phone which incorrectly combines twins' visits
    # beneficiary_case_id is unique per child
    # TODO: Revert to "entity_id" once the upstream bug is fixed
    linking_field="beneficiary_case_id",
    fields=[
        # Beneficiary case ID - unique per child, used for linking visits
        # This correctly separates twins/multiples under the same mother
        FieldComputation(
            name="beneficiary_case_id",
            paths=[
                "form.case.@case_id",  # Registration visit - main case ID
                "form.kmc_beneficiary_case_id",  # Follow-up visits store it explicitly
            ],
            aggregation="first",
            description="Beneficiary case ID (unique per child, used for linking)",
        ),
        # Child identifier for linking - using entity_id (Connect's linking field)
        # Note: entity_id from deliver unit is what links visits in Connect
        # This is kept for backwards compatibility but not used for linking when twins exist
        FieldComputation(
            name="child_entity_id",
            path="form.new_registration_du.deliver.entity_id",
            paths=[
                "form.new_registration_du.deliver.entity_id",  # Registration visit
                "form.kmc_non_pay_visit_du.deliver.entity_id",  # Follow-up visits (non-pay)
                "form.kmc_pay_visit_du.deliver.entity_id",  # Follow-up visits (pay)
            ],
            aggregation="first",
            description="Child entity ID (Connect linking field - mother_name+phone)",
        ),
        # ===== Fields generated from timeline config (DRY) =====
        # Widget fields (weight, date, time_end, visit_number, etc.)
        *_build_widget_fields(),
        # Header fields (child_name, child_dob, child_gender, mother_name, etc.)
        *_build_header_fields(),
        # ===== Additional entity/linking fields not in timeline config =====
        FieldComputation(
            name="entity_name",
            path="form.new_registration_du.deliver.entity_name",
            paths=[
                "form.new_registration_du.deliver.entity_name",
                "form.kmc_non_pay_visit_du.deliver.entity_name",
                "form.kmc_pay_visit_du.deliver.entity_name",
            ],
            aggregation="first",
            description="Entity name from deliver unit",
        ),
        # Images with question IDs (special transform that receives full visit_data)
        FieldComputation(
            name="images_with_questions",
            path="__images__",  # Special marker - will be computed from full visit context
            aggregation="first",
            transform=extract_images_with_question_ids,
            description="Images mapped to their question IDs in form",
        ),
    ],
    histograms=[],
    filters={},
)
