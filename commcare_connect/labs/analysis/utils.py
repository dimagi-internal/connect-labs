"""
Utility functions for analysis framework.

Provides JSON path extraction, type coercion, aggregation helpers,
and shared cache utilities.
"""

import hashlib
import logging
from typing import Any

logger = logging.getLogger(__name__)

# =============================================================================
# Cache Constants and Utilities
# =============================================================================

# TTL for Django cache backend (1 hour)
DJANGO_CACHE_TTL = 3600


def get_config_hash(config) -> str:
    """
    Generate a hash of the analysis config to detect changes.

    Includes field paths, aggregations, histograms, and grouping.
    EXCLUDES filters - filters are applied at query time, not cache time.
    This allows a single cache to serve all filtered queries efficiently.

    Args:
        config: AnalysisPipelineConfig instance

    Returns:
        12-character hash string
    """
    # Build a string representation of the config
    parts = []

    # Add field computations
    for field in config.fields:
        parts.append(f"field:{field.name}:{field.path}:{field.aggregation}")
        # Include transform function bytecode if present (detects lambda changes)
        if field.transform:
            try:
                parts.append(f"transform:{field.transform.__code__.co_code.hex()}")
            except AttributeError:
                parts.append(f"transform:{str(field.transform)}")

    # Add histogram computations
    for hist in config.histograms:
        parts.append(f"hist:{hist.name}:{hist.path}:{hist.lower_bound}:{hist.upper_bound}:{hist.num_bins}")
        if hist.transform:
            try:
                parts.append(f"hist_transform:{hist.transform.__code__.co_code.hex()}")
            except AttributeError:
                parts.append(f"hist_transform:{str(hist.transform)}")

    # OPTIMIZATION: Filters are NOT included in hash - they're applied at query time
    # This allows timeline views to share the same cache and just filter by entity_id

    # Add grouping key
    parts.append(f"grouping:{config.grouping_key}")

    # Joins: changing the join graph or the joined fields must invalidate cache
    # because join inputs feed into per-row aggregations. We do NOT include
    # resolved_config_hash here — the joined pipeline's hash is structural
    # (its own fields/grouping change), and including it would create circular
    # dependency on hash order between sibling pipelines.
    for j in getattr(config, "joins", []) or []:
        parts.append(f"join:{j.from_alias}:{j.local_key}:{j.remote_key_field}")
        for jf in j.fields:
            parts.append(f"join_field:{jf.get('name')}:{jf.get('from')}")

    # Generate hash
    config_str = "|".join(parts)
    return hashlib.md5(config_str.encode()).hexdigest()[:12]


def resolve_join_hashes(configs_by_alias: dict) -> None:
    """Populate `resolved_config_hash` on every JoinConfig in `configs_by_alias`.

    The orchestration layer holds a mapping `{alias: AnalysisPipelineConfig}`
    for sibling pipelines in a workflow. JOIN SQL needs the joined pipeline's
    `config_hash` so it can read the right rows from `labs_computed_visit_cache`.

    This walker iterates each config's `joins`, looks up the sibling by alias,
    computes its hash, and patches `resolved_config_hash` in place. Joined
    pipelines must already be present in the dict — a missing alias is a hard
    error (no silent fallback that would produce wrong data).

    Args:
        configs_by_alias: Mapping of pipeline alias to AnalysisPipelineConfig.

    Raises:
        ValueError: If a join references an unknown alias.
    """
    # Pre-compute hashes once per config (dedupe across multiple joins of same alias).
    hashes_by_alias = {alias: get_config_hash(cfg) for alias, cfg in configs_by_alias.items()}
    for alias, cfg in configs_by_alias.items():
        for j in getattr(cfg, "joins", None) or []:
            if j.from_alias not in hashes_by_alias:
                raise ValueError(
                    f"Pipeline {alias!r} declares JOIN from_alias={j.from_alias!r}, "
                    f"but no sibling pipeline with that alias was supplied. "
                    f"Known aliases: {sorted(hashes_by_alias)}"
                )
            j.resolved_config_hash = hashes_by_alias[j.from_alias]


# =============================================================================
# JSON Path Extraction
# =============================================================================


def extract_json_path(json_obj: dict | Any, path: str) -> Any:
    """
    Extract value from nested JSON using dot-notation path.

    Supports paths like:
        "form.building_count" -> json_obj["form"]["building_count"]
        "form.additional_case_info.childs_age_in_month"
        "metadata.location"

    Args:
        json_obj: JSON object (dict) or any object
        path: Dot-separated path string

    Returns:
        Extracted value or None if path doesn't exist

    Examples:
        >>> data = {"form": {"building_count": 5}}
        >>> extract_json_path(data, "form.building_count")
        5

        >>> data = {"form": {"case": {"update": {"MUAC_consent": "yes"}}}}
        >>> extract_json_path(data, "form.case.update.MUAC_consent")
        'yes'

        >>> extract_json_path(data, "form.missing.path")
        None
    """
    if not isinstance(json_obj, dict):
        return None

    if not path:
        return None

    parts = path.split(".")
    current = json_obj

    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
        if current is None:
            return None

    return current


def extract_json_path_multi(json_obj: dict | Any, paths: list[str]) -> Any:
    """
    Extract value from nested JSON, trying multiple paths in order.

    Tries each path in order and returns the first non-None value found.
    Useful for handling different form structures across opportunities.

    Args:
        json_obj: JSON object (dict) or any object
        paths: List of dot-separated path strings to try in order

    Returns:
        First non-None value found, or None if all paths fail

    Examples:
        >>> data = {"form": {"case": {"update": {"muac_cm": 12.5}}}}
        >>> extract_json_path_multi(data, ["form.old_path", "form.case.update.muac_cm"])
        12.5

        >>> data = {"form": {"subcase_0": {"case": {"update": {"muac": 11.0}}}}}
        >>> extract_json_path_multi(data, [
        ...     "form.case.update.muac_cm",  # Doesn't exist
        ...     "form.subcase_0.case.update.muac",  # Found!
        ... ])
        11.0
    """
    for path in paths:
        value = extract_json_path(json_obj, path)
        if value is not None:
            return value
    return None


def safe_int(value: Any, default: int = 0) -> int:
    """
    Safely convert value to integer.

    Args:
        value: Value to convert
        default: Default value if conversion fails

    Returns:
        Integer value or default

    Examples:
        >>> safe_int("5")
        5
        >>> safe_int("5.7")
        5
        >>> safe_int(None)
        0
        >>> safe_int("invalid", default=-1)
        -1
    """
    if value is None:
        return default

    try:
        if isinstance(value, str):
            # Try to handle decimal strings
            return int(float(value))
        return int(value)
    except (ValueError, TypeError):
        return default


def safe_float(value: Any, default: float = 0.0) -> float:
    """
    Safely convert value to float.

    Args:
        value: Value to convert
        default: Default value if conversion fails

    Returns:
        Float value or default

    Examples:
        >>> safe_float("5.5")
        5.5
        >>> safe_float("5")
        5.0
        >>> safe_float(None)
        0.0
        >>> safe_float("invalid", default=-1.0)
        -1.0
    """
    if value is None:
        return default

    try:
        return float(value)
    except (ValueError, TypeError):
        return default


def safe_bool(value: Any, default: bool = False) -> bool:
    """
    Safely convert value to boolean.

    Args:
        value: Value to convert
        default: Default value if value is None

    Returns:
        Boolean value

    Examples:
        >>> safe_bool("yes")
        True
        >>> safe_bool("no")
        False
        >>> safe_bool("1")
        True
        >>> safe_bool(None)
        False
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, str):
        return value.lower() in ("yes", "true", "1", "y")

    return bool(value)


def aggregate_sum(values: list[Any]) -> float:
    """
    Sum numeric values, ignoring None and non-numeric.

    Args:
        values: List of values to sum

    Returns:
        Sum of numeric values

    Examples:
        >>> aggregate_sum([1, 2, 3])
        6.0
        >>> aggregate_sum([1, None, 3])
        4.0
        >>> aggregate_sum([1, "2", 3])
        6.0
        >>> aggregate_sum([])
        0.0
    """
    total = 0.0
    for value in values:
        if value is not None:
            try:
                total += float(value)
            except (ValueError, TypeError):
                continue
    return total


def aggregate_avg(values: list[Any]) -> float | None:
    """
    Average numeric values, ignoring None and non-numeric.

    Args:
        values: List of values to average

    Returns:
        Average of numeric values, or None if no valid values

    Examples:
        >>> aggregate_avg([1, 2, 3])
        2.0
        >>> aggregate_avg([1, None, 3])
        2.0
        >>> aggregate_avg([])
        None
    """
    numeric_values = []
    for value in values:
        if value is not None:
            try:
                numeric_values.append(float(value))
            except (ValueError, TypeError):
                continue

    if not numeric_values:
        return None

    return sum(numeric_values) / len(numeric_values)


def aggregate_count(values: list[Any]) -> int:
    """
    Count non-None values.

    Args:
        values: List of values to count

    Returns:
        Count of non-None values

    Examples:
        >>> aggregate_count([1, 2, 3])
        3
        >>> aggregate_count([1, None, 3])
        2
        >>> aggregate_count([])
        0
    """
    return sum(1 for v in values if v is not None)


def aggregate_min(values: list[Any]) -> float | None:
    """
    Find minimum numeric value, ignoring None and non-numeric.

    Args:
        values: List of values

    Returns:
        Minimum value or None if no valid values

    Examples:
        >>> aggregate_min([1, 2, 3])
        1.0
        >>> aggregate_min([1, None, 3])
        1.0
        >>> aggregate_min([])
        None
    """
    numeric_values = []
    for value in values:
        if value is not None:
            try:
                numeric_values.append(float(value))
            except (ValueError, TypeError):
                continue

    if not numeric_values:
        return None

    return min(numeric_values)


def aggregate_max(values: list[Any]) -> float | None:
    """
    Find maximum numeric value, ignoring None and non-numeric.

    Args:
        values: List of values

    Returns:
        Maximum value or None if no valid values

    Examples:
        >>> aggregate_max([1, 2, 3])
        3.0
        >>> aggregate_max([1, None, 3])
        3.0
        >>> aggregate_max([])
        None
    """
    numeric_values = []
    for value in values:
        if value is not None:
            try:
                numeric_values.append(float(value))
            except (ValueError, TypeError):
                continue

    if not numeric_values:
        return None

    return max(numeric_values)


def aggregate_list(values: list[Any]) -> list[Any]:
    """
    Collect unique non-None values.

    Args:
        values: List of values

    Returns:
        List of unique non-None values

    Examples:
        >>> aggregate_list([1, 2, 2, 3])
        [1, 2, 3]
        >>> aggregate_list([1, None, 2])
        [1, 2]
        >>> aggregate_list([])
        []
    """
    seen = set()
    result = []
    for value in values:
        if value is not None and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def aggregate_first(values: list[Any]) -> Any | None:
    """
    Get first non-None value.

    Args:
        values: List of values

    Returns:
        First non-None value or None if all None

    Examples:
        >>> aggregate_first([None, 2, 3])
        2
        >>> aggregate_first([1, 2, 3])
        1
        >>> aggregate_first([None, None])
        None
    """
    for value in values:
        if value is not None:
            return value
    return None


def aggregate_last(values: list[Any]) -> Any | None:
    """
    Get last non-None value.

    Args:
        values: List of values

    Returns:
        Last non-None value or None if all None

    Examples:
        >>> aggregate_last([1, 2, None])
        2
        >>> aggregate_last([1, 2, 3])
        3
        >>> aggregate_last([None, None])
        None
    """
    for value in reversed(values):
        if value is not None:
            return value
    return None


def aggregate_count_unique(values: list[Any]) -> int:
    """
    Count unique non-None values.

    Args:
        values: List of values

    Returns:
        Count of unique non-None values

    Examples:
        >>> aggregate_count_unique([1, 2, 2, 3])
        3
        >>> aggregate_count_unique([1, None, 2])
        2
        >>> aggregate_count_unique([])
        0
    """
    return len({v for v in values if v is not None})


def apply_aggregation(aggregation_type: str, values: list[Any]) -> Any:
    """
    Apply aggregation function based on type string.

    Args:
        aggregation_type: Type of aggregation ("sum", "avg", "count", etc.)
        values: List of values to aggregate

    Returns:
        Aggregated result

    Raises:
        ValueError: If aggregation type is unknown

    Examples:
        >>> apply_aggregation("sum", [1, 2, 3])
        6.0
        >>> apply_aggregation("avg", [1, 2, 3])
        2.0
        >>> apply_aggregation("count", [1, None, 3])
        2
    """
    aggregation_map = {
        "sum": aggregate_sum,
        "avg": aggregate_avg,
        "count": aggregate_count,
        "min": aggregate_min,
        "max": aggregate_max,
        "list": aggregate_list,
        "first": aggregate_first,
        "last": aggregate_last,
        "count_unique": aggregate_count_unique,
    }

    if aggregation_type not in aggregation_map:
        raise ValueError(f"Unknown aggregation type: {aggregation_type}")

    return aggregation_map[aggregation_type](values)
