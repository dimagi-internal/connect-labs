"""Importing a Connect Worker's past Open Chat Studio conversations into labs' record.

Coaching used to run in OCS: Connect opened a session for an OCS-mode task, and the
conversation lives there. ``workers.api.ocs_sessions_for`` finds a worker's sessions (team key,
``OCS_API_KEY``); this reads each session's transcript (``GET /api/sessions/<id>/``) and keeps it
as a closed ``Thread`` with its ``Message``s, so the worker's history, the brief and the worker
tools see it like any other conversation:

* the thread: ``handler_kind=none``, ``handler_ref=ocs:<chatbot>``, ``started_by=ocs:<session>``,
  closed, ``context`` = the OCS session, chatbot and the Connect task it was opened for;
* each message: the worker's as ``worker``, the bot's as ``agent`` with ``author_ref=ocs:<bot>``,
  ``transport=ocs`` and ``transport_message_id=ocs:<session>:<n>`` -- the idempotency key, so
  importing again adds only what is new;
* the worker's ``ocs_session`` timeline event (``workers.ocs_link``, linked to the task) gains
  ``ref.thread_id``; a session with no task gets an ``ocs_session`` event of its own.

Nothing is sent to anyone. Each imported thread gets the fallback summary (``summaries.py``), so
the next brief's history includes it.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from connect_labs.messaging.canopy_bridge import summaries
from connect_labs.messaging.models import (
    AuthorKind,
    DeliveryStatus,
    Direction,
    HandlerKind,
    Message,
    Thread,
    ThreadState,
)
from connect_labs.workers import api as workers
from connect_labs.workers.connect_facade.shapes import parse_ts
from connect_labs.workers.models import ConnectWorker, WorkerEvent, WorkerMembership, WorkerSnapshot

logger = logging.getLogger(__name__)

TRANSPORT = "ocs"
WORKER_ROLES = frozenset({"user", "human"})
BOT_ROLES = frozenset({"assistant", "ai"})


def _session_detail(client, session_id: str) -> dict:
    response = client.get(f"/api/sessions/{session_id}/")
    response.raise_for_status()
    return response.json()


def _scope(worker: ConnectWorker, task_id: str) -> tuple[int | None, int | None]:
    """(program, opportunity) of the Connect task a session was opened for, if labs knows it."""
    if not task_id:
        return None, None
    snap = WorkerSnapshot.objects.filter(worker=worker, kind="assigned_tasks", key=str(task_id)).first()
    if snap is None:
        return None, None
    program = (
        WorkerMembership.objects.filter(worker=worker, opportunity_id=snap.opportunity_id)
        .values_list("program_id", flat=True)
        .first()
    )
    return program, snap.opportunity_id


def import_session(worker: ConnectWorker, ref: dict, detail: dict) -> tuple[Thread, int]:
    """Keep one OCS session as a thread. Returns (thread, messages added)."""
    session_id = str(ref["session_id"])
    chatbot = str(ref.get("chatbot_id") or (detail.get("experiment") or {}).get("id") or "")
    bot_name = ref.get("chatbot_name") or (detail.get("experiment") or {}).get("name") or chatbot
    program_id, opportunity_id = _scope(worker, ref.get("connect_task_id") or "")
    opened = parse_ts(ref.get("created_at") or detail.get("created_at")) or timezone.now()
    closed = parse_ts(ref.get("ended_at") or detail.get("ended_at") or ref.get("updated_at")) or opened
    rows = [m for m in (detail.get("messages") or []) if isinstance(m, dict)]
    added = 0
    with transaction.atomic():
        thread = Thread.objects.filter(worker=worker, started_by=f"ocs:{session_id}"[:200]).first()
        if thread is None:
            thread = Thread.objects.create(
                worker=worker,
                address=None,
                program_id=program_id,
                opportunity_id=opportunity_id,
                direction=Direction.OUTBOUND,
                handler_kind=HandlerKind.NONE,
                handler_ref=f"ocs:{chatbot}"[:200],
                state=ThreadState.CLOSED,
                opened_at=opened,
                closed_at=closed,
                close_reason="imported from Open Chat Studio",
                started_by=f"ocs:{session_id}"[:200],
                context={
                    "source": "ocs",
                    "ocs_session_id": session_id,
                    "chatbot_id": chatbot,
                    "chatbot_name": bot_name,
                    "connect_task_id": ref.get("connect_task_id") or "",
                    "url": ref.get("url") or "",
                },
            )
        have = set(
            Message.objects.filter(thread=thread, transport=TRANSPORT).values_list("transport_message_id", flat=True)
        )
        last_at = None
        for index, row in enumerate(rows):
            key = f"ocs:{session_id}:{index}"[:64]
            role = str(row.get("role") or "").lower()
            text = str(row.get("content") or row.get("message") or "").strip()
            if key in have or role not in WORKER_ROLES | BOT_ROLES or not text:
                continue
            at = parse_ts(row.get("created_at")) or opened
            inbound = role in WORKER_ROLES
            Message.objects.create(
                thread=thread,
                worker=worker,
                direction=Direction.INBOUND if inbound else Direction.OUTBOUND,
                author_kind=AuthorKind.WORKER if inbound else AuthorKind.AGENT,
                author_ref=worker.username if inbound else f"ocs:{bot_name}"[:200],
                text=text,
                transport=TRANSPORT,
                transport_message_id=key,
                delivery_status=DeliveryStatus.RECEIVED if inbound else DeliveryStatus.DELIVERED,
                created_at=at,
                handled_at=at,
            )
            added += 1
            last_at = max(last_at, at) if last_at else at
        if last_at and (thread.last_message_at is None or last_at > thread.last_message_at):
            Thread.objects.filter(pk=thread.pk).update(last_message_at=last_at)
        _link_event(worker, thread, ref, program_id, opportunity_id, opened)
    return thread, added


def _link_event(worker, thread, ref, program_id, opportunity_id, opened) -> None:
    session_id = str(ref["session_id"])
    events = WorkerEvent.objects.filter(worker=worker, kind="ocs_session", ref__ocs_session_id=session_id)
    if events.exists():
        for event in events:
            if (event.ref or {}).get("thread_id") != thread.pk:
                event.ref = {**(event.ref or {}), "thread_id": thread.pk}
                event.save(update_fields=["ref"])
        return
    workers.record_event(
        worker,
        "ocs_session",
        source="ocs",
        dedupe_key=f"ocs:{opportunity_id if opportunity_id is not None else '-'}:ocs_session:{session_id}"[:200],
        program_id=program_id,
        opportunity_id=opportunity_id,
        occurred_at=opened,
        ref={"ocs_session_id": session_id, "chatbot_id": ref.get("chatbot_id"), "thread_id": thread.pk},
        payload={"chatbot_name": ref.get("chatbot_name") or "", "status": ref.get("status") or ""},
    )


def import_worker(worker: ConnectWorker, *, client=None) -> dict:
    """Import every OCS session of this worker. Idempotent. Raises ``OCSNotConfigured`` without a team key."""
    from connect_labs.workers.ocs_link import _client

    if worker.erased_at is not None:
        return {"sessions": 0, "threads": 0, "messages": 0, "errors": 0}
    own = client is None
    client = client or _client()
    counts = {"sessions": 0, "threads": 0, "messages": 0, "errors": 0}
    try:
        for ref in workers.ocs_sessions_for(worker, client=client):
            if not ref.get("session_id"):
                continue
            counts["sessions"] += 1
            try:
                existed = Thread.objects.filter(worker=worker, started_by=f"ocs:{ref['session_id']}").exists()
                thread, added = import_session(worker, ref, _session_detail(client, str(ref["session_id"])))
                summaries.summarize(thread)
                counts["threads"] += 0 if existed else 1
                counts["messages"] += added
            except Exception:  # noqa: BLE001 -- one session's failure never stops the rest
                logger.exception("canopy bridge: importing OCS session %s failed", ref.get("session_id"))
                counts["errors"] += 1
    finally:
        if own:
            client.close()
    return counts
