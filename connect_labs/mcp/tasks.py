"""Celery tasks for long-running MCP jobs (``connect_labs/mcp/jobs.py`` explains the design)."""

from __future__ import annotations

import logging
import os
import uuid

from celery import signals
from django.conf import settings

from config import celery_app

logger = logging.getLogger(__name__)


def _soft_limit() -> int:
    # Under the Redis broker's one-hour visibility timeout, so a message is never
    # redelivered while its job is still legitimately running (jobs.py, point 4).
    return int(getattr(settings, "MCP_JOB_SOFT_TIME_LIMIT", 45 * 60))


@celery_app.task(
    bind=True,
    # Acknowledged when the job ENDS, so a deploy that stops the worker mid-job leaves
    # the message with the broker, which hands it to the new worker (jobs.py, 1-3).
    acks_late=True,
    reject_on_worker_lost=True,
    max_retries=None,
    soft_time_limit=_soft_limit(),
    time_limit=_soft_limit() + 300,
)
def run_mcp_job(self, job_id: str) -> str:
    """Run one long-running MCP job on the worker, or launch its standalone task."""
    from . import jobs
    from .models import MCPJob

    job = MCPJob.objects.filter(pk=job_id).only("state", "runner").first()
    if job is None or job.state in MCPJob.TERMINAL_STATES:
        return "done"
    if job.runner == MCPJob.STANDALONE and jobs.standalone_enabled() and not jobs.ecs_tasks.in_runner(jobs.KIND):
        jobs.launch_standalone(job_id)
        return "launched"
    hostname = getattr(self.request, "hostname", None) or ""
    outcome = jobs.execute(job_id, owner=f"{hostname}:{os.getpid()}:{uuid.uuid4().hex[:8]}", worker=hostname)
    if outcome == "held":
        # Another attempt holds it (a copy of this message redelivered while it runs):
        # look again once its lease would have lapsed, and take it over if it did.
        raise self.retry(countdown=jobs.held_countdown(job_id))
    return outcome


@celery_app.task
def mcp_job_housekeeping() -> dict:
    """Beat: fail lost jobs, reap standalone tasks, prune jobs past retention (jobs.housekeeping)."""
    from . import jobs

    return jobs.housekeeping()


@signals.worker_shutting_down.connect
def _on_worker_shutting_down(sender=None, how=None, **kwargs):
    """Hand back the jobs this worker holds, so the new worker takes them over at once (jobs.py, 5).

    Only on a COLD shutdown -- what a deploy sends (docker/start_celery remaps SIGTERM).
    That is the one that cancels running jobs and returns their messages to the broker.
    A warm shutdown lets them finish and acknowledges their messages, so a job handed
    back there would have no message left to run it, and its finished result would be
    refused for want of the lease.
    """
    from . import jobs

    if how != "Cold":
        return
    try:
        released = jobs.release_held_by(str(sender or ""))
    except Exception:  # noqa: BLE001 -- shutdown must go on; the lease lapses by itself
        logger.exception("could not hand back MCP jobs on worker shutdown")
        return
    if released:
        logger.warning("worker shutting down: handed back %d MCP job(s) to be run again: %s", len(released), released)
