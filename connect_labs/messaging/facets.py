"""Messaging's own facets of a Connect Worker, computed live from labs' record of its threads.

They sit beside the sync's facets (``WorkerState.facets``) under ``messaging``, so a playbook's
``eligible.facets`` can match them like any other, e.g. connect-coach's inbound ``coach-followup``:

    "eligible": {"facets": {"messaging.recently_coached": {"eq": true}}}

* ``recently_coached`` -- a coaching thread in this program closed within the program's
  ``recently_coached_days`` (labs Settings › Messaging, default 14). A coaching thread is one
  answered by ``connect-coach`` or opened under a playbook whose key starts with ``coach``.
* ``last_coached_at`` -- when the latest such thread closed (ISO 8601), or None.
* ``open_threads`` -- the worker's open threads in this program.

Computed when asked rather than stored, so "within N days" is always true to the minute.
"""

from __future__ import annotations

import datetime as dt

from django.db.models import Max, Q
from django.utils import timezone

COACH_AGENTS = ("connect-coach",)
COACH_PLAYBOOK_PREFIX = "coach"
DEFAULT_RECENTLY_COACHED_DAYS = 14


def coaching_threads_q() -> Q:
    q = Q(playbook_key__startswith=COACH_PLAYBOOK_PREFIX)
    for agent in COACH_AGENTS:
        q |= Q(handler_ref__startswith=f"{agent}:")
    return q


def messaging_facets(worker, program_id, *, now: dt.datetime | None = None) -> dict:
    from connect_labs.messaging import config
    from connect_labs.messaging.models import Thread, ThreadState

    now = now or timezone.now()
    days = int(config.program_settings(program_id).get("recently_coached_days") or DEFAULT_RECENTLY_COACHED_DAYS)
    threads = Thread.objects.filter(worker=worker, program_id=program_id)
    last = (
        threads.filter(coaching_threads_q(), state=ThreadState.CLOSED, closed_at__isnull=False)
        .aggregate(m=Max("closed_at"))
        .get("m")
    )
    return {
        "recently_coached": bool(last and last >= now - dt.timedelta(days=days)),
        "last_coached_at": last.isoformat() if last else None,
        "open_threads": threads.filter(state=ThreadState.OPEN).count(),
    }


def with_messaging(facets: dict | None, worker, program_id, *, now=None) -> dict:
    """``facets`` with the live ``messaging`` facets merged in (stored keys kept, live ones win)."""
    out = dict(facets or {})
    out["messaging"] = {**(out.get("messaging") or {}), **messaging_facets(worker, program_id, now=now)}
    return out
