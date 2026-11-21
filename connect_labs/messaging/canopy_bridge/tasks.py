"""The canopy bridge's background work. Every task returns at once while the bridge is off."""

from __future__ import annotations

import logging

from config import celery_app
from connect_labs.audit_trail.context import audit_context

logger = logging.getLogger(__name__)


@celery_app.task(acks_late=True)
def push_worker_profile(worker_id: int):
    from connect_labs.messaging.canopy_bridge import conf, profile_push
    from connect_labs.workers.models import ConnectWorker

    if not conf.enabled():
        return None
    with audit_context(source="celery"):
        worker = ConnectWorker.objects.filter(pk=worker_id, erased_at__isnull=True).first()
        return profile_push.push([worker]) if worker else None


@celery_app.task
def sweep():
    """Keep canopy's profile cache current (S2)."""
    from connect_labs.messaging.canopy_bridge import conf, profile_push

    if not conf.enabled():
        return None
    with audit_context(source="celery"):
        out = {}
        try:
            out["profiles"] = profile_push.push_due()
        except Exception:  # noqa: BLE001 -- one part's failure never stops the rest
            logger.exception("canopy bridge: profile push sweep failed")
        return out
