"""Celery entry points for CommCare project sync."""

from __future__ import annotations

import logging

from django.utils import timezone

from config import celery_app
from connect_labs.hqdata.models import HQProject
from connect_labs.hqdata.sync import sync_project

logger = logging.getLogger(__name__)


@celery_app.task(name="connect_labs.hqdata.tasks.sync_project_task")
def sync_project_task(project_id: int, mode: str | None = None) -> dict:
    project = HQProject.objects.get(pk=project_id)
    results = sync_project(project, mode=mode)
    return {
        "project": project_id,
        "sources": {str(k): ("ok" if not isinstance(v, Exception) else type(v).__name__) for k, v in results.items()},
    }


@celery_app.task(name="connect_labs.hqdata.tasks.sync_due_projects")
def sync_due_projects() -> int:
    """Beat entry (every 5 minutes): queue each persistent project whose interval has elapsed."""
    now = timezone.now()
    queued = 0
    for project in HQProject.objects.filter(storage_mode=HQProject.STORAGE_PERSISTENT).exclude(
        status__in=[HQProject.STATUS_PAUSED, HQProject.STATUS_NEEDS_REAUTH]
    ):
        if project.sync_due(now):
            sync_project_task.delay(project.pk)
            queued += 1
    return queued
