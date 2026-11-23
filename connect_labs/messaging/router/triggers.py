"""What starts or ends conversations on its own: state events, schedules, and idle threads.

Each runs from the messaging sweep (`tasks.py`), never inline in a request:

* **State events**: the sweep reads new `WorkerEvent`s past a cursor (the sync writes them in
  bulk, with no signals) and starts any ``state_event`` playbook whose kinds (and stages) match.
* **Schedules**: a ``schedule`` playbook due this hour starts once per period (a day, or an ISO
  week) for the program's Connect Workers, recorded as a `PlaybookRun`.
* **Idle**: an open thread whose playbook's ``exit.idle_hours`` has passed with no message closes.

Both starting triggers go through `starting.start_automatic`, so only an ``auto_within_caps``
playbook starts, every worker is judged at the moment it starts, and one worker's failure never
stops the rest.
"""

from __future__ import annotations

import datetime as dt
import logging

from django.db import transaction
from django.utils import timezone

from connect_labs.messaging import config, playbooks, service
from connect_labs.messaging.models import Thread, ThreadState
from connect_labs.workers.models import WorkerMembership

from .starting import start_automatic

logger = logging.getLogger(__name__)

#: Events older than this when first read never start anything (a backlog is not a reason to message).
EVENT_MAX_AGE = dt.timedelta(hours=6)


def on_worker_event(event) -> list:
    """A `WorkerEvent` happened: start any state_event playbook of its program that asks for it."""
    if event.program_id is None or not service.enabled() or not config.program_enabled(event.program_id):
        return []
    results = []
    for key, playbook in playbooks.all_for(event.program_id).items():
        trigger = playbook.get("trigger") or {}
        if (
            playbook.get("direction") != "outbound"
            or not playbooks.is_enabled(playbook)
            or trigger.get("type") != "state_event"
            or event.kind not in (trigger.get("kinds") or [])
        ):
            continue
        payload = event.payload or {}
        stage = payload.get("stage_to") or payload.get("stage") or payload.get("to")
        if trigger.get("stages") and stage not in trigger["stages"]:
            continue
        results.append(
            start_automatic(
                program_id=event.program_id,
                playbook_key=key,
                username=event.worker.username,
                trigger=f"state_event:{event.kind}",
                opportunity_id=event.opportunity_id,
            )
        )
    return results


def scan_worker_events(limit: int = 2000, now: dt.datetime | None = None) -> int:
    """Read the timeline past the cursor and run state-event triggers on what is new. Returns how many ran.

    A cursor, not a signal: the sync writes events with `bulk_create`, which sends none. The first
    scan starts from the newest event, so turning this on never replays history.
    """
    from django.db.models import Max

    from connect_labs.messaging.models import EventCursor
    from connect_labs.workers.models import WorkerEvent

    now = now or timezone.now()
    with transaction.atomic():
        cursor, created = EventCursor.objects.select_for_update().get_or_create(name="state_event")
        if created:
            cursor.last_id = WorkerEvent.objects.aggregate(m=Max("id"))["m"] or 0
            cursor.save(update_fields=["last_id", "updated_at"])
            return 0
        events = list(
            WorkerEvent.objects.filter(id__gt=cursor.last_id, program_id__isnull=False)
            .exclude(kind__in=("message_in", "message_out", "consent_changed"))
            .select_related("worker")
            .order_by("id")[:limit]
        )
        if not events:
            return 0
        cursor.last_id = events[-1].id
        cursor.save(update_fields=["last_id", "updated_at"])
    ran = 0
    if not service.enabled():
        return 0
    for event in events:
        if event.observed_at < now - EVENT_MAX_AGE:
            continue
        try:
            on_worker_event(event)
            ran += 1
        except Exception:  # noqa: BLE001 -- one event's failure never stops the rest
            logger.exception("messaging: trigger for worker event %s failed", event.pk)
    return ran


def due_schedules(now: dt.datetime) -> list[tuple[int, str, dict, str]]:
    """(program_id, key, playbook, period) for every schedule playbook due this hour (UTC)."""
    from connect_labs.scope_config.models import ScopeConfig

    due = []
    rows = ScopeConfig.objects.filter(namespace=playbooks.PLAYBOOKS, scope_type="program")
    for row in rows:
        program_id = int(row.scope_key)
        for key, playbook in ((row.data or {}).get("playbooks") or {}).items():
            trigger = playbook.get("trigger") or {}
            if trigger.get("type") != "schedule" or not playbooks.is_enabled(playbook):
                continue
            if trigger.get("hour") != now.hour:
                continue
            if trigger.get("every") == "weekly":
                if trigger.get("weekday") != now.weekday():
                    continue
                period = f"{now.isocalendar().year}-W{now.isocalendar().week}"
            else:
                period = now.date().isoformat()
            due.append((program_id, key, playbook, period))
    return due


def run_schedules(now: dt.datetime | None = None, limit: int = 500) -> list[dict]:
    """Start every schedule playbook due now, once per period, for its eligible Connect Workers."""
    from connect_labs.messaging.models import PlaybookRun

    now = now or timezone.now()
    out = []
    if not service.enabled():
        return out
    for program_id, key, _playbook, period in due_schedules(now):
        if not config.program_enabled(program_id):
            continue
        run, created = PlaybookRun.objects.get_or_create(program_id=program_id, playbook_key=key, period=period)
        if not created:
            continue
        started = blocked = 0
        usernames = (
            WorkerMembership.objects.filter(program_id=program_id, worker__erased_at__isnull=True)
            .values_list("worker__username", flat=True)
            .distinct()[:limit]
        )
        for username in usernames:
            result = start_automatic(program_id=program_id, playbook_key=key, username=username, trigger="schedule")
            if isinstance(result, Thread):
                started += 1
            else:
                blocked += 1
        run.started, run.blocked, run.finished_at = started, blocked, timezone.now()
        run.save(update_fields=["started", "blocked", "finished_at"])
        out.append(
            {"program_id": program_id, "playbook": key, "period": period, "started": started, "blocked": blocked}
        )
    return out


def close_idle(now: dt.datetime | None = None) -> int:
    """Close open threads whose playbook's `exit.idle_hours` has passed with no message."""
    now = now or timezone.now()
    closed = 0
    for thread in Thread.objects.filter(state=ThreadState.OPEN).exclude(playbook_key="").iterator():
        hours = ((playbooks.for_thread(thread) or {}).get("exit") or {}).get("idle_hours")
        last = thread.last_message_at or thread.opened_at
        if hours and last < now - dt.timedelta(hours=hours):
            service.close_thread(thread, f"no messages for {hours} hours", by="exit rule")
            closed += 1
    return closed
