"""Hooks into Messaging that do not change it: a new channel means canopy should know the worker."""

from __future__ import annotations

import logging

from django.db import transaction
from django.db.models.signals import post_save

logger = logging.getLogger(__name__)


def address_saved(sender, instance, created, **kwargs):
    """A worker's first channel: register them with canopy before their first message (S2)."""
    if not created:
        return
    from connect_labs.messaging.canopy_bridge import conf, profile_push

    if not conf.enabled():
        return
    profile_push.mark_dirty([instance.worker_id])
    worker_id = instance.worker_id

    def enqueue():
        from connect_labs.messaging.canopy_bridge import tasks

        try:
            tasks.push_worker_profile.delay(worker_id)
        except Exception:  # noqa: BLE001 -- the row is dirty; the sweep pushes it
            logger.warning("canopy bridge: could not queue a profile push for worker %s", worker_id, exc_info=True)

    transaction.on_commit(enqueue)


def connect() -> None:
    from connect_labs.messaging.models import Address

    post_save.connect(address_saved, sender=Address, dispatch_uid="canopy_bridge.address_saved")
