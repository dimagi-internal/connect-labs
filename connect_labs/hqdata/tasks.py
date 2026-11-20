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


@celery_app.task(name="connect_labs.hqdata.tasks.fill_computed_source", soft_time_limit=40 * 60, time_limit=45 * 60)
def fill_computed_source(project_id: int, user_id: int, name: str, params: dict, token: str) -> None:
    """Compute one slow computed source of a project as the person who asked, keeping it (``computed.fill``)."""
    from django.contrib.auth import get_user_model

    from connect_labs.hqdata import computed

    project = HQProject.objects.get(pk=project_id)
    user = get_user_model().objects.get(pk=user_id)
    computed.fill(user, project, name, params, token)


@celery_app.task(name="connect_labs.hqdata.tasks.refresh_computed_sources")
def refresh_computed_sources() -> dict:
    """Beat entry (every 10 minutes): keep the slow computed sources of every kept-up-to-date project computed, as
    the project's sync owner, so its pages are ready when opened (``computed.refresh``)."""
    from connect_labs.hqdata import computed

    started = skipped = 0
    for project in HQProject.objects.filter(storage_mode=HQProject.STORAGE_PERSISTENT, sync_owner__isnull=False):
        try:
            started += len(computed.refresh(project.sync_owner, project))
        except Exception:  # noqa: BLE001 -- one project's trouble (a lapsed membership) never stops the others
            logger.warning("hqdata: could not refresh computed sources of project %s", project.pk, exc_info=True)
            skipped += 1
    return {"started": started, "skipped": skipped}
