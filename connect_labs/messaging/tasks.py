"""Messaging's background work: sending, and doing what connect-id told us.

Nothing here runs while `MESSAGING_ENABLED` is off, except to mark a queued message failed.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from config import celery_app
from connect_labs.audit_trail.context import audit_context

logger = logging.getLogger(__name__)

#: How many times a send is retried when PersonalID cannot be reached (back-off 30 s doubling,
#: so about half an hour in all) before the message is marked failed for a person to see.
DELIVERY_RETRIES = 6
#: A queued message older than this with no task in flight is queued again by the sweep.
STALE_QUEUED = timedelta(minutes=5)
#: A transport event not processed this long after it came in is processed again by the sweep.
STALE_EVENT = timedelta(minutes=2)
MAX_EVENT_ATTEMPTS = 5


@celery_app.task(bind=True, max_retries=DELIVERY_RETRIES, acks_late=True)
def deliver_message(self, message_id: int):
    from connect_labs.messaging import service
    from connect_labs.messaging.transport import TransportUnavailable

    with audit_context(source="celery"):
        try:
            return service.deliver(message_id)
        except TransportUnavailable as e:
            if self.request.retries >= DELIVERY_RETRIES:
                service.give_up(message_id, f"PersonalID could not be reached ({e.code})")
                return "failed"
            raise self.retry(countdown=30 * (2**self.request.retries)) from e


@celery_app.task(acks_late=True)
def process_transport_event(event_id: int):
    from connect_labs.messaging import service
    from connect_labs.messaging.models import TransportEvent

    with audit_context(source="celery"), transaction.atomic():
        event = TransportEvent.objects.select_for_update(skip_locked=True).filter(pk=event_id).first()
        if event is None or event.processed_at is not None:
            return
        event.attempts += 1
        try:
            with transaction.atomic():
                problems = service.process_event(event)
            if problems:
                logger.warning("messaging: transport event %s: %s", event.pk, problems)
            event.error = problems
            event.processed_at = timezone.now()
        except (LookupError, ValueError) as e:
            # Not worth retrying: a channel that is not ours, a post we cannot read.
            logger.warning("messaging: transport event %s: %s", event.pk, e)
            event.error = str(e)[:500]
            event.processed_at = timezone.now()
        except Exception as e:  # noqa: BLE001 -- left unprocessed; the sweep tries again
            logger.exception("messaging: transport event %s failed", event.pk)
            event.error = f"{type(e).__name__}: {e}"[:500]
            if event.attempts >= MAX_EVENT_ATTEMPTS:
                event.processed_at = timezone.now()
        event.save(update_fields=["attempts", "error", "processed_at"])


def enqueue_event(event_id: int) -> None:
    try:
        process_transport_event.delay(event_id)
    except Exception:  # noqa: BLE001 -- the event is stored; the sweep processes it
        logger.warning("messaging: could not queue transport event %s", event_id, exc_info=True)


@celery_app.task
def sweep():
    """Pick up anything left behind: events not processed, messages queued with no task in flight."""
    from connect_labs.messaging import service
    from connect_labs.messaging.models import DeliveryStatus, Message, TransportEvent

    if not service.enabled():
        return
    now = timezone.now()
    for event_id in TransportEvent.objects.filter(
        processed_at__isnull=True, received_at__lt=now - STALE_EVENT
    ).values_list("pk", flat=True)[:500]:
        enqueue_event(event_id)
    stale = Message.objects.filter(delivery_status=DeliveryStatus.QUEUED, created_at__lt=now - STALE_QUEUED).exclude(
        scheduled_for__gt=now
    )
    for message_id in stale.values_list("pk", flat=True)[:500]:
        try:
            deliver_message.delay(message_id)
        except Exception:  # noqa: BLE001
            logger.warning("messaging: could not queue message %s", message_id, exc_info=True)
    # Threads whose debounced hand-off never ran (a lost task), and threads idle past their exit rule.
    from connect_labs.messaging import router
    from connect_labs.messaging.models import Thread

    for thread_id in Thread.objects.filter(inbound_due_at__lt=now - timedelta(minutes=1)).values_list("pk", flat=True)[
        :200
    ]:
        try:
            handle_inbound.delay(thread_id)
        except Exception:  # noqa: BLE001
            logger.warning("messaging: could not queue thread %s", thread_id, exc_info=True)
    router.close_idle(now)
    # State-event playbooks: what the sync wrote to the workers' timeline since the last sweep.
    router.scan_worker_events(now=now)


@celery_app.task(acks_late=True)
def handle_inbound(thread_id: int):
    """The debounced hand-off of a worker's messages to their thread's handler (`router.handle_due`)."""
    from connect_labs.messaging import router

    with audit_context(source="celery"):
        return router.handle_due(thread_id)


@celery_app.task
def run_schedules():
    """Start the schedule playbooks due this hour (UTC), once per period each."""
    from connect_labs.messaging import router

    with audit_context(source="celery"):
        return router.run_schedules()
