"""Linking a Connect Worker's OCS-mode tasks to the Open Chat Studio sessions they opened.

Connect stores ``ocs_session_id`` on an ``AssignedTask`` but does not export it
(docs/connect-asks.md #3). When Connect opens a coaching session for a task it calls
OCS's trigger_bot with ``identifier=<username>`` and
``participant_data={"connectTaskId": "<assigned_task_id>"}``. So labs finds the
session from the OCS side, with the team key (``OCS_API_KEY``):

1. ``GET /api/participants?identifier=<username>`` -- the chatbots this participant has data with;
2. ``GET /api/sessions/?experiment=<chatbot>`` for each, newest first, kept where the
   participant is this worker; each session's ``participant_data.connectTaskId``
   names the task it was opened for.

`ocs_sessions_for(worker)` returns those session refs (a transcript import into
``messaging`` comes later); `link_ocs_tasks(worker)` writes one ``ocs_session``
WorkerEvent per session that matches one of the worker's tasks.
"""

from __future__ import annotations

import logging

import httpx
from django.conf import settings

from . import events as ev
from .connect_facade.shapes import parse_ts
from .models import Confidence, ConnectWorker, WorkerMembership, WorkerSnapshot

logger = logging.getLogger(__name__)

MAX_SESSION_PAGES = 5
PAGE_SIZE = 100


class OCSNotConfigured(RuntimeError):
    """No OCS team key, so sessions cannot be read headless."""


def _client() -> httpx.Client:
    key = getattr(settings, "OCS_API_KEY", "") or ""
    if not key:
        raise OCSNotConfigured("OCS_API_KEY is not set; OCS sessions cannot be linked.")
    return httpx.Client(
        base_url=getattr(settings, "OCS_URL", "https://www.openchatstudio.com").rstrip("/"),
        headers={"X-API-KEY": key},
        timeout=30.0,
        follow_redirects=True,
    )


def _chatbots_for(client: httpx.Client, username: str) -> list[str]:
    response = client.get("/api/participants", params={"identifier": username})
    response.raise_for_status()
    ids = []
    for participant in response.json().get("results", []):
        if participant.get("identifier") != username:
            continue
        for entry in participant.get("data") or []:
            if entry.get("chatbot_id"):
                ids.append(entry["chatbot_id"])
    return list(dict.fromkeys(ids))


def _sessions(client: httpx.Client, chatbot_id: str, username: str) -> list[dict]:
    url, params = "/api/sessions/", {"experiment": chatbot_id, "ordering": "-created_at", "page_size": PAGE_SIZE}
    out = []
    for _ in range(MAX_SESSION_PAGES):
        response = client.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        for s in data.get("results", []):
            if ((s.get("participant") or {}).get("identifier") or "") == username:
                out.append(s)
        url, params = data.get("next"), None
        if not url:
            break
    return out


def _ref(session: dict, chatbot_id: str) -> dict:
    pdata = session.get("participant_data") or {}
    return {
        "session_id": session.get("id"),
        "chatbot_id": (session.get("experiment") or {}).get("id") or chatbot_id,
        "chatbot_name": (session.get("experiment") or {}).get("name") or "",
        "created_at": session.get("created_at"),
        "updated_at": session.get("updated_at"),
        "ended_at": session.get("ended_at"),
        "status": session.get("status") or "",
        "platform": session.get("platform") or "",
        "connect_task_id": pdata.get("connectTaskId") or "",
        "url": session.get("url") or "",
    }


def ocs_sessions_for(worker: ConnectWorker, *, client: httpx.Client | None = None) -> list[dict]:
    """OCS session refs for this worker (newest first), each with the Connect task it was opened for, if any.

    Raises `OCSNotConfigured` when labs has no OCS team key.
    """
    own = client is None
    client = client or _client()
    try:
        refs = []
        for chatbot_id in _chatbots_for(client, worker.username):
            refs.extend(_ref(s, chatbot_id) for s in _sessions(client, chatbot_id, worker.username))
        refs.sort(key=lambda r: r.get("created_at") or "", reverse=True)
        return refs
    finally:
        if own:
            client.close()


def link_ocs_tasks(worker: ConnectWorker, *, client: httpx.Client | None = None) -> int:
    """Write an ``ocs_session`` event for each OCS session opened for one of the worker's tasks."""
    tasks = {
        str((s.data or {}).get("assigned_task_id") or s.key): s
        for s in WorkerSnapshot.objects.filter(worker=worker, kind="assigned_tasks")
        if (s.data or {}).get("mode") == "ocs"
    }
    if not tasks:
        return 0
    programs = dict(WorkerMembership.objects.filter(worker=worker).values_list("opportunity_id", "program_id"))
    events = []
    for ref in ocs_sessions_for(worker, client=client):
        snap = tasks.get(str(ref.get("connect_task_id") or ""))
        if snap is None or not ref.get("session_id"):
            continue
        events.append(
            ev.build(
                worker,
                ev.OCS_SESSION,
                source="ocs",
                dedupe_key=ev.key("ocs", snap.opportunity_id, ev.OCS_SESSION, ref["session_id"]),
                opportunity_id=snap.opportunity_id,
                program_id=programs.get(snap.opportunity_id),
                occurred_at=parse_ts(ref.get("created_at")),
                confidence=Confidence.EXACT,
                ref={
                    "ocs_session_id": ref["session_id"],
                    "chatbot_id": ref.get("chatbot_id"),
                    "assigned_task_id": ref.get("connect_task_id"),
                    "url": ref.get("url"),
                },
                payload={
                    "task_type_name": (snap.data or {}).get("task_type_name"),
                    "status": ref.get("status"),
                    "ended_at": ref.get("ended_at"),
                    "chatbot_name": ref.get("chatbot_name"),
                },
            )
        )
    return ev.bulk_record(events)
