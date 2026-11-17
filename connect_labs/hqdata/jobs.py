"""Syncing a project as a durable job.

A sync can take hours the first time, and labs deploys several times a day, each of which hard-kills
every worker. So a sync is a long-running MCP job (``mcp/jobs.py``): recorded in the labs database,
redelivered when its worker is lost, visible with ``job_status``, cancellable with ``job_cancel``, and
safe to run again from the start because every batch commits with its checkpoint.

A job is also time-boxed. It stops, cleanly, after ``sync_budget_seconds`` (under the worker's limit)
with everything it read stored, and queues its own continuation. A sync of any size is therefore a
chain of short jobs, none of which the worker ever has to kill.

Whose job it is. A person pressing "Sync now" or calling ``commcare_project_sync`` owns the job they started.
The schedule, and a continuation, run as the project's sync owner: the person who chose to keep the
project up to date, whose CommCare sign-in the sync uses anyway.
"""

from __future__ import annotations

import time

from django.conf import settings

from connect_labs.hqdata.models import HQProject
from connect_labs.hqdata.projects import Refused
from connect_labs.hqdata.sync import LEASE_SECONDS, summarize, sync_project

TOOL = "commcare_project_sync"


def sync_budget_seconds() -> int:
    """How long one job reads before handing over to its continuation: under the worker's soft limit."""
    soft = int(getattr(settings, "MCP_JOB_SOFT_TIME_LIMIT", 45 * 60))
    return int(getattr(settings, "HQ_SYNC_JOB_BUDGET_SECONDS", max(soft - 10 * 60, 5 * 60)))


def queue_sync(
    project: HQProject, *, user=None, mode: str | None = None, actor: str = "", dedupe: bool = True
) -> dict:
    """Queue a sync of ``project`` as a durable job and return its reply (``job_id``, ``state``...).

    ``user`` is whoever asked; with none, the project's sync owner. Raises ``Refused`` when there is
    nobody to run it as or the person already has as many jobs running as they may. With ``dedupe`` a
    sync already queued or running for the project is returned instead of a second one.
    """
    from connect_labs.mcp import jobs as mcp_jobs
    from connect_labs.mcp.models import MCPJob
    from connect_labs.mcp.tool_registry import MCPToolError, get_tool

    job_user = user or project.sync_owner
    if job_user is None:
        raise Refused("Nobody's CommCare sign-in is attached to this project.")
    if dedupe:
        live = MCPJob.objects.filter(
            tool_name=TOOL, state__in=MCPJob.LIVE_STATES, arguments__project_id=project.pk
        ).first()
        if live is not None:
            return {**mcp_jobs.reply(live), "already_running": True}
    try:
        return mcp_jobs.submit(
            get_tool(TOOL),
            job_user,
            {"project_id": project.pk, "mode": mode},
            restricted=False,
            actor=actor,
            wait=False,
        )
    except MCPToolError as exc:
        raise Refused(exc.message) from exc


def run_sync(project: HQProject, user, mode: str | None, progress) -> dict:
    """The body of the ``commcare_project_sync`` job: sync within the time budget, queue the rest."""
    deadline = time.monotonic() + sync_budget_seconds()
    results = sync_project(
        project,
        mode=mode,
        deadline=deadline,
        # A job a deploy killed is redelivered straight away; wait out the lease it left behind.
        wait_for_lease_s=LEASE_SECONDS + 30,
        progress=progress,
    )
    outcome = summarize(results)
    continued = None
    if outcome["more_to_read"] and not outcome["errors"]:
        try:
            continued = queue_sync(project, user=user, actor="continuation", dedupe=False).get("job_id")
        except Refused:
            continued = None  # the schedule picks it up at its next tick
    return {
        "project_id": project.pk,
        "complete": outcome["complete"],
        "rows_read": outcome["rows"],
        "rows_removed": outcome["removed"],
        "continues_as": continued,
        "errors": outcome["errors"],
    }
