"""Reading the live model Sheets, and remembering whether they have drifted.

``effectiveness_check_sheets`` is the by-hand report; ``tasks.check_model_sheets`` runs the same
comparison daily and leaves the answer here for the models page to show. The comparison itself is
``sheets_check.compare`` (pure); this module is the part that touches Google and the cache.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import quote

from django.core.cache import cache
from django.core.management.base import CommandError

from connect_labs.effectiveness import engine, explore, sheets_check

#: Long enough to outlive a missed day, short enough that a fixed Sheet stops being reported.
STATUS_KEY = "effectiveness:sheet-drift"
STATUS_TTL_SECONDS = 3 * 24 * 3600


def sheet_id(url: str) -> str:
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", url or "")
    if not m:
        raise CommandError(f"No spreadsheet id in {url!r}")
    return m.group(1)


def read_model_tab(spreadsheet_id: str) -> list[list]:
    """The Model tab with numbers as numbers (``UNFORMATTED_VALUE``), as the labs service account."""
    import httpx
    from google.auth.transport.requests import Request

    from connect_labs.labs.synthetic.gdrive import _load_credentials

    creds = _load_credentials()
    if not creds.valid:
        creds.refresh(Request())
    url = (
        f"https://sheets.googleapis.com/v4/spreadsheets/{spreadsheet_id}/values/{quote('Model!A1:N400', safe='')}"
        "?valueRenderOption=UNFORMATTED_VALUE"
    )
    got = httpx.get(url, headers={"Authorization": f"Bearer {creds.token}"}, timeout=60)
    got.raise_for_status()
    return got.json().get("values", [])


def check_all(read=None) -> list[dict]:
    """One ``sheets_check.compare`` report per model, against its live Sheet (``read`` is for tests)."""
    read = read or read_model_tab
    meta = explore.models()
    return [sheets_check.compare(model, read(sheet_id(meta[model]["sheet"]))) for model in engine.MODELS]


def record(reports: list[dict], now: datetime | None = None) -> dict:
    """Remember which models differ, so the models page can say so until the specs are refreshed."""
    status = {
        "checked_at": (now or datetime.now(timezone.utc)).isoformat(timespec="minutes"),
        "drifted": [r["model"] for r in reports if not r["in_sync"]],
    }
    cache.set(STATUS_KEY, status, STATUS_TTL_SECONDS)
    return status


def status() -> dict | None:
    """The last daily check, or None when none has run (or the last one is more than three days old)."""
    return cache.get(STATUS_KEY)


def drift_note() -> str:
    """One line for the models page: empty unless the last check found a Sheet that moved."""
    got = status()
    if not got or not got.get("drifted"):
        return ""
    names = ", ".join(got["drifted"])
    return f"The live Sheet for {names} has changed since this copy was taken ({got['checked_at'][:10]})."
