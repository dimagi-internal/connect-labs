"""
Analysis configuration for Audit.

Defines the FieldComputation for extracting images with question IDs
from visit form_json data.
"""

from connect_labs.labs.analysis import AnalysisPipelineConfig, FieldComputation

# Keys to skip when traversing form_json (metadata, not question data)
SKIP_KEYS = frozenset({"@xmlns", "@name", "@uiVersion", "@version", "meta", "#type", "attachments"})


def _build_filename_map(data: dict, path: str = "") -> dict[str, str]:
    """
    Build a map of all string values to their paths in a single traversal.

    This is O(m) where m = size of form_json tree, done once per visit.
    Then each image filename lookup is O(1).

    Args:
        data: Form data dict to traverse
        path: Current path prefix (for recursion)

    Returns:
        Dict mapping string values to their question paths
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


def _collect_leaf_paths(data: dict, path: str = "") -> list[str]:
    """Collect paths to all leaf scalar values in a form_json tree.

    Skips SKIP_KEYS and repeat groups (lists) — v1 only targets non-repeating
    scalar questions for the comparison-field picker.
    """
    result: list[str] = []
    if not isinstance(data, dict):
        return result

    for key, value in data.items():
        if key in SKIP_KEYS:
            continue
        current_path = f"{path}/{key}" if path else key
        if isinstance(value, dict):
            result.extend(_collect_leaf_paths(value, current_path))
        elif isinstance(value, list):
            continue  # repeat group — skipped in v1
        else:
            result.append(current_path)  # scalar leaf (str / number / None)

    return result


def extract_field_paths(form_json: dict | None) -> list[str]:
    """Return sorted, de-duped leaf scalar question paths from a visit's form_json.

    Mirrors how extract_images_with_question_ids reads form_json: it unwraps the
    top-level "form" key when present.
    """
    if not isinstance(form_json, dict):
        return []
    form_data = form_json.get("form", form_json)
    return sorted(set(_collect_leaf_paths(form_data)))


def extract_images_with_question_ids(visit_data: dict) -> list[dict]:
    """
    Extract images with question IDs from a visit.

    This is a custom extractor for FieldComputation - receives the full visit dict
    and extracts the images array enriched with question_ids from form_json.

    Optimized: builds filename->path map in a single traversal, then O(1) lookups.

    Args:
        visit_data: Full visit dict with 'form_json' and 'images' fields

    Returns:
        List of image dicts with blob_id, name, question_id, and visit metadata
    """
    form_json = visit_data.get("form_json", {})
    images = visit_data.get("images", [])

    if not images:
        return []

    # Extract visit-level metadata
    username = visit_data.get("username") or ""
    entity_name = visit_data.get("entity_name") or "No Entity"

    # Build filename->path map in a SINGLE traversal (O(m) where m=tree size)
    form_data = form_json.get("form", form_json)

    # Use form.meta.timeEnd for actual submission time; fall back to visit_date (date only)
    visit_date = form_data.get("meta", {}).get("timeEnd") or visit_data.get("visit_date") or ""
    filename_map = _build_filename_map(form_data)

    # Now each lookup is O(1) instead of O(m)
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
                "username": username,
                "visit_date": visit_date,
                "entity_name": entity_name,
            }
        )

    return result


# Field computation for extracting images with question_ids
AUDIT_IMAGES_FIELD = FieldComputation(
    name="images_with_questions",
    extractor=extract_images_with_question_ids,
    aggregation="first",
    description="Images with question IDs extracted from form_json",
)

# Config for audit image extraction
AUDIT_EXTRACTION_CONFIG = AnalysisPipelineConfig(
    grouping_key="username",
    experiment="audit",
    fields=[AUDIT_IMAGES_FIELD],
)
