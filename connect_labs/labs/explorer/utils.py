"""
Utility functions for Labs Data Explorer

JSON validation, formatting, and export/import helpers.
"""

import json
from datetime import datetime

from connect_labs.labs.models import LocalLabsRecord


def validate_json_string(json_string: str) -> tuple[bool, str, dict | None]:
    """Validate a JSON string.

    Args:
        json_string: String to validate as JSON

    Returns:
        Tuple of (is_valid, error_message, parsed_data)
    """
    try:
        data = json.loads(json_string)
        return True, "", data
    except json.JSONDecodeError as e:
        return False, f"Invalid JSON: {e}", None


def format_json(data: dict | list) -> str:
    """Format data as pretty JSON string.

    Args:
        data: Dictionary or list to format

    Returns:
        Formatted JSON string with indentation
    """
    return json.dumps(data, indent=2, sort_keys=False, ensure_ascii=False)


def export_records_to_json(records: list[LocalLabsRecord]) -> str:
    """Export records to JSON string.

    Args:
        records: List of LocalLabsRecord instances

    Returns:
        JSON string representation
    """
    records_data = []
    for record in records:
        record_dict = {
            "id": record.id,
            "experiment": record.experiment,
            "type": record.type,
            "data": record.data,
            "username": record.username,
            "opportunity_id": record.opportunity_id,
            "organization_id": record.organization_id,
            "program_id": record.program_id,
            "labs_record_id": record.labs_record_id,
            # Note: date_created and date_modified don't exist on LocalLabsRecord
            # Store dates in record.data if needed
        }
        records_data.append(record_dict)

    return format_json(records_data)


def generate_export_filename(experiment: str | None = None) -> str:
    """Generate filename for export.

    Args:
        experiment: Optional experiment name to include in filename

    Returns:
        Filename string
    """
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    if experiment:
        return f"labs_records_{experiment}_{timestamp}.json"
    return f"labs_records_{timestamp}.json"


def validate_import_data(json_string: str) -> tuple[bool, str, list[dict] | None]:
    """Validate imported JSON data structure.

    Args:
        json_string: JSON string to validate

    Returns:
        Tuple of (is_valid, error_message, records_list)
    """
    # First validate it's valid JSON
    is_valid, error, data = validate_json_string(json_string)
    if not is_valid:
        return False, error, None

    # Check if it's a list
    if not isinstance(data, list):
        return False, "JSON must be a list of records", None

    # Validate each record has required fields
    required_fields = ["experiment", "type", "data"]
    for i, record in enumerate(data):
        if not isinstance(record, dict):
            return False, f"Record {i} is not a dictionary", None

        for field in required_fields:
            if field not in record:
                return False, f"Record {i} missing required field: {field}", None

    return True, "", data


def parse_date_range(start_str: str | None, end_str: str | None) -> tuple[str | None, str | None]:
    """Parse date range strings.

    Args:
        start_str: Start date string (YYYY-MM-DD)
        end_str: End date string (YYYY-MM-DD)

    Returns:
        Tuple of (start_date, end_date) or (None, None) if invalid
    """
    if not start_str and not end_str:
        return None, None

    try:
        start_date = start_str if start_str else None
        end_date = end_str if end_str else None
        return start_date, end_date
    except Exception:
        return None, None


def filter_records_by_date(
    records: list[LocalLabsRecord],
    start_date: str | None,
    end_date: str | None,
    date_field: str = "date_created",
) -> list[LocalLabsRecord]:
    """Filter records by date range.

    Args:
        records: List of records to filter
        start_date: Start date (ISO format string)
        end_date: End date (ISO format string)
        date_field: Field to filter on (note: date_created/date_modified don't exist on
            LocalLabsRecord - use 'id' for recency)

    Returns:
        Filtered list of records
    """
    if not start_date and not end_date:
        return records

    filtered = []
    for record in records:
        # Note: date_created and date_modified don't exist on LocalLabsRecord
        # If date filtering is needed, store dates in record.data
        record_date = record.data.get(date_field) if hasattr(record, "data") else getattr(record, date_field, None)
        if not record_date:
            continue

        # record_date is already a string in ISO format from API
        if start_date and record_date < start_date:
            continue
        if end_date and record_date > end_date:
            continue

        filtered.append(record)

    return filtered


def truncate_json_preview(data: dict, max_length: int = 100) -> str:
    """Create truncated JSON preview for display.

    Args:
        data: Dictionary to preview
        max_length: Maximum length of preview string

    Returns:
        Truncated JSON string
    """
    json_str = json.dumps(data, ensure_ascii=False)
    if len(json_str) <= max_length:
        return json_str
    return json_str[:max_length] + "..."


# =============================================================================
# Cache Management Utilities
# =============================================================================


def format_cache_size(size_bytes: int) -> str:
    """
    Format byte size as human-readable string.

    Args:
        size_bytes: Size in bytes

    Returns:
        Formatted string (e.g., "1.5 MB", "512 KB", "2.3 GB")
    """
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 * 1024 * 1024:
        return f"{size_bytes / (1024 * 1024):.1f} MB"
    else:
        return f"{size_bytes / (1024 * 1024 * 1024):.2f} GB"


def get_cache_type_display(cache_type: str) -> str:
    """
    Get human-readable display name for cache type.

    Args:
        cache_type: Cache type identifier ("raw", "computed_visit", "computed_flw")

    Returns:
        Human-readable name
    """
    display_names = {
        "raw": "Raw Visits",
        "computed_visit": "Computed Visits",
        "computed_flw": "Computed FLWs",
    }
    return display_names.get(cache_type, cache_type)


def truncate_config_hash(config_hash: str | None, length: int = 8) -> str:
    """
    Truncate config hash for display.

    Args:
        config_hash: Full config hash or None
        length: Number of characters to show

    Returns:
        Truncated hash or "N/A" if None
    """
    if not config_hash:
        return "N/A"
    return config_hash[:length]


def is_cache_expired(expires_at: datetime) -> bool:
    """
    Check if cache entry is expired.

    Args:
        expires_at: Expiration datetime

    Returns:
        True if expired, False otherwise
    """
    from django.utils import timezone

    return expires_at < timezone.now()


def is_cache_expiring_soon(expires_at: datetime, threshold_minutes: int = 10) -> bool:
    """
    Check if cache entry is expiring soon.

    Args:
        expires_at: Expiration datetime
        threshold_minutes: Minutes before expiration to consider "expiring soon"

    Returns:
        True if expiring soon, False otherwise
    """
    from datetime import timedelta

    from django.utils import timezone

    now = timezone.now()
    threshold = now + timedelta(minutes=threshold_minutes)
    return now < expires_at <= threshold
