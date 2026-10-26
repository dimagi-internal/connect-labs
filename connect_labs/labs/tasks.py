"""
Celery tasks for labs analysis framework.
"""

import logging

from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task()
def cleanup_expired_sql_cache():
    """
    Clean up expired SQL cache entries.

    Should be scheduled to run periodically (e.g., hourly) via Celery Beat.
    """
    from connect_labs.labs.analysis.backends.sql.models import ComputedFLWCache, ComputedVisitCache, RawVisitCache

    raw_deleted = RawVisitCache.cleanup_expired()
    visit_deleted = ComputedVisitCache.cleanup_expired()
    flw_deleted = ComputedFLWCache.cleanup_expired()

    total = raw_deleted + visit_deleted + flw_deleted
    if total > 0:
        logger.info(
            f"[SQLCache] Cleaned up {total} expired entries: "
            f"{raw_deleted} raw, {visit_deleted} computed visits, {flw_deleted} FLW results"
        )

    return {"raw": raw_deleted, "computed_visits": visit_deleted, "flw": flw_deleted}
