"""Celery tasks for the supply domain.

At the app root because `app.autodiscover_tasks()` finds `<app>.tasks` and
nothing deeper. The work itself lives in `alerts/service.py` and
`stock/services/visit_reader.py`.
"""

from config import celery_app
from connect_labs.audit_trail.context import audit_context


@celery_app.task
def send_supply_alerts() -> dict:
    """Run every active alert subscription and email what is new.

    Scheduled every five minutes by migration 0012.
    """
    from connect_labs.supply_chain.alerts import service

    with audit_context(source="celery"):
        return service.run_alerts()


@celery_app.task
def ingest_visit_consumption() -> dict:
    """Read every active dispensing rule's visits into the ledger. Hourly, by migration 0039.

    Synthetic programmes only (design 2026-09-28 §1); a real one is skipped by name.
    """
    from connect_labs.supply_chain.stock.services import visit_reader

    with audit_context(source="celery"):
        return visit_reader.run_scheduled()
