"""Background work for communications: delivery reports, and settling what the mail path left open."""

from __future__ import annotations

import logging

from config import celery_app
from connect_labs.audit_trail.context import audit_context

logger = logging.getLogger(__name__)


@celery_app.task(bind=True, max_retries=6, autoretry_for=(Exception,), retry_backoff=30, retry_backoff_max=900)
def record_delivery(self, sent_message_id, delivered: bool, failed: bool = False):
    """Record what became of one message, apart from sending it.

    The mail task queues this once the mail service has answered, so a database
    that is unavailable at that moment delays the record (retried with back-off)
    instead of losing it -- and never resends the email. What it could not
    record in the end, `reconcile_communications` settles.
    """
    from connect_labs.communications import service

    with audit_context(source="celery"):
        if delivered:
            service.delivered(sent_message_id)
        else:
            service.not_delivered(sent_message_id, failed=failed)


@celery_app.task
def reconcile_communications() -> dict:
    """Every ten minutes (migration 0004): lost messages become "Outcome unknown";
    failed or stalled follow-ups are retried with back-off."""
    from connect_labs.communications import service

    with audit_context(source="celery"):
        return service.reconcile()
