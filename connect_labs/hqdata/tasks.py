"""The schedule for CommCare project sync. The sync itself is a durable job (``jobs.py``)."""

from __future__ import annotations

import logging
from datetime import timedelta

from django.utils import timezone

from config import celery_app
from connect_labs.hqdata.alerts import stale_projects
from connect_labs.hqdata.jobs import queue_sync
from connect_labs.hqdata.models import HQProject, HQSyncRun
from connect_labs.hqdata.projects import Refused
from connect_labs.hqdata.sync import reap_interrupted_runs

logger = logging.getLogger(__name__)

# A project whose last sync failed is tried again at this pace, not on every tick of the schedule.
RETRY_AFTER_FAILURE = timedelta(minutes=15)


@celery_app.task(name="connect_labs.hqdata.tasks.sync_due_projects")
def sync_due_projects() -> dict:
    """Beat entry (every 5 minutes): queue a sync job for each persistent project that is due.

    Also closes the history of runs a stopped worker left unfinished. A project that already has a
    sync queued or running is left alone (``queue_sync`` dedupes), so a long first read, which runs as
    a chain of jobs, is never doubled up.
    """
    now = timezone.now()
    reaped = reap_interrupted_runs(now)
    queued = skipped = 0
    for project in HQProject.objects.filter(storage_mode=HQProject.STORAGE_PERSISTENT).exclude(
        status__in=[HQProject.STATUS_PAUSED, HQProject.STATUS_NEEDS_REAUTH]
    ):
        if not project.sync_due(now):
            continue
        if (
            project.status == HQProject.STATUS_ERROR
            and HQSyncRun.objects.filter(source__project=project, started_at__gt=now - RETRY_AFTER_FAILURE).exists()
        ):
            skipped += 1
            continue
        try:
            reply = queue_sync(project, actor="scheduler")
        except Refused as exc:
            logger.warning("hqdata: could not queue a sync of project %s: %s", project.pk, exc)
            skipped += 1
            continue
        if not reply.get("already_running"):
            queued += 1
    return {"queued": queued, "skipped": skipped, "reaped": reaped, "stale": len(stale_projects(now))}
