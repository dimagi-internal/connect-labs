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


@celery_app.task
def top_up_clone_supply() -> dict:
    """Record the invented deliveries due on every seeded clone (demo/clone_supply.py). Weekly, by migration 0047.

    Synthetic programmes only: top_up refuses anything else before it reads a visit.
    """
    from connect_labs.supply_chain.demo import clone_supply

    results = {}
    with audit_context(source="celery"):
        for program_id, opportunity_id in clone_supply.seeded_clones():
            try:
                results[f"{program_id}/{opportunity_id}"] = clone_supply.top_up(
                    program_id=program_id, opportunity_id=opportunity_id
                )
            except ValueError as error:
                results[f"{program_id}/{opportunity_id}"] = {"topped_up": False, "reason": str(error)}
    return results
