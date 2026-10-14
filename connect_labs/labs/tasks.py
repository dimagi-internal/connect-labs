"""
Celery tasks for labs analysis framework.
"""

import logging

from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task()
def cleanup_expired_sql_cache():
    """
    Clean up expired SQL cache entries and expired user synthetic datasets.

    Should be scheduled to run periodically (e.g., hourly) via Celery Beat.
    """
    from django.utils import timezone

    from connect_labs.labs.analysis.backends.sql.models import ComputedFLWCache, ComputedVisitCache, RawVisitCache
    from connect_labs.labs.synthetic.models import UserSyntheticDataset

    raw_deleted = RawVisitCache.cleanup_expired()
    visit_deleted = ComputedVisitCache.cleanup_expired()
    flw_deleted = ComputedFLWCache.cleanup_expired()

    synthetic_deleted, _ = UserSyntheticDataset.objects.filter(expires_at__lte=timezone.now()).delete()

    total = raw_deleted + visit_deleted + flw_deleted
    if total > 0:
        logger.info(
            f"[SQLCache] Cleaned up {total} expired entries: "
            f"{raw_deleted} raw, {visit_deleted} computed visits, {flw_deleted} FLW results"
        )
    if synthetic_deleted > 0:
        logger.info(f"[SyntheticData] Cleaned up {synthetic_deleted} expired user synthetic datasets")

    return {"raw": raw_deleted, "computed_visits": visit_deleted, "flw": flw_deleted, "synthetic": synthetic_deleted}


@celery_app.task(name="connect_labs.labs.tasks.sync_connect_organizations")
def sync_connect_organizations():
    """Link the organisation registry to Connect's organisations (`labs.connect_orgs`).

    On beat because Connect's organisations change without labs hearing of it:
    one is created, one merges two workspaces and deletes the other. Daily, after
    the directory import, so a slug attribution added to the sheet that morning
    is linked the same day.
    """
    from django.core.management import call_command

    call_command("connect_org_sync")
