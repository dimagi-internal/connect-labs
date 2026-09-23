"""Celery tasks for the supply domain.

At the app root because `app.autodiscover_tasks()` finds `<app>.tasks` and
nothing deeper. The work itself lives in `alerts/service.py`.
"""

from config import celery_app
from connect_labs.audit_trail.context import audit_context


@celery_app.task
def send_supply_alerts() -> dict:
    """Run every active alert subscription and email what is new.

    Scheduled every five minutes by migration 0007.
    """
    from connect_labs.supply_chain.alerts import service

    with audit_context(source="celery"):
        return service.run_alerts()
