"""Generator that dumps a real Connect opp's exports into a new GDrive folder.

Called from DumpStreamView. No error recovery — the first exception propagates
and is rendered as a final SSE error event by the outer view.
"""

from __future__ import annotations

import json
import logging

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from commcare_connect.labs.analysis.sse_streaming import send_sse_event
from commcare_connect.labs.integrations.connect.factory import get_export_client
from commcare_connect.labs.synthetic.gdrive import DriveClient

logger = logging.getLogger(__name__)


ENDPOINTS = [
    ("", "opportunity.json"),
    ("user_visits", "user_visits.json"),
    ("user_data", "user_data.json"),
    ("completed_works", "completed_works.json"),
    ("completed_module", "completed_module.json"),
]


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

    with get_export_client(opportunity_id=opp_id, access_token=access_token) as client:
        for key, filename in ENDPOINTS:
            label = key or "opportunity"
            yield send_sse_event(f"Fetching {label}...", data={"event": "fetching", "endpoint": label})
            path = f"/export/opportunity/{opp_id}/{key}/" if key else f"/export/opportunity/{opp_id}/"
            rows = client.fetch_all(path)
            count = len(rows) if isinstance(rows, list) else 1
            yield send_sse_event(
                f"Uploading {filename} ({count} rows)",
                data={"event": "uploading", "file": filename, "count": count},
            )
            drive.upload_file(folder_id, filename, json.dumps(rows).encode())
            yield send_sse_event(f"\u2713 {filename}", data={"event": "uploaded", "file": filename})

    yield send_sse_event("Dump complete", data={"event": "done", "folder_id": folder_id})
