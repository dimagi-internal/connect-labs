"""Republishing a cohort off the request thread.

A publication reads the source workflow's whole run history and writes thousands
of rows -- 30-60s on the KMC cohort -- which must not be added to the click that
saved the report. So the save queues this and returns. The user's own access
token travels with the task, the same way `workflow.tasks.run_workflow_job`
carries one, because the run and its history are read as that user.
"""

from __future__ import annotations

import logging

from config import celery_app

logger = logging.getLogger(__name__)


@celery_app.task
def auto_publish_after_save(
    access_token: str,
    *,
    run_id: int | None = None,
    workflow_id: int | None = None,
    opportunity_id: int | None = None,
    program_id: int | None = None,
) -> list[dict]:
    """With `run_id`: republish from that run. Without: from the workflow's newest run."""
    from connect_labs.benchmarks.auto_publish import publish_for_completed_run, publish_latest_for_workflow
    from connect_labs.workflow.data_access import WorkflowDataAccess

    wda = WorkflowDataAccess(access_token=access_token, opportunity_id=opportunity_id, program_id=program_id)
    try:
        if run_id is None:
            report = publish_latest_for_workflow(wda, int(workflow_id))
        else:
            run = wda.get_run(int(run_id))
            report = publish_for_completed_run(wda, run) if run is not None else []
    finally:
        wda.close()
    logger.info("auto-publish (workflow %s, run %s): %s", workflow_id, run_id, report)
    return report


def queue_auto_publish(data_access, *, workflow_id, run_id: int | None = None) -> bool:
    """Queue a republish if any cohort follows `workflow_id`. Never raises: the
    save that calls this has already succeeded and must stay succeeded."""
    try:
        from connect_labs.benchmarks.auto_publish import cohorts_following

        if not cohorts_following(workflow_id):
            return False
        auto_publish_after_save.delay(
            getattr(data_access, "access_token", None),
            run_id=run_id,
            workflow_id=int(workflow_id),
            opportunity_id=getattr(data_access, "opportunity_id", None),
            program_id=getattr(data_access, "program_id", None),
        )
        return True
    except Exception:  # noqa: BLE001
        logger.warning("could not queue auto-publish for workflow %s", workflow_id, exc_info=True)
        return False
