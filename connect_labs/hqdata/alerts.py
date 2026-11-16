"""Telling someone when a project's sync stops working.

A sync fails quietly by nature: it runs on a schedule, as a job, with nobody watching. Two things
are reported, both as ERROR logs (which Sentry turns into alerts) and the first also on the audit
trail:

* a project that *newly* fails or needs its owner to sign in again (the change of state, not every
  retry, so one outage is one alert); and
* a project that is meant to stay up to date and has not synced for several times its interval,
  whatever the reason (a stuck job, a paused beat schedule, a lost worker).
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta

from django.utils import timezone

from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.hqdata.audit import project_event
from connect_labs.hqdata.models import HQProject

logger = logging.getLogger(__name__)

STALE_AFTER_INTERVALS = 3
STALE_FLOOR = timedelta(hours=3)


def sync_state_changed(project_id: int, before: str, after: str, error: str = "") -> None:
    """Report a project that has just stopped syncing. Silent when it was already in that state."""
    if after == before or after not in (HQProject.STATUS_ERROR, HQProject.STATUS_NEEDS_REAUTH):
        return
    logger.error("hqdata sync of project %s is now %s: %s", project_id, after, error[:300] or "no detail")
    project_event(
        Action.UPDATE, project_id, kind="sync", outcome=Outcome.FAILURE, metadata={"status": after, "was": before}
    )


def stale_projects(now: datetime | None = None) -> list[HQProject]:
    """Projects that should have synced by now and have not, reported once per call as errors."""
    now = now or timezone.now()
    stale = []
    live = HQProject.objects.filter(storage_mode=HQProject.STORAGE_PERSISTENT).exclude(
        status__in=[HQProject.STATUS_PAUSED, HQProject.STATUS_NEEDS_REAUTH]
    )
    for project in live:
        allowed = max(timedelta(minutes=project.sync_interval_minutes * STALE_AFTER_INTERVALS), STALE_FLOOR)
        since = project.last_synced_at or project.created_at
        if now - since > allowed:
            stale.append(project)
            logger.error(
                "hqdata project %s has not synced for %s (it should every %s minutes)",
                project.pk,
                now - since,
                project.sync_interval_minutes,
            )
    return stale
