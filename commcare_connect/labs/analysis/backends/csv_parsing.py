"""
Shared CSV parsing utilities for analysis backends.

Provides unified parsing of Connect API CSV responses.
Both SQL and Python/Redis backends use this module.
"""

import ast
import io
import json
import logging
from collections.abc import Generator

import pandas as pd

logger = logging.getLogger(__name__)

# Column definitions from Connect API UserVisitDataSerializer
ALL_COLUMNS = [
    "id",
    "opportunity_id",
    "username",
    "deliver_unit",
    "entity_id",
    "entity_name",
    "visit_date",
    "status",
    "reason",
    "location",
    "flagged",
    "flag_reason",
    "form_json",
    "completed_work",
    "status_modified_date",
    "review_status",
    "review_created_on",
    "justification",
    "date_created",
    "completed_work_id",
    "deliver_unit_id",
    "images",
]

# Columns to load in slim mode (excludes form_json for memory efficiency)
SLIM_COLUMNS = [col for col in ALL_COLUMNS if col != "form_json"]


def _parse_form_json(raw_json: str) -> dict:
    """
    Parse form_json from CSV string to Python dict.

    The API returns form_json as Python repr format (single quotes, Python literals)
    not valid JSON (double quotes, null/true/false). We try JSON first, then ast.literal_eval.
    """
    if not raw_json or pd.isna(raw_json):
        return {}

    try:
        return json.loads(raw_json)
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        return ast.literal_eval(raw_json)
    except (ValueError, SyntaxError):
        logger.warning(f"Failed to parse form_json: {str(raw_json)[:100]}...")
        return {}


def _parse_images(raw_images: str) -> list:
    """Parse images column from CSV string to Python list."""
    if not raw_images or pd.isna(raw_images):
        return []

    # Try JSON first (handles null/true/false), then Python repr (single quotes)
    try:
        parsed = json.loads(raw_images)
        if isinstance(parsed, list):
            return parsed
        if parsed is None:
            return []
        return [parsed]
    except (json.JSONDecodeError, TypeError):
        pass

    try:
        parsed = ast.literal_eval(raw_images)
        if isinstance(parsed, list):
            return parsed
        if parsed is None:
            return []
        return [parsed]
    except (ValueError, SyntaxError):
        return []


def _row_to_visit_dict(row: pd.Series, opportunity_id: int, include_form_json: bool = True) -> dict:
    """
    Convert a pandas row to a visit dict.

    Args:
        row: pandas Series from DataFrame
        opportunity_id: Opportunity ID (fallback if not in row)
        include_form_json: If True, parse and include form_json; if False, use empty dict
    """

    def get_str(col: str) -> str | None:
        return str(row[col]) if col in row.index and pd.notna(row[col]) else None

    def get_int(col: str) -> int | None:
        if col in row.index and pd.notna(row[col]):
            try:
                return int(row[col])
            except (ValueError, TypeError):
                return None
        return None

    def get_bool(col: str) -> bool:
        return bool(row[col]) if col in row.index and pd.notna(row[col]) else False

    # Parse form_json if requested
    form_json = {}
    xform_id = None
    if include_form_json and "form_json" in row.index:
        form_json = _parse_form_json(row["form_json"])
        if form_json:
            xform_id = form_json.get("id")

    # Parse images
    images = []
    if "images" in row.index:
        images = _parse_images(row["images"])

    return {
        "id": get_int("id"),
        "xform_id": xform_id,
        "opportunity_id": get_int("opportunity_id") or opportunity_id,
        "username": get_str("username"),
        "deliver_unit": get_str("deliver_unit"),
        "deliver_unit_id": get_int("deliver_unit_id"),
        "entity_id": get_str("entity_id"),
        "entity_name": get_str("entity_name"),
        "visit_date": get_str("visit_date"),
        "status": get_str("status"),
        "reason": get_str("reason"),
        "location": get_str("location"),
        "flagged": get_bool("flagged"),
        "flag_reason": get_str("flag_reason"),
        "form_json": form_json,
        "completed_work": get_str("completed_work"),
        "status_modified_date": get_str("status_modified_date"),
        "review_status": get_str("review_status"),
        "review_created_on": get_str("review_created_on"),
        "justification": get_str("justification"),
        "date_created": get_str("date_created"),
        "completed_work_id": get_int("completed_work_id"),
        "images": images,
    }


def parse_csv_bytes(
    csv_bytes: bytes,
    opportunity_id: int,
    skip_form_json: bool = False,
    filter_visit_ids: set[int] | None = None,
    chunksize: int = 1000,
) -> list[dict]:
    """
    Parse CSV bytes into list of visit dicts.

    Always uses chunked parsing for memory efficiency.

    Args:
        csv_bytes: Raw CSV bytes from Connect API
        opportunity_id: Opportunity ID (fallback if not in CSV)
        skip_form_json: If True, exclude form_json column (~90% memory reduction)
        filter_visit_ids: If provided, only return visits with these IDs
        chunksize: Number of rows per chunk (default 1000)

    Returns:
        List of visit dicts

    Examples:
        # Full parse (all visits with form_json)
        visits = parse_csv_bytes(csv_bytes, opp_id)

        # Slim mode (all visits without form_json)
        visits = parse_csv_bytes(csv_bytes, opp_id, skip_form_json=True)

        # Filtered (specific visits with form_json)
        visits = parse_csv_bytes(csv_bytes, opp_id, filter_visit_ids={1, 2, 3})
    """
    # Determine columns to load
    usecols = SLIM_COLUMNS if skip_form_json else None

    visits = []

    try:
        csv_reader = pd.read_csv(io.BytesIO(csv_bytes), usecols=usecols, chunksize=chunksize, on_bad_lines="warn")
    except ValueError as e:
        # Handle case where some slim columns don't exist in CSV
        if "not in list" in str(e) and skip_form_json:
            logger.warning(f"Some slim columns not found in CSV, falling back to all columns: {e}")
            csv_reader = pd.read_csv(io.BytesIO(csv_bytes), chunksize=chunksize, on_bad_lines="warn")
        else:
            raise

    for chunk in csv_reader:
        # Filter by visit IDs if specified
        if filter_visit_ids is not None and "id" in chunk.columns:
            chunk = chunk[chunk["id"].isin(filter_visit_ids)]

        # Convert rows to dicts
        for _, row in chunk.iterrows():
            visit = _row_to_visit_dict(row, opportunity_id, include_form_json=not skip_form_json)
            visits.append(visit)

    if filter_visit_ids is not None:
        logger.info(f"Parsed {len(visits)} visits matching {len(filter_visit_ids)} requested IDs")
    elif skip_form_json:
        logger.info(f"Parsed {len(visits)} visits (slim mode, no form_json)")
    else:
        logger.info(f"Parsed {len(visits)} visits (full mode)")

    return visits


def parse_csv_file_chunks(
    csv_path: str,
    opportunity_id: int,
    chunksize: int = 1000,
) -> Generator[list[dict], None, None]:
    """
    Parse CSV from file path in chunks. Memory-efficient: no BytesIO copy.

    Reads directly from a file path using pandas C parser, avoiding the
    BytesIO copy that doubles memory usage with parse_csv_bytes().

    Args:
        csv_path: Path to CSV file on disk
        opportunity_id: Opportunity ID (fallback if not in CSV)
        chunksize: Number of rows per chunk (default 1000)

    Yields:
        Lists of visit dicts (with form_json), one list per chunk
    """
    total_parsed = 0
    for chunk in pd.read_csv(csv_path, chunksize=chunksize, on_bad_lines="warn"):
        batch = []
        for _, row in chunk.iterrows():
            batch.append(_row_to_visit_dict(row, opportunity_id, include_form_json=True))
        total_parsed += len(batch)
        yield batch

    logger.info(f"Parsed {total_parsed} visits from file (chunked)")
