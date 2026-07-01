"""Generator that dumps a real Connect opp's exports into a new GDrive folder.

Called from DumpStreamView. No error recovery — the first exception propagates
and is rendered as a final SSE error event by the outer view.

Note: this intentionally bypasses `get_export_client` (the factory used for
read-side fixture serving) and hits prod Connect directly via httpx. Users
who click "Dump fresh data from prod" want real production data even if the
opp is already registered as synthetic — the factory would otherwise loop
back to the existing fixture and shuffle it around.
"""

from __future__ import annotations

import json
import logging

import httpx
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from connect_labs.labs.analysis.sse_streaming import send_sse_event
from connect_labs.labs.synthetic.gdrive import DriveClient

logger = logging.getLogger(__name__)

# (url_path_tail, output_filename). "" means the bare opp-detail endpoint,
# which returns a flat dict rather than a paginated `{results: [...]}` shape.
ENDPOINTS = [
    ("", "opportunity.json"),
    ("user_visits", "user_visits.json"),
    ("user_data", "user_data.json"),
    ("completed_works", "completed_works.json"),
    ("completed_module", "completed_module.json"),
]

_V2_HEADERS = {"Accept": "application/json; version=2.0"}


def _fetch_endpoint(base_url: str, opp_id: int, key: str, access_token: str):
    """Fetch one export endpoint and return the payload.

    For list endpoints (`{results, next}` shape) walks pagination until
    exhausted and returns a flat list. For the bare opp-detail endpoint
    (no `results` key) returns the dict as-is.
    """
    tail = f"{key}/" if key else ""
    url = f"{base_url.rstrip('/')}/export/opportunity/{opp_id}/{tail}"
    headers = {"Authorization": f"Bearer {access_token}", **_V2_HEADERS}

    rows: list[dict] = []
    while url:
        resp = httpx.get(url, headers=headers, timeout=180, follow_redirects=True)
        resp.raise_for_status()
        payload = resp.json()
        if "results" not in payload:
            return payload  # bare-dict detail endpoint
        rows.extend(payload["results"])
        url = payload.get("next")
    return rows


def dump_generator(opp_id: int, access_token: str):
    """Stream SSE events while dumping real prod export data for `opp_id` to a new GDrive folder."""
    parent_id = settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID
    if not parent_id:
        raise ImproperlyConfigured("LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID is not set.")

    drive = DriveClient()
    folder_name = f"opp-{opp_id}-{timezone.now():%Y%m%d-%H%M%S}"
    folder_id = drive.create_folder(folder_name, parent_id=parent_id)
    yield send_sse_event(
        f"Created folder {folder_name}",
        data={"event": "folder", "folder_id": folder_id, "name": folder_name},
    )

    base_url = settings.CONNECT_PRODUCTION_URL
    for key, filename in ENDPOINTS:
        label = key or "opportunity"
        yield send_sse_event(f"Fetching {label}...", data={"event": "fetching", "endpoint": label})
        rows = _fetch_endpoint(base_url, opp_id, key, access_token)
        count = len(rows) if isinstance(rows, list) else 1
        yield send_sse_event(
            f"Uploading {filename} ({count} rows)",
            data={"event": "uploading", "file": filename, "count": count},
        )
        drive.upload_file(folder_id, filename, json.dumps(rows).encode())
        yield send_sse_event(f"\u2713 {filename}", data={"event": "uploaded", "file": filename})

    yield send_sse_event("Dump complete", data={"event": "done", "folder_id": folder_id})
