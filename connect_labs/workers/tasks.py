"""Celery entry points for the Connect Worker sync.

* ``refresh_opportunities`` -- hourly. Which opportunities the sync identity can read.
* ``sync_due`` -- every 5 minutes. The cheap tier for each opportunity whose turn it is.
* ``sync_worker_task`` / ``sync_opportunity_task`` -- on demand (MCP ``worker_sync``, pages).

Cadence follows Pulse's visit tiering where it exists (`PulseCursor.tier` for the
opportunity's ``user_visits``): an active opportunity is synced every 30 minutes,
one whose visits have gone dormant every 6 hours, an inactive one daily. A failing
opportunity backs off (1 min doubling to 6 h), as Pulse's cursors do (#1277).

Cost of one opportunity's cheap tier: ~9 small export requests (user_data,
completed_module, assessment, completed_works, payment, task_types, assigned_tasks,
audit_report_entries, work_areas) plus the learn-module list once a day. Visits are
read from labs' own caches, never downloaded here.
"""

from __future__ import annotations

import logging
import time
from datetime import timedelta

from django.utils import timezone

from config import celery_app

from .identity import WorkerSyncAuthError
from .models import OpportunitySync

logger = logging.getLogger(__name__)

ACTIVE_INTERVAL = timedelta(minutes=30)
DORMANT_INTERVAL = timedelta(hours=6)
INACTIVE_INTERVAL = timedelta(hours=24)
MAX_BACKOFF = timedelta(hours=6)
# One run's budget, well under the 5-minute beat so runs never overlap.
RUN_SECONDS = 240
RUN_LIMIT = 25


def interval_for(opp: OpportunitySync, tier: str | None) -> timedelta:
    if not opp.is_active:
        return INACTIVE_INTERVAL
    if tier == "dormant":
        return DORMANT_INTERVAL
    return ACTIVE_INTERVAL


def next_due(opp: OpportunitySync, tier: str | None):
    if opp.last_attempt_at is None:
        return None  # never tried: due now
    if opp.consecutive_failures:
        backoff = timedelta(seconds=min(MAX_BACKOFF.total_seconds(), 60 * 2 ** min(opp.consecutive_failures, 12)))
        return opp.last_attempt_at + backoff
    return (opp.last_synced_at or opp.last_attempt_at) + interval_for(opp, tier)


def due_opportunities(now=None, limit: int = RUN_LIMIT) -> list[OpportunitySync]:
    from connect_labs.pulse.models import PulseCursor

    now = now or timezone.now()
    opps = list(OpportunitySync.objects.filter(visible=True, is_test=False))
    tiers = dict(
        PulseCursor.objects.filter(
            opportunity_id__in=[o.opportunity_id for o in opps], endpoint="user_visits"
        ).values_list("opportunity_id", "tier")
    )
    due = []
    for opp in opps:
        at = next_due(opp, tiers.get(opp.opportunity_id))
        if at is None or at <= now:
            due.append((at or opp.updated_at, opp))
    due.sort(key=lambda pair: pair[0])
    return [opp for _, opp in due[:limit]]


def _fresh_source():
    from .connect_facade import get_source

    return get_source()


@celery_app.task(name="connect_labs.workers.tasks.refresh_opportunities")
def refresh_opportunities() -> dict:
    from .sync import refresh_opportunities as refresh

    with _fresh_source() as source:
        return refresh(source)


@celery_app.task(name="connect_labs.workers.tasks.sync_due")
def sync_due(limit: int = RUN_LIMIT) -> dict:
    from .sync import sync_opportunity

    started = time.monotonic()
    report = {"synced": [], "failed": {}, "skipped": 0}
    opps = due_opportunities(limit=limit)
    if not opps:
        return report
    try:
        source = _fresh_source()
    except WorkerSyncAuthError:
        logger.error("[workers] sync identity unusable; nothing synced", exc_info=True)
        raise
    with source:
        for opp in opps:
            if time.monotonic() - started > RUN_SECONDS:
                report["skipped"] += 1
                continue
            try:
                # Memoised pulls must not outlive their opportunity.
                source.reset()
                result = sync_opportunity(source, opp)
                report["synced"].append(result["opportunity_id"])
            except Exception as exc:  # noqa: BLE001 -- recorded on the row; the next opportunity still runs
                logger.warning("[workers] sync of opp %s failed: %s", opp.opportunity_id, exc)
                report["failed"][opp.opportunity_id] = str(exc)[:300]
    return report


@celery_app.task(name="connect_labs.workers.tasks.sync_worker_task")
def sync_worker_task(username: str, discover: bool = False) -> dict:
    from .sync import sync_worker

    with _fresh_source() as source:
        return sync_worker(username, source=source, discover=discover)


@celery_app.task(name="connect_labs.workers.tasks.sync_opportunity_task")
def sync_opportunity_task(opportunity_id: int) -> dict:
    from .sync import opportunity_for, sync_opportunity

    with _fresh_source() as source:
        return sync_opportunity(source, opportunity_for(int(opportunity_id)))
