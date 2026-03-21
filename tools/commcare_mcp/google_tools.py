"""Google Sheets/Drive tools for the MCP server.

Uses cached OAuth credentials from google_auth.py to read Google Sheets
and Drive files via the Google APIs.
"""

from __future__ import annotations

import logging
import re

from google_auth import get_credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)


def _get_sheets_service():
    creds = get_credentials()
    if not creds:
        raise PermissionError("Not logged in to Google. Run: python tools/commcare_mcp/google_auth.py login")
    return build("sheets", "v4", credentials=creds)


def _get_drive_service():
    creds = get_credentials()
    if not creds:
        raise PermissionError("Not logged in to Google. Run: python tools/commcare_mcp/google_auth.py login")
    return build("drive", "v3", credentials=creds)


def _parse_sheet_url(url: str) -> tuple[str, int | None]:
    """Extract spreadsheet ID and gid from a Google Sheets URL.

    Returns (spreadsheet_id, gid_or_none).
    """
    # Match /d/{id}/ pattern
    match = re.search(r"/spreadsheets/d/([a-zA-Z0-9_-]+)", url)
    if not match:
        # Assume it's a raw spreadsheet ID
        return url, None
    spreadsheet_id = match.group(1)

    # Extract gid if present
    gid_match = re.search(r"gid=(\d+)", url)
    gid = int(gid_match.group(1)) if gid_match else None
    return spreadsheet_id, gid


async def read_google_sheet(
    url: str,
    tab_name: str = "",
    cell_range: str = "",
) -> dict:
    """Read data from a Google Sheet.

    Args:
        url: Google Sheets URL or spreadsheet ID
        tab_name: Sheet tab name (overrides gid from URL). If empty, uses
                  the gid from the URL or the first sheet.
        cell_range: Optional A1 notation range (e.g. "A1:D10"). If empty,
                    reads all data.

    Returns:
        Dict with sheet_name, headers (first row), rows (list of dicts),
        and raw_values (list of lists).
    """
    spreadsheet_id, gid = _parse_sheet_url(url)
    service = _get_sheets_service()

    # Resolve tab name from gid if needed
    if not tab_name and gid is not None:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        for sheet in meta.get("sheets", []):
            props = sheet.get("properties", {})
            if props.get("sheetId") == gid:
                tab_name = props.get("title", "")
                break

    # If still no tab name, get the first sheet
    if not tab_name:
        meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
        sheets = meta.get("sheets", [])
        if sheets:
            tab_name = sheets[0].get("properties", {}).get("title", "Sheet1")

    # Build the range
    if cell_range:
        range_str = f"'{tab_name}'!{cell_range}"
    else:
        range_str = f"'{tab_name}'"

    result = service.spreadsheets().values().get(spreadsheetId=spreadsheet_id, range=range_str).execute()

    values = result.get("values", [])
    if not values:
        return {
            "sheet_name": tab_name,
            "headers": [],
            "rows": [],
            "raw_values": [],
            "row_count": 0,
        }

    headers = values[0]
    rows = []
    for row in values[1:]:
        # Pad row to match headers length
        padded = row + [""] * (len(headers) - len(row))
        rows.append(dict(zip(headers, padded)))

    return {
        "sheet_name": tab_name,
        "headers": headers,
        "rows": rows,
        "raw_values": values,
        "row_count": len(rows),
    }


async def list_sheet_tabs(url: str) -> dict:
    """List all tabs in a Google Sheet.

    Args:
        url: Google Sheets URL or spreadsheet ID

    Returns:
        Dict with spreadsheet title and list of tabs with their names and gids.
    """
    spreadsheet_id, _ = _parse_sheet_url(url)
    service = _get_sheets_service()

    meta = service.spreadsheets().get(spreadsheetId=spreadsheet_id).execute()
    tabs = []
    for sheet in meta.get("sheets", []):
        props = sheet.get("properties", {})
        tabs.append(
            {
                "name": props.get("title", ""),
                "gid": props.get("sheetId"),
                "index": props.get("index"),
                "row_count": props.get("gridProperties", {}).get("rowCount"),
                "column_count": props.get("gridProperties", {}).get("columnCount"),
            }
        )

    return {
        "title": meta.get("properties", {}).get("title", ""),
        "spreadsheet_id": spreadsheet_id,
        "tabs": tabs,
    }
