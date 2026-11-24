"""Audit reviews: bring the reviews up to date when the report they follow saves a run.

Without this, a new KC Pulse run reached the reviews only when someone next opened the page (and that
person waited while it was read in). Every place a workflow run is completed calls
``queue_sync_after_save`` next to ``benchmarks.tasks.queue_auto_publish``; the task runs off the request
thread, as the person who saved the run (their token travels with it, as for auto-publish), and does
what opening the page would: freeze the new run, then recompute.
"""

from __future__ import annotations

import logging

from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def sync_after_save(access_token: str, *, workflow_id: int) -> list[dict]:
    """Sync and recompute every audit workflow that follows ``workflow_id``. Returns what it did."""
    from connect_labs.audit_reviews import service
    from connect_labs.audit_reviews.models import AuditSource
    from connect_labs.workflow.data_access import WorkflowDataAccess

    report = []
    for source in AuditSource.objects.filter(source_workflow_id=int(workflow_id)):
        scope = (
            {"opportunity_id": source.opportunity_id} if source.opportunity_id else {"program_id": source.program_id}
        )
        outcome = {"audit_workflow": source.workflow_id}
        wda = None
        try:
            wda = WorkflowDataAccess(access_token=access_token, **scope)
            # Read as the saver: someone who cannot read the audit workflow syncs nothing for it.
            definition = wda.get_definition(source.workflow_id)
            if definition is None:
                outcome["skipped"] = "the audit workflow is not readable as the person who saved the run"
            else:
                source = service.open_source(definition, **scope)
                outcome["changed"] = service.sync(source, wda, definition)
                service.world(source)  # recompute now, so the next page load is quick
                outcome["ok"] = True
        except Exception as e:  # noqa: BLE001 -- the next page load syncs anyway; this is a head start
            logger.exception("audit_reviews: sync after a save of workflow %s failed", workflow_id)
            outcome["error"] = type(e).__name__
        finally:
            if wda is not None:
                wda.close()
        report.append(outcome)
    logger.info("audit_reviews: sync after a save of workflow %s: %s", workflow_id, report)
    return report


def queue_sync_after_save(data_access, *, workflow_id) -> bool:
    """Queue ``sync_after_save`` if an audit workflow follows ``workflow_id``. Never raises: the save
    that calls this has already succeeded and must stay succeeded."""
    try:
        from connect_labs.audit_reviews.models import AuditSource

        if not AuditSource.objects.filter(source_workflow_id=int(workflow_id)).exists():
            return False
        token = getattr(data_access, "access_token", None)
        if not token:
            return False
        sync_after_save.delay(token, workflow_id=int(workflow_id))
        return True
    except Exception:  # noqa: BLE001
        logger.warning("could not queue an audit reviews sync for workflow %s", workflow_id, exc_info=True)
        return False
