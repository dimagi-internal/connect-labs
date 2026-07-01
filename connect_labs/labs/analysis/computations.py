"""
Shared field computation functions.

Provides functions to extract values from form_json and compute per-visit fields.
These are used by both the analysis pipeline backends and the audit module.
"""

import logging
from typing import Any

from connect_labs.labs.analysis.config import FieldComputation, HistogramComputation
from connect_labs.labs.analysis.models import LocalUserVisit
from connect_labs.labs.analysis.utils import extract_json_path, extract_json_path_multi

logger = logging.getLogger(__name__)


def _extract_field_value(form_json: dict, field_comp: FieldComputation) -> Any:
    """Extract value from form_json using field computation paths.

    Note: This does NOT handle custom extractors - those need the full visit dict.
    Use _extract_field_value_from_visit for fields that may have extractors.
    """
    paths = field_comp.get_paths()
    if len(paths) > 1:
        return extract_json_path_multi(form_json, paths)
    elif paths:
        return extract_json_path(form_json, paths[0])
    return None


def _extract_field_value_from_visit(visit: LocalUserVisit, field_comp: FieldComputation) -> Any:
    """
    Extract value from visit using field computation.

    Handles both path-based extraction and custom extractors.

    Args:
        visit: LocalUserVisit object (has form_json, images, etc.)
        field_comp: Field computation configuration

    Returns:
        Extracted value (before transform)
    """
    # Custom extractor takes precedence - receives full visit data dict
    if field_comp.uses_extractor and field_comp.extractor:
        return field_comp.extractor(visit._data)

    # Path-based extraction from form_json
    return _extract_field_value(visit.form_json, field_comp)


def _extract_histogram_value(form_json: dict, hist_comp) -> Any:
    """Extract value from form_json using histogram computation paths."""
    paths = hist_comp.get_paths()
    if len(paths) > 1:
        return extract_json_path_multi(form_json, paths)
    elif paths:
        return extract_json_path(form_json, paths[0])
    return None


def compute_visit_fields(
    visits: list[LocalUserVisit],
    field_comps: list[FieldComputation],
    hist_comps: list[HistogramComputation] | None = None,
) -> list[dict[str, Any]]:
    """
    Compute field values for each visit individually (no aggregation).

    Unlike compute_fields_batch which aggregates values across visits,
    this returns one result dict per visit with the extracted/transformed values.

    Supports both path-based extraction and custom extractors.
    Custom extractors receive the full visit data dict, enabling complex
    extractions like combining images array with form_json data.

    Also extracts histogram raw values (prefixed with _hist_) so they can be
    aggregated into histogram bins at the FLW level.

    Args:
        visits: List of visits to process
        field_comps: List of field computations (aggregation is ignored for visit-level)
        hist_comps: Optional list of histogram computations to extract values for

    Returns:
        List of dicts, one per visit: [{visit_id, field1, field2, ..., _hist_name, ...}, ...]
    """
    results = []

    for visit in visits:
        visit_result = {"visit_id": visit.id}
        form_json = visit.form_json

        for field_comp in field_comps:
            try:
                # Extract value - handles both path-based and custom extractors
                value = _extract_field_value_from_visit(visit, field_comp)

                # Apply transform if provided (for path-based extraction)
                # Note: extractors typically do their own transformation
                if value is not None and field_comp.transform and not field_comp.uses_extractor:
                    try:
                        value = field_comp.transform(value)
                    except Exception:
                        value = None

                # Use default if None
                if value is None:
                    value = field_comp.default

                visit_result[field_comp.name] = value

            except Exception as e:
                logger.warning(f"Failed to compute field {field_comp.name} for visit {visit.id}: {e}")
                visit_result[field_comp.name] = field_comp.default

        # Extract histogram raw values (for later aggregation)
        if hist_comps:
            for hist_comp in hist_comps:
                try:
                    value = _extract_histogram_value(form_json, hist_comp)

                    # Apply transform if provided
                    if value is not None and hist_comp.transform:
                        try:
                            value = hist_comp.transform(value)
                        except Exception:
                            value = None

                    # Convert to float if possible
                    if value is not None:
                        try:
                            value = float(value)
                        except (ValueError, TypeError):
                            value = None

                    # Store with _hist_ prefix to indicate it's a histogram raw value
                    visit_result[f"_hist_{hist_comp.name}"] = value

                except Exception as e:
                    logger.warning(f"Failed to extract histogram value {hist_comp.name} for visit {visit.id}: {e}")
                    visit_result[f"_hist_{hist_comp.name}"] = None

        results.append(visit_result)

    return results
