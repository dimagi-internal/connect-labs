"""The canopy bridge's background work. Every task returns at once while the bridge is off."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from config import celery_app
from connect_labs.audit_trail.context import audit_context

logger = logging.getLogger(__name__)

#: A turn whose poll is this late with no task in flight is polled again by the sweep.
STALE_POLL = timedelta(seconds=90)


@celery_app.task(acks_late=True)
def push_worker_profile(worker_id: int):
    from connect_labs.messaging.canopy_bridge import conf, profile_push
    from connect_labs.workers.models import ConnectWorker

    if not conf.enabled():
        return None
    with audit_context(source="celery"):
        worker = ConnectWorker.objects.filter(pk=worker_id, erased_at__isnull=True).first()
        return profile_push.push([worker]) if worker else None


@celery_app.task(acks_late=True)
def poll_turn(pending_id: int):
    """Ask canopy whether the agent has answered; again later (backing off) until it has."""
    from connect_labs.messaging.canopy_bridge import conf, handler
    from connect_labs.messaging.canopy_bridge.models import PendingTurn, TurnStatus

    if not conf.enabled():
        return None
    pending = PendingTurn.objects.select_related("session", "thread").filter(pk=pending_id).first()
    if pending is None or pending.status != TurnStatus.PENDING:
        return "done"
    with audit_context(source="celery"):
        try:
            outcome = handler.poll(pending)
        except Exception as exc:  # noqa: BLE001 -- canopy unreachable: try again later, until the timeout
            logger.warning("canopy bridge: polling turn %s failed: %s", pending.turn_id, exc)
            outcome = "error"
            if timezone.now() - pending.sent_at > timedelta(seconds=conf.turn_timeout_seconds()):
                handler.fail(
                    pending,
                    status=TurnStatus.TIMED_OUT,
                    reason="Canopy could not be reached for the agent's answer; a person should reply.",
                )
                return TurnStatus.TIMED_OUT
    if outcome in ("running", "webhook", "error"):
        PendingTurn.objects.filter(pk=pending.pk).update(polls=pending.polls + 1)
        pending.polls += 1
        handler._schedule_poll(pending, handler.next_poll_in(pending.polls))
    return outcome


@celery_app.task(acks_late=True)
def kickoff(thread_id: int, attempt: int = 0):
    """After a handoff to another agent, or a send over canopy's per-person limit (``attempt``
    retries so far): answer a message the worker is still waiting on."""
    from connect_labs.messaging.canopy_bridge import handler
    from connect_labs.messaging.models import Thread

    thread = Thread.objects.select_related("worker").filter(pk=thread_id).first()
    if thread is None:
        return None
    with audit_context(source="celery"):
        return handler.kickoff(thread, attempt=attempt)


def poll_stale(now=None) -> int:
    """Poll every turn whose next poll is overdue (a lost task)."""
    from connect_labs.messaging.canopy_bridge.models import PendingTurn, TurnStatus

    now = now or timezone.now()
    due = PendingTurn.objects.filter(status=TurnStatus.PENDING, next_poll_at__lt=now - STALE_POLL)
    count = 0
    for pending_id in due.values_list("pk", flat=True)[:500]:
        try:
            poll_turn.delay(pending_id)
            count += 1
        except Exception:  # noqa: BLE001
            logger.warning("canopy bridge: could not queue a poll of turn %s", pending_id, exc_info=True)
    return count


@celery_app.task
def sweep():
    """Keep canopy's profile cache current (S2); poll turns a lost task left behind; retry canopy erasures (S6)."""
    from connect_labs.messaging.canopy_bridge import conf, erasure, profile_push

    if not conf.enabled():
        return None
    out = {}
    with audit_context(source="celery"):
        parts = (("profiles", profile_push.push_due), ("polls", poll_stale), ("erasures", erasure.retry_pending))
        for name, part in parts:
            try:
                out[name] = part()
            except Exception:  # noqa: BLE001 -- one part's failure never stops the rest
                logger.exception("canopy bridge: sweep part %s failed", name)
    return out


@celery_app.task(acks_late=True)
def erase_in_canopy(erasure_id: int):
    """Tell canopy to forget an erased Connect Worker (S6); the sweep retries until it answers."""
    from connect_labs.messaging.canopy_bridge import erasure
    from connect_labs.messaging.canopy_bridge.models import Erasure

    row = Erasure.objects.filter(pk=erasure_id).first()
    if row is None or row.canopy_status not in ("pending", "error"):
        return None
    with audit_context(source="celery"):
        return erasure.erase_in_canopy(row)


@celery_app.task
def summarize_closed_threads():
    """The fallback summary for threads that closed or timed out with none (summaries.py)."""
    from connect_labs.messaging import service
    from connect_labs.messaging.canopy_bridge import summaries

    if not service.enabled():
        return None
    with audit_context(source="celery"):
        return summaries.summarize_closed()


@celery_app.task(acks_late=True)
def import_ocs_history(worker_id: int):
    """Import one Connect Worker's past Open Chat Studio conversations (ocs_import.py)."""
    from connect_labs.messaging.canopy_bridge import ocs_import
    from connect_labs.workers.models import ConnectWorker

    worker = ConnectWorker.objects.filter(pk=worker_id, erased_at__isnull=True).first()
    if worker is None:
        return None
    with audit_context(source="celery"):
        return ocs_import.import_worker(worker)
