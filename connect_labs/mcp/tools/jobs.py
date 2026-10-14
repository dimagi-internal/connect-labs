"""job_status / job_cancel: follow and stop the jobs long-running tools start (``mcp/jobs.py``).

Who sees a job. Only the person who started it, and only on a call at least as
privileged as the one that started it:

* a restricted call ("no user visit data") sees only jobs a restricted call started.
  Those were held to the restricted rules when they ran; a job a full-access call
  started may hold real visit data, so it is invisible here -- the same answer as a job
  that does not exist;
* a delegated token (canopy acting for a visitor) sees only jobs of tools its scopes
  reach.

An unknown id and somebody else's get the same answer, so a guessed id reveals nothing.

Synthetic jobs (``synthetic_clone_opp`` and the other synthetic tools) keep their own
records; ``job_status`` and ``job_cancel`` follow them too, by their task_id, so a caller
needs one status tool. ``synthetic_job_status`` still works.
"""

from __future__ import annotations

import uuid
from typing import Any

from ..models import MCPJob
from ..tool_registry import MCPToolError, register


def _visible_jobs(user):
    from .. import server

    access = server.get_access_token()
    qs = MCPJob.objects.filter(user=user)
    if server.restricted_call(access):
        qs = qs.filter(restricted=True)
    permitted = server.allowed_tools(access)
    if permitted is not None:
        qs = qs.filter(tool_name__in=permitted)
    return qs


def _may_follow_synthetic() -> bool:
    from .. import server

    permitted = server.allowed_tools(server.get_access_token())
    return permitted is None or "synthetic_job_status" in permitted


def _job(user, job_id: str) -> MCPJob | None:
    try:
        pk = uuid.UUID(str(job_id))
    except ValueError:
        return None
    return _visible_jobs(user).filter(pk=pk).first()


def _not_found(job_id: str) -> MCPToolError:
    return MCPToolError("NOT_FOUND", f"No job {job_id!r} started by your account.")


def describe(job: MCPJob, *, include_result: bool = True) -> dict[str, Any]:
    from ..jobs import STATE_WORDS

    out: dict[str, Any] = {
        "job_id": str(job.pk),
        "tool": job.tool_name,
        "state": job.state,
        "status": (
            (job.progress or {}).get("message") or STATE_WORDS.get(job.state, job.state)
            if job.state == MCPJob.RUNNING
            else STATE_WORDS.get(job.state, job.state)
        ),
        "runner": job.runner,
        "attempts": job.attempts,
        "created_at": job.created_at.isoformat() if job.created_at else None,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
    }
    if job.progress:
        out["progress"] = job.progress
    if job.state == MCPJob.SUCCEEDED and include_result:
        out["result"] = job.result
    if job.state == MCPJob.FAILED:
        out["error_code"] = job.error_code
        out["error"] = job.error
    return out


@register(
    name="job_status",
    description=(
        "Follow a background job: a long-running tool (workflow_preview_as_of, "
        "workflow_rebuild_history, ...) returns a job_id instead of its result when it takes "
        "more than a moment. Returns the job's state (queued | running | succeeded | failed | "
        "cancelled), its progress, and -- once it has succeeded -- the tool's full result under "
        "`result`, exactly what the tool would have returned. A job keeps running through a labs "
        "deploy, and its result is kept for a week.\n\n"
        "Call with no job_id to list your recent jobs (newest first, without their results) -- "
        "the way back to a job whose id you lost. Also follows synthetic jobs by their task_id. "
        "Only the account that started a job can read it."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "job_id": {"type": "string", "description": "The job_id a long-running tool returned."},
            "limit": {
                "type": "integer",
                "minimum": 1,
                "maximum": 50,
                "description": "With no job_id: how many recent jobs to list (default 10).",
            },
        },
        "additionalProperties": False,
    },
    is_write=False,
)
def job_status(user, *, job_id: str | None = None, limit: int = 10) -> dict[str, Any]:
    if not job_id:
        jobs = _visible_jobs(user).order_by("-created_at")[: max(1, min(int(limit), 50))]
        return {"jobs": [describe(j, include_result=False) for j in jobs]}
    job = _job(user, job_id)
    if job is not None:
        return describe(job)
    if _may_follow_synthetic() and not MCPJob.objects.filter(pk=_uuid_or_none(job_id)).exists():
        from .synthetic import synthetic_job_status

        try:
            out = synthetic_job_status(user, task_id=str(job_id))
        except MCPToolError:
            raise _not_found(job_id) from None
        return {**out, "job_id": str(job_id), "kind": "synthetic"}
    raise _not_found(job_id)


def _uuid_or_none(value) -> uuid.UUID | None:
    try:
        return uuid.UUID(str(value))
    except ValueError:
        return None


@register(
    name="job_cancel",
    description=(
        "Stop a background job you started (a job_id from a long-running tool, or a synthetic "
        "job's task_id). A queued job never starts; a running one is stopped and recorded "
        "cancelled. Work it had already written stays written. A job that has already ended is "
        "left as it is."
    ),
    input_schema={
        "type": "object",
        "properties": {"job_id": {"type": "string"}},
        "required": ["job_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def job_cancel(user, *, job_id: str) -> dict[str, Any]:
    from .. import jobs

    job = _job(user, job_id)
    if job is not None:
        if job.state in MCPJob.TERMINAL_STATES:
            return {**describe(job, include_result=False), "note": "Already ended; nothing to stop."}
        return describe(jobs.cancel(job), include_result=False)
    if _may_follow_synthetic() and not MCPJob.objects.filter(pk=_uuid_or_none(job_id)).exists():
        from connect_labs.labs.synthetic.tasks import cancel_job

        from .synthetic import _profile_task_owned_by

        if _profile_task_owned_by(user, str(job_id)):
            cancel_job(str(job_id))
            return {"job_id": str(job_id), "kind": "synthetic", "state": "REVOKED", "status": "Cancelled."}
    raise _not_found(job_id)
