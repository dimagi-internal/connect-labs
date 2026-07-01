"""
Connect Export API fetcher for the analysis pipeline.

Fetches records from the Connect production export endpoints
(/export/opportunity/<id>/<endpoint>/) and normalizes them to visit-dict
shape so the existing SQL extraction path works unchanged.

Each record is placed under form_json[<endpoint_singular>] so pipeline
field paths follow the pattern "audit_report.period_start", etc.
"""

import logging

import httpx
from django.conf import settings
from django.http import HttpRequest

from connect_labs.labs.analysis.config import DataSourceConfig

logger = logging.getLogger(__name__)

# Maps endpoint name → key used in form_json wrapper
_ENDPOINT_SINGULAR = {
    "audit_reports": "audit_report",
    "audit_report_entries": "audit_entry",
    "assigned_tasks": "assigned_task",
    "work_areas": "work_area",
    "work_area_groups": "work_area_group",
}

# Maps endpoint → field name to use as `username` in the visit dict
_ENDPOINT_USERNAME_FIELD = {
    "audit_reports": "completed_by_username",
    "audit_report_entries": "username",
    "assigned_tasks": "username",
    "work_areas": None,
    "work_area_groups": None,
}

# Maps endpoint → field name to use as `visit_date` in the visit dict
_ENDPOINT_DATE_FIELD = {
    "audit_reports": "period_start",
    "audit_report_entries": "date_created",
    "assigned_tasks": "date_created",
    "work_areas": "date_created",
    "work_area_groups": "date_created",
}

DEFAULT_PAGE_SIZE = 500


def _get_access_token(request: HttpRequest | None, access_token: str | None) -> str:
    """Return a Connect OAuth Bearer token from the provided token or request session."""
    if access_token:
        return access_token
    if request is not None:
        token_data = request.session.get("labs_oauth", {})
        token = token_data.get("access_token", "")
        if token:
            return token
    raise ValueError(
        "connect_export data source requires a Connect OAuth token. "
        "Ensure you are logged in via Connect OAuth before running this pipeline."
    )


def normalize_connect_export_record(record: dict, endpoint: str, opportunity_id: int, index: int) -> dict:
    """
    Normalize a Connect export API record to visit-dict shape.

    The record is nested under form_json[<endpoint_singular>] so FieldComputation
    paths like "audit_report.period_start" work via the existing dot-notation
    extractor — the same mechanism used by cchq_forms ("form.*" paths).
    """
    singular = _ENDPOINT_SINGULAR.get(endpoint, endpoint.rstrip("s"))
    username_field = _ENDPOINT_USERNAME_FIELD.get(endpoint)
    date_field = _ENDPOINT_DATE_FIELD.get(endpoint, "date_created")

    username = ""
    if username_field and record.get(username_field):
        username = record[username_field] or ""

    visit_date = None
    raw_date = record.get(date_field, "")
    if raw_date:
        visit_date = str(raw_date)[:10]

    return {
        "id": record.get("id", index),
        "opportunity_id": opportunity_id,
        "username": username,
        "visit_date": visit_date,
        "status": "approved",
        "entity_id": str(record.get("id", index)),
        "entity_name": "",
        "deliver_unit": "",
        "deliver_unit_id": None,
        "location": "",
        "flagged": False,
        "flag_reason": "",
        "reason": "",
        "form_json": {singular: record},
        "completed_work": "",
        "status_modified_date": None,
        "review_status": "",
        "review_created_on": None,
        "justification": "",
        "date_created": raw_date,
        "completed_work_id": None,
        "images": [],
    }


def fetch_connect_export_as_visit_dicts(
    request: HttpRequest | None,
    data_source: DataSourceConfig,
    access_token: str | None,
    opportunity_id: int,
) -> list[dict]:
    """
    Fetch all records from a Connect export endpoint for the given opportunity
    and return them as normalized visit dicts.

    Args:
        request: HttpRequest with labs_oauth in session, or None for headless callers.
            Token can be passed via access_token instead.
        data_source: DataSourceConfig with type="connect_export" and endpoint set.
        access_token: Explicit OAuth Bearer token; takes precedence over request.session.
        opportunity_id: Connect opportunity ID.

    Returns:
        List of visit-shaped dicts ready for SQL backend processing.
    """
    if not data_source.endpoint:
        raise ValueError("connect_export data source requires endpoint to be set in the pipeline schema.")

    token = _get_access_token(request, access_token)
    base_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
    endpoint = data_source.endpoint

    url = f"{base_url}/export/opportunity/{opportunity_id}/{endpoint}/"
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json; version=2.0",
    }

    logger.info(f"[ConnectExportFetcher] Fetching {endpoint} for opp {opportunity_id}...")

    all_records: list[dict] = []
    page = 0
    params: dict = {"page_size": DEFAULT_PAGE_SIZE}
    next_url: str | None = url

    with httpx.Client(headers=headers, timeout=60.0) as client:
        while next_url:
            page += 1
            try:
                resp = client.get(next_url, params=params if page == 1 else None)
                resp.raise_for_status()
                data = resp.json()
            except httpx.HTTPStatusError as e:
                raise RuntimeError(
                    f"Connect export API returned {e.response.status_code} for {endpoint} "
                    f"opp {opportunity_id}: {e.response.text[:200]}"
                ) from e
            except Exception as e:
                raise RuntimeError(f"Failed to fetch {endpoint} for opp {opportunity_id} (page {page}): {e}") from e

            if isinstance(data, list):
                records = data
                next_url = None
            else:
                records = data.get("results", [])
                next_url = data.get("next")

            all_records.extend(records)
            logger.debug(
                f"[ConnectExportFetcher] {endpoint} page {page}: {len(records)} records "
                f"(total: {len(all_records)})"
            )

    logger.info(f"[ConnectExportFetcher] Fetched {len(all_records)} {endpoint} records for opp {opportunity_id}")

    return [normalize_connect_export_record(r, endpoint, opportunity_id, i) for i, r in enumerate(all_records)]
