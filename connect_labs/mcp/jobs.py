"""Long-running MCP tools run as durable jobs that survive a labs deploy.

A tool registered with ``long_running=True`` (``tool_registry.register``) is not run
inside the web request. The call passes every gate a tool call passes (scope,
restricted-endpoint schema, generated-only visit access, the labs-only gate, the write
limit), then :func:`submit` writes an :class:`~connect_labs.mcp.models.MCPJob` and
queues it. The caller gets ``{job_id, state, poll_with: "job_status"}`` -- or, after a
short wait, the result itself -- and follows the job with the ``job_status`` tool
(``tools/jobs.py``), which shows a job only to the person who started it and only on a
call at least as privileged as the one that started it.

Why. A grading run (``workflow_preview_as_of``) takes minutes. Inside the request, a
deploy replaced the web task mid-call and the client got "transport dropped mid-call",
the result lost; labs deploys several times a day.

WHERE A JOB RUNS -- the tool declares it (``job_runner``):

* ``"worker"`` (the default): the existing Celery worker service, through the Redis
  queue. This is for nearly everything: minutes, up to ``MCP_JOB_SOFT_TIME_LIMIT``.
* ``"standalone"``: a Fargate task of its own (``connect_labs.labs.ecs_tasks``, the
  runner synthetic jobs use), for work of an hour or more. The worker picks the job
  off the queue and launches the task; the task runs ``manage.py run_mcp_job``.

The same record, the same ``job_status``, either way.

HOW A WORKER JOB SURVIVES A DEPLOY. A deploy replaces the worker service: ECS sends
SIGTERM, which ``docker/start_celery`` remaps to Celery's cold shutdown (SIGQUIT), and
SIGKILLs it at ``stopTimeout`` (30s). Five things together turn that from "job lost" into
"job runs again on the new worker":

1. ``acks_late=True`` (``tasks.run_mcp_job``): the queue message is acknowledged only
   when the job has ended, so a message whose job did not end is still the broker's.
2. Cold shutdown cancels an unacknowledged ``acks_late`` request without acknowledging
   it (celery ``Consumer.cancel_active_requests``), and kombu's Redis transport puts
   every unacknowledged message back on the queue when the channel closes
   (``QoS.restore_at_shutdown``). The new worker -- started before the old one stops,
   because the deploy is rolling -- receives it straight away.
3. ``reject_on_worker_lost=True``: a job whose PROCESS dies under it (out of memory) is
   requeued rather than recorded failed.
4. If the process is killed before it can do (2), the message stays in Redis's unacked
   set and the transport redelivers it after the broker's visibility timeout (one
   hour, kombu's default). That is why a worker job's soft limit is under an hour: a
   message is never redelivered while its job is still legitimately running.
5. The lease. A redelivered message may arrive while the job is held, so a job is
   CLAIMED before it runs (:func:`_claim`): held by one worker until ``lease_until``,
   renewed every ``MCP_JOB_HEARTBEAT_SECONDS`` by a heartbeat thread. A copy that finds
   it held waits (a Celery retry) and a copy that finds it lapsed takes it over. On
   shutdown the worker hands back the leases it holds (``tasks._on_worker_shutting_down``)
   and that attempt does not count against the job's ``MCP_JOB_MAX_ATTEMPTS``; an
   attempt that ended any other way does, so a job that kills its worker every time
   stops after a few tries instead of looping.

So a long-running tool's handler must be SAFE TO RUN AGAIN FROM THE START: a rerun is
what recovery is. ``workflow_preview_as_of`` persists nothing; a
``workflow_rebuild_history`` batch replaces the runs it writes.

The result is stored on the job row (labs DB), not in Celery's result backend, and kept
for ``MCP_JOB_RETENTION_DAYS``. The housekeeping beat (``tasks.mcp_job_housekeeping``)
prunes old jobs, fails jobs that were lost altogether, and reaps standalone tasks.

TESTS AND LOCAL DEV. ``MCP_JOBS_RUN_INLINE`` (on in tests) runs the job in the calling
thread at submit, through the same claim/run/finish path, so no broker or AWS is
needed. Local dev's ``CELERY_TASK_ALWAYS_EAGER`` has the same effect through Celery.
"""

from __future__ import annotations

import logging
import os
import socket
import threading
import time
import uuid
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import connections, transaction
from django.db.models import F
from django.utils import timezone

from connect_labs.labs import ecs_tasks
from connect_labs.labs.progress import NULL_PROGRESS, safe_call

from .models import MCPJob
from .tool_registry import MCPToolError, get_tool

logger = logging.getLogger(__name__)

#: Long-running MCP jobs as a kind of standalone job (``connect_labs.labs.ecs_tasks``).
KIND = ecs_tasks.Kind(
    started_by="mcp-job",
    command="run_mcp_job",
    runner_env="LABS_MCP_JOB_RUNNER",
    label="long-running job",
    # Searched for by the labs-jj-synthetic-job-overran alarm (infra/labs-synthetic-jobs.yml).
    overran_log="long-running job overran",
)

STATE_WORDS = {
    MCPJob.QUEUED: "Waiting to start.",
    MCPJob.RUNNING: "Running.",
    MCPJob.SUCCEEDED: "Done.",
    MCPJob.FAILED: "Failed.",
    MCPJob.CANCELLED: "Cancelled.",
}


def _setting(name: str, default):
    return getattr(settings, name, default)


def lease_seconds() -> int:
    return int(_setting("MCP_JOB_LEASE_SECONDS", 120))


def heartbeat_seconds() -> int:
    return int(_setting("MCP_JOB_HEARTBEAT_SECONDS", 30))


def max_attempts() -> int:
    return int(_setting("MCP_JOB_MAX_ATTEMPTS", 3))


def wait_seconds() -> float:
    return float(_setting("MCP_JOB_WAIT_SECONDS", 30))


def standalone_enabled() -> bool:
    return bool(_setting("MCP_JOBS_ON_ECS", False))


def standalone_max_runtime_seconds() -> int:
    return int(_setting("MCP_JOB_STANDALONE_MAX_RUNTIME_SECONDS", 6 * 3600))


# ---------------------------------------------------------------------------
# Submitting (the web request)
# ---------------------------------------------------------------------------


def submit(
    spec,
    user,
    arguments: dict,
    *,
    restricted: bool,
    client_id: str = "",
    actor: str = "",
    wait: bool = True,
    progress=NULL_PROGRESS,
) -> dict[str, Any]:
    """Record and queue a job for a long-running tool call; wait briefly for it if asked.

    Called by ``server._run_registry_tool_inner`` once every gate has passed. Raises
    MCPToolError for the caller: RATE_LIMITED past the per-person limit, the job's own
    error if it failed within the wait.
    """
    live = MCPJob.objects.filter(user=user, state__in=MCPJob.LIVE_STATES).count()
    limit = int(_setting("MCP_JOB_MAX_LIVE_PER_USER", 5))
    if live >= limit:
        raise MCPToolError(
            "RATE_LIMITED",
            f"You have {live} long-running jobs queued or running (the limit is {limit}). "
            "Follow them with job_status, or stop one with job_cancel, then try again.",
        )
    job = MCPJob.objects.create(
        id=uuid.uuid4(),
        user=user,
        tool_name=spec.name,
        arguments=arguments,
        restricted=bool(restricted),
        client_id=(client_id or "")[:255],
        actor=(actor or "")[:100],
        runner=spec.job_runner,
    )
    try:
        _dispatch(job)
    except Exception as exc:  # noqa: BLE001 -- the broker refused it; say so, do not leave it queued
        logger.exception("could not queue MCP job %s (%s)", job.id, spec.name)
        MCPJob.objects.filter(pk=job.pk).update(
            state=MCPJob.FAILED,
            error_code="UPSTREAM_ERROR",
            error=f"Could not queue the job: {exc}",
            finished_at=timezone.now(),
        )
        raise MCPToolError("UPSTREAM_ERROR", f"Could not queue {spec.name}: {exc}") from exc
    if not wait:
        return reply(job)
    return _wait_for(job, progress)


def _dispatch(job: MCPJob) -> None:
    if _setting("MCP_JOBS_RUN_INLINE", False):
        execute(str(job.id), owner=f"inline:{os.getpid()}:{uuid.uuid4().hex[:8]}", heartbeat=False)
        return
    from .tasks import run_mcp_job

    # The job id is the task id, so revoking the task (job_cancel) names the job.
    run_mcp_job.apply_async(args=[str(job.id)], task_id=str(job.id))


def reply(job: MCPJob) -> dict[str, Any]:
    """The reply for a job that has not finished: where it is and how to follow it."""
    return {
        "job_id": str(job.id),
        "state": job.state,
        "status": STATE_WORDS.get(job.state, job.state),
        "poll_with": "job_status",
        "note": (
            f"{job.tool_name} is running as a background job, which a labs deploy does not lose. "
            "Call job_status with this job_id for its progress and, when it is done, its result."
        ),
        **({"progress": job.progress} if job.progress else {}),
    }


def _wait_for(job: MCPJob, progress) -> dict[str, Any]:
    """Wait up to ``MCP_JOB_WAIT_SECONDS`` for the job; return its result or the reply.

    Short on purpose. A wait is the part of a long-running call that a deploy can
    still cut off, so the job id must reach the caller long before a deploy is likely
    -- and each progress notification sent while waiting carries it, so even a call
    cut off mid-wait leaves the id in the client's transcript.
    """
    deadline = time.monotonic() + wait_seconds()
    last_seen = None
    while True:
        job.refresh_from_db()
        if job.state in MCPJob.TERMINAL_STATES:
            break
        if time.monotonic() >= deadline:
            return reply(job)
        p = job.progress or {}
        if p != last_seen:
            last_seen = p
            message = f"job {job.id}: {p.get('message') or STATE_WORDS[job.state]}"
            safe_call(progress, p.get("current") or 0, p.get("total"), message)
        time.sleep(1)
    if job.state == MCPJob.SUCCEEDED:
        return with_job_id(job.result, job)
    if job.state == MCPJob.FAILED:
        raise MCPToolError(job.error_code or "UPSTREAM_ERROR", job.error or f"{job.tool_name} failed.")
    return reply(job)


def with_job_id(result, job: MCPJob) -> dict[str, Any]:
    if isinstance(result, dict):
        return {**result, "job_id": str(job.id)}
    return {"value": result, "job_id": str(job.id)}


# ---------------------------------------------------------------------------
# Running (the worker, a standalone task, or inline)
# ---------------------------------------------------------------------------


def _claim(job_id: str, owner: str, worker: str, lease: int) -> str:
    """Take the job for this attempt. Returns "claimed", "held" (someone else has it), or "done".

    "done" covers a job that has ended (a late copy of its message), one cancelled
    while queued, a job that no longer exists, and one out of attempts (failed here).
    """
    now = timezone.now()
    with transaction.atomic():
        job = MCPJob.objects.select_for_update().filter(pk=job_id).first()
        if job is None or job.state in MCPJob.TERMINAL_STATES:
            return "done"
        if job.cancel_requested:
            job.state, job.finished_at = MCPJob.CANCELLED, now
            job.save(update_fields=["state", "finished_at", "updated_at"])
            return "done"
        if job.state == MCPJob.RUNNING and job.lease_until and job.lease_until > now:
            return "held"
        if job.attempts >= max_attempts():
            job.state, job.finished_at, job.error_code = MCPJob.FAILED, now, "UPSTREAM_ERROR"
            job.error = (
                f"Stopped after {job.attempts} attempts, each of which ended without finishing "
                "(its worker was lost -- out of memory, or killed). Start it again; if it fails "
                "the same way, it may need job_runner='standalone'."
            )
            job.lease_owner, job.lease_until = "", None
            job.save()
            logger.error("MCP job %s (%s): out of attempts", job.pk, job.tool_name)
            return "done"
        job.state = MCPJob.RUNNING
        job.attempts += 1
        job.lease_owner = owner
        job.lease_until = now + timedelta(seconds=lease)
        job.worker = worker
        job.started_at = job.started_at or now
        job.save()
    return "claimed"


def held_countdown(job_id: str) -> int:
    """Seconds until a held job's lease lapses (when a waiting copy should look again)."""
    job = MCPJob.objects.filter(pk=job_id).only("lease_until").first()
    if job is None or job.lease_until is None:
        return 5
    return max(5, int((job.lease_until - timezone.now()).total_seconds()) + 5)


class _Heartbeat(threading.Thread):
    """Renew the job's lease while it runs, from a thread of its own (its own DB connection)."""

    def __init__(self, job_id: str, owner: str):
        super().__init__(name=f"mcp-job-heartbeat-{job_id}", daemon=True)
        self.job_id, self.owner = job_id, owner
        self._stop = threading.Event()

    def run(self):
        try:
            while not self._stop.wait(heartbeat_seconds()):
                renewed = MCPJob.objects.filter(pk=self.job_id, lease_owner=self.owner, state=MCPJob.RUNNING).update(
                    lease_until=timezone.now() + timedelta(seconds=lease_seconds())
                )
                if not renewed:  # cancelled, or handed back by a shutdown
                    return
        except Exception:  # noqa: BLE001 -- a missed renewal is recoverable; a crash here must not be
            logger.exception("MCP job %s: heartbeat failed", self.job_id)
        finally:
            connections.close_all()  # this thread's connections only

    def stop(self):
        self._stop.set()


def _reporter(job_id: str, owner: str):
    """The job's ``progress`` callback: store the latest progress on the job, a few times a minute."""
    state = {"last": 0.0}

    def report(current, total=None, message=None):
        now = time.monotonic()
        if now - state["last"] < 2 and not (total and current and current >= total):
            return
        state["last"] = now
        MCPJob.objects.filter(pk=job_id, lease_owner=owner, state=MCPJob.RUNNING).update(
            progress={"current": current, "total": total, "message": str(message or "")}
        )

    return report


def _finish(job_id: str, owner: str, state: str, **fields) -> bool:
    """Record how the job ended -- only if this attempt still holds it (not cancelled, not handed back)."""
    done = MCPJob.objects.filter(pk=job_id, lease_owner=owner, state=MCPJob.RUNNING).update(
        state=state, finished_at=timezone.now(), lease_owner="", lease_until=None, **fields
    )
    if not done:
        logger.warning("MCP job %s: ended (%s) after it was cancelled or handed back; not recorded", job_id, state)
    return bool(done)


def execute(job_id: str, *, owner: str, worker: str = "", heartbeat: bool = True, lease: int | None = None) -> str:
    """Claim the job and run its tool as the person who started it. Returns the claim outcome.

    "claimed" means this call ran it (however it ended); "held" that another attempt
    holds it; "done" that there was nothing to run.
    """
    outcome = _claim(job_id, owner, worker, lease or lease_seconds())
    if outcome != "claimed":
        return outcome
    beat = _Heartbeat(job_id, owner) if heartbeat else None
    if beat:
        beat.start()
    try:
        _run(MCPJob.objects.select_related("user").get(pk=job_id), owner)
    finally:
        if beat:
            beat.stop()
    return outcome


def _run(job: MCPJob, owner: str) -> None:
    from celery.exceptions import SoftTimeLimitExceeded

    from connect_labs.audit_trail.context import audit_context
    from connect_labs.labs.integrations.ocs.ocs_tokens import mcp_caller

    from .server import policy_denial
    from .visit_access import restricted_job

    job_id = str(job.pk)
    spec = get_tool(job.tool_name)
    user = job.user
    if spec is None:
        _finish(job_id, owner, MCPJob.FAILED, error_code="NOT_FOUND", error=f"Unknown tool: {job.tool_name}")
        return
    if not user.is_active:
        _finish(job_id, owner, MCPJob.FAILED, error_code="PERMISSION_DENIED", error="This account is not active.")
        return
    # The same context a tool call runs in (server._run_registry_tool): data access is
    # attributed to the person, pipelines read Open Chat Studio as them, and the call's
    # "no user visit data" answer holds for the handler's own checks (caller_restricted).
    with (
        audit_context(user=user, source="mcp", request_id=f"mcp-job:{job_id}", path=f"mcp:{job.tool_name}"),
        mcp_caller(user),
        restricted_job(job.restricted),
    ):
        denied = policy_denial(job.tool_name, job.arguments, user, job.restricted)
        if denied:
            _finish(job_id, owner, MCPJob.FAILED, error_code="PERMISSION_DENIED", error=denied)
            return
        kwargs = dict(job.arguments)
        if spec.wants_progress:
            kwargs["progress"] = _reporter(job_id, owner)
        try:
            result = spec.handler(user=user, **kwargs)
        except MCPToolError as e:
            _finish(job_id, owner, MCPJob.FAILED, error_code=e.code, error=e.message)
            return
        except SoftTimeLimitExceeded:
            _finish(
                job_id,
                owner,
                MCPJob.FAILED,
                error_code="UPSTREAM_ERROR",
                error="Ran past the time limit for a job on the worker, so it was stopped.",
            )
            return
        except Exception as e:  # noqa: BLE001
            logger.exception("MCP job %s (%s) failed", job_id, job.tool_name)
            _finish(job_id, owner, MCPJob.FAILED, error_code="UPSTREAM_ERROR", error=f"{type(e).__name__}: {e}")
            return
    if isinstance(result, dict):
        result = {k: v for k, v in result.items() if not k.startswith("_")}
    _finish(job_id, owner, MCPJob.SUCCEEDED, result=result if isinstance(result, dict) else {"value": result})


def release_held_by(worker: str) -> list[str]:
    """Hand back every job this worker holds, so the copy redelivered after a restart takes it at once.

    The attempt is not counted: a deploy is not the job's fault.
    """
    if not worker:
        return []
    ids = [
        str(pk)
        for pk in MCPJob.objects.filter(state=MCPJob.RUNNING, worker=worker, runner=MCPJob.WORKER).values_list(
            "pk", flat=True
        )
    ]
    if ids:
        MCPJob.objects.filter(pk__in=ids, state=MCPJob.RUNNING).update(
            state=MCPJob.QUEUED, lease_owner="", lease_until=None, attempts=F("attempts") - 1
        )
    return ids


# ---------------------------------------------------------------------------
# Standalone (a Fargate task of its own)
# ---------------------------------------------------------------------------


def launch_standalone(job_id: str, ecs=None) -> str | None:
    """Start the job's own Fargate task. Returns the ARN, or None if it was launched already.

    Idempotent per job (a redelivered message must not start a second machine): the
    launch is claimed on the row first.
    """
    if not MCPJob.objects.filter(pk=job_id, task_arn="", state=MCPJob.QUEUED).update(task_arn="launching"):
        return None
    try:
        arn = ecs_tasks.run_task(
            KIND,
            job_id,
            cpu=str(_setting("MCP_JOB_STANDALONE_CPU", "4096")),
            memory=str(_setting("MCP_JOB_STANDALONE_MEMORY", "16384")),
            ecs=ecs or ecs_tasks.client(),
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("could not start a standalone task for MCP job %s", job_id)
        MCPJob.objects.filter(pk=job_id).update(
            task_arn="",
            state=MCPJob.FAILED,
            error_code="UPSTREAM_ERROR",
            error=f"Could not start a machine for the job: {exc}",
            finished_at=timezone.now(),
        )
        return None
    MCPJob.objects.filter(pk=job_id).update(task_arn=arn)
    logger.info("MCP job %s launched as %s", job_id, arn)
    return arn


def _exit(code: int) -> None:  # replaced in tests
    os._exit(code)


def run_standalone(job_id: str) -> str:
    """The standalone task's body (``manage.py run_mcp_job``): run the job, bounded by a wall clock."""
    seconds = standalone_max_runtime_seconds()

    def on_limit(signum, frame):
        logger.error("%s: job %s ran %ss", KIND.overran_log, job_id, seconds)
        try:
            fail(job_id, f"Ran over its {seconds // 3600}h limit on its own machine, so it was stopped.")
        finally:
            _exit(124)

    previous = ecs_tasks.arm_runtime_limit(seconds, on_limit)
    try:
        # No heartbeat: nothing redelivers a standalone job, so the lease only has to
        # outlast the machine.
        return execute(
            job_id,
            owner=f"standalone:{socket.gethostname()}:{os.getpid()}",
            worker="standalone",
            heartbeat=False,
            lease=seconds + ecs_tasks.REAP_GRACE_SECONDS,
        )
    finally:
        ecs_tasks.disarm_runtime_limit(previous)


# ---------------------------------------------------------------------------
# Cancelling, failing, housekeeping
# ---------------------------------------------------------------------------


def fail(job_id: str, message: str, code: str = "UPSTREAM_ERROR") -> bool:
    """Record a live job failed, from outside the attempt running it (a reaper, a time limit)."""
    return bool(
        MCPJob.objects.filter(pk=job_id, state__in=MCPJob.LIVE_STATES).update(
            state=MCPJob.FAILED,
            error_code=code,
            error=message,
            finished_at=timezone.now(),
            lease_owner="",
            lease_until=None,
        )
    )


def cancel(job: MCPJob) -> MCPJob:
    """Stop a job: record it cancelled now, and stop whatever is running it.

    Recorded first, so the attempt running it can no longer record anything else
    (``_finish`` needs the job still running) and any later copy of its message ends
    at ``_claim``. Then the work itself is stopped: the worker's process is terminated
    (a Celery revoke), or the standalone task is stopped. Progress is never used to
    stop work (``connect_labs.labs.progress``: telemetry, not control flow).
    """
    MCPJob.objects.filter(pk=job.pk, state__in=MCPJob.LIVE_STATES).update(
        state=MCPJob.CANCELLED,
        cancel_requested=True,
        finished_at=timezone.now(),
        lease_owner="",
        lease_until=None,
    )
    job.refresh_from_db()
    if job.runner == MCPJob.STANDALONE and job.task_arn.startswith("arn:"):
        try:
            ecs_tasks.stop_task(job.task_arn, "long-running job cancelled", ecs_tasks.client())
        except Exception:  # noqa: BLE001 -- recorded cancelled; the reaper stops an orphan
            logger.exception("could not stop the task of MCP job %s", job.pk)
    elif not _setting("MCP_JOBS_RUN_INLINE", False):
        try:
            from config import celery_app

            celery_app.control.revoke(str(job.pk), terminate=True)
        except Exception:  # noqa: BLE001
            logger.exception("could not revoke MCP job %s", job.pk)
    return job


def housekeeping(now=None, ecs=None) -> dict[str, Any]:
    """Beat: fail jobs that were lost, reap standalone tasks, prune old jobs."""
    now = now or timezone.now()
    out: dict[str, Any] = {}
    # A worker job whose lease lapsed long ago and that nobody took over: its message
    # was lost with Redis, or its worker was killed and redelivery never came.
    stale = now - timedelta(seconds=int(_setting("MCP_JOB_LOST_AFTER_SECONDS", 2 * 3600)))
    lost = list(
        MCPJob.objects.filter(state=MCPJob.RUNNING, runner=MCPJob.WORKER, lease_until__lt=stale).values_list(
            "pk", flat=True
        )
    )
    never = list(MCPJob.objects.filter(state=MCPJob.QUEUED, created_at__lt=stale).values_list("pk", flat=True))
    for pk in lost:
        fail(str(pk), "Its worker stopped and the job was not picked up again. Start it again.")
    for pk in never:
        fail(str(pk), "It waited too long for a worker and never started. Start it again.")
    out["lost"] = [str(pk) for pk in lost + never]
    if standalone_enabled():
        out.update(
            ecs_tasks.reap(
                KIND,
                max_runtime_seconds=standalone_max_runtime_seconds(),
                is_live=lambda job_id: MCPJob.objects.filter(pk=job_id, state__in=MCPJob.LIVE_STATES).exists(),
                fail=lambda job_id, message: fail(job_id, message),
                ecs=ecs or ecs_tasks.client(),
                now=now,
            )
        )
    cutoff = now - timedelta(days=int(_setting("MCP_JOB_RETENTION_DAYS", 7)))
    out["pruned"] = MCPJob.objects.filter(state__in=MCPJob.TERMINAL_STATES, finished_at__lt=cutoff).delete()[0]
    return out
