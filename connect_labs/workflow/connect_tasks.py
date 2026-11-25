"""Connect's own tasks (relearn, OCS), read and assigned through Connect's export API.

Labs has its own task records (``connect_labs/tasks``); these are different: a
Connect assigned task lives in Connect, shows in the worker's Connect app and on
Connect's task list, and (for a relearn type) sets a case property in CommCare HQ.

Checked against commcare-connect (``data_export/urls.py``, ``data_export/views.py``,
``data_export/serializer.py``, CI-1060 / #1782):

* ``GET /export/opportunity/<id>/task_types/`` (``Accept: application/json;version=2.0``,
  scope ``export``): ``{"next", "results": [{id, task_type_id, name, slug, mode,
  case_property, is_active, archived, ...}]}``.
* ``POST /export/opportunity/<id>/assigned_tasks/create/`` (JSON ``{task_type_id,
  username, due_date}``, scope ``write``, the caller must be a program manager of the
  opportunity -- anyone else gets 404): 201 with the assigned task; 400 for a bad
  field (an inactive type, a worker not on the opportunity, a past due date); 409 when
  that type is already assigned to that worker; 502 when Connect could not update
  CommCare HQ or start the chatbot. A token without ``write`` gets 403.
"""

from __future__ import annotations

import logging

import httpx
from django.conf import settings

logger = logging.getLogger(__name__)

ACCEPT_V2 = "application/json;version=2.0"
TIMEOUT = 30.0


class ConnectTaskError(Exception):
    """Connect refused or failed a task call. ``public_message`` is safe to show."""

    def __init__(self, public_message: str, status_code: int | None = None):
        self.public_message = public_message
        self.status_code = status_code
        super().__init__(public_message)


def _base() -> str:
    return settings.CONNECT_PRODUCTION_URL.rstrip("/")


def _headers(access_token: str, accept: str = ACCEPT_V2) -> dict[str, str]:
    return {"Authorization": f"Bearer {access_token}", "Accept": accept}


def list_task_types(access_token: str, opportunity_id: int) -> list[dict]:
    """The opportunity's task types, every page."""
    url = f"{_base()}/export/opportunity/{int(opportunity_id)}/task_types/"
    out: list[dict] = []
    pages = 0
    while url and pages < 20:
        pages += 1
        try:
            response = httpx.get(url, headers=_headers(access_token), timeout=TIMEOUT)
        except httpx.HTTPError as e:
            raise ConnectTaskError("Connect could not be reached to read the task types.") from e
        if response.status_code != 200:
            raise ConnectTaskError(
                f"Connect would not list this opportunity's task types (HTTP {response.status_code}).",
                response.status_code,
            )
        body = response.json()
        if isinstance(body, list):
            out.extend(body)
            break
        out.extend(body.get("results") or [])
        url = body.get("next")
    return out


def find_task_type(
    access_token: str, opportunity_id: int, *, task_type_id: str | None = None, name: str | None = None
) -> dict | None:
    """One active, unarchived task type of the opportunity, by its ``task_type_id`` or else
    its ``name`` (case and surrounding spaces ignored). A name two active types share is
    ambiguous and finds nothing."""
    live = [
        t for t in list_task_types(access_token, opportunity_id) if t.get("is_active", True) and not t.get("archived")
    ]
    if task_type_id:
        return next((t for t in live if str(t.get("task_type_id")) == str(task_type_id)), None)
    wanted = (name or "").strip().casefold()
    hits = [t for t in live if str(t.get("name") or "").strip().casefold() == wanted] if wanted else []
    return hits[0] if len(hits) == 1 else None


def _refusal(response: httpx.Response) -> str:
    """Connect's own reason, flattened to one line (DRF field errors or ``{"error"}``)."""
    try:
        body = response.json()
    except ValueError:
        return ""
    if isinstance(body, dict):
        if isinstance(body.get("error"), str):
            return body["error"]
        if isinstance(body.get("detail"), str):
            return body["detail"]
        parts = []
        for field, msgs in body.items():
            text = " ".join(str(m) for m in msgs) if isinstance(msgs, list) else str(msgs)
            parts.append(f"{field}: {text}")
        return "; ".join(parts)
    return str(body)[:300]


def assign_task(access_token: str, opportunity_id: int, *, username: str, task_type_id: str, due_date: str) -> dict:
    """Assign one task in Connect, as the person whose token this is. Returns Connect's task."""
    url = f"{_base()}/export/opportunity/{int(opportunity_id)}/assigned_tasks/create/"
    try:
        response = httpx.post(
            url,
            headers=_headers(access_token, "application/json"),
            json={"task_type_id": str(task_type_id), "username": username, "due_date": due_date},
            timeout=TIMEOUT,
        )
    except httpx.HTTPError as e:
        raise ConnectTaskError("Connect could not be reached; nothing was assigned.") from e
    if response.status_code == 201:
        return response.json()
    status = response.status_code
    reason = _refusal(response)
    if status == 403:
        message = (
            "Your Labs sign-in does not allow creating Connect tasks yet. "
            "Sign out of Labs and sign in again, then retry."
        )
    elif status == 404:
        message = "Only a program manager of this opportunity can assign Connect tasks."
    elif status == 409:
        message = "This task is already assigned to this worker in Connect."
    else:
        message = f"Connect refused the task (HTTP {status})" + (f": {reason}" if reason else ".")
    logger.warning("Connect task create %s for opp %s: %s %s", status, opportunity_id, username, reason[:200])
    raise ConnectTaskError(message[:300], status)
