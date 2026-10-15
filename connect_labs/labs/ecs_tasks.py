"""Run a job on a machine of its own: a standalone ECS Fargate task, which a deploy does not stop.

This is the generic half of what synthetic jobs started (``labs/synthetic/ecs_runner.py``,
#2498) and long-running MCP jobs now share (``mcp/jobs.py``, ``job_runner="standalone"``).
It is for EXTREMELY long work only -- an hour or more, or work that needs a bigger machine
than the worker's. Ordinary async work runs on the worker service through the Celery
queue, which survives a deploy by redelivery (``mcp/jobs.py`` says how).

Why a task of its own. The worker is a SERVICE and every labs deploy replaces it, several
times a day. A standalone task belongs to no service, so a deploy does not touch it: it
runs until its command exits, from the worker's CURRENT task definition (same image and
environment), billed per second and gone when it exits.

What each kind of job supplies (:class:`Kind`): the ``startedBy`` tag its tasks carry (so
one kind's reaper never touches another's), the management command the task runs with the
job id as its last argument, the environment variable that tells the body it is already on
its own machine, and the phrase its overrun is logged with (an alarm's metric filter
matches it -- ``infra/labs-synthetic-jobs.yml``).

How a machine turns off. Only by its command exiting, so two things bound it. The job arms
a wall-clock limit (:func:`arm_runtime_limit`) that records it failed and exits the
process. And :func:`reap`, on beat, stops any task that outlives its limit anyway and fails
any job whose task stopped without recording how it ended -- otherwise the job reads "in
progress" until it expires.

Cluster, worker service and region are the ``SYNTHETIC_ECS_*`` settings: they name the
infrastructure, which every kind shares, and keeping their names kept the deployed
environment unchanged.
"""

from __future__ import annotations

import logging
import os
import signal
from collections.abc import Callable, Iterable
from dataclasses import dataclass

from django.conf import settings

logger = logging.getLogger(__name__)

# One math-library thread per process: a job that forks a process per vCPU, with each
# NumPy/BLAS pool sized to the whole machine, oversubscribed it.
THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
# The reaper's margin past the runtime limit: the job's own limit fires first.
REAP_GRACE_SECONDS = 15 * 60
# Logged (with the kind's label) when a machine stopped before its job recorded an
# ending. Searched for by the labs-jj-synthetic-job-machine-died alarm.
MACHINE_DIED_LOG = "its task stopped without a result"


@dataclass(frozen=True)
class Kind:
    """One kind of standalone job."""

    #: ECS ``startedBy`` on every task of this kind (at most 36 characters).
    started_by: str
    #: ``python manage.py <command> <job id>`` is the task's command.
    command: str
    #: Set to "1" on the task, so the body runs there instead of launching another.
    runner_env: str
    #: What the logs call one of these ("synthetic job").
    label: str
    #: The line an overrun is logged with; an alarm's metric filter matches it.
    overran_log: str


def client():
    import boto3

    return boto3.client("ecs", region_name=getattr(settings, "SYNTHETIC_ECS_REGION", "us-east-1"))


def cluster() -> str:
    return getattr(settings, "SYNTHETIC_ECS_CLUSTER", "labs-jj-cluster")


def in_runner(kind: Kind) -> bool:
    return os.environ.get(kind.runner_env) == "1"


def running_count(kind: Kind, ecs) -> int:
    """Tasks of this kind running or starting now, across the cluster."""
    arns: list[str] = []
    for status in ("RUNNING", "PENDING"):
        resp = ecs.list_tasks(cluster=cluster(), startedBy=kind.started_by, desiredStatus=status)
        arns.extend(resp.get("taskArns") or [])
    return len(set(arns))


def run_task(kind: Kind, job_id: str, *, cpu: str, memory: str, ecs, extra_env: Iterable[dict] = ()) -> str:
    """Start ``manage.py <kind.command> <job_id>`` on a task of its own. Returns the task ARN.

    Raises if ECS refuses: the caller decides whether that is a retry or a failure.
    """
    service = ecs.describe_services(
        cluster=cluster(), services=[getattr(settings, "SYNTHETIC_ECS_SERVICE", "labs-jj-worker")]
    )["services"][0]
    # The worker's CURRENT task definition: the same image and environment the worker
    # runs, so the job runs the code that is deployed right now.
    task_definition = service["taskDefinition"]
    container = ecs.describe_task_definition(taskDefinition=task_definition)["taskDefinition"]["containerDefinitions"][
        0
    ]["name"]
    resp = ecs.run_task(
        cluster=cluster(),
        launchType="FARGATE",
        taskDefinition=task_definition,
        count=1,
        startedBy=kind.started_by,
        networkConfiguration=service["networkConfiguration"],
        overrides={
            "cpu": str(cpu),
            "memory": str(memory),
            "containerOverrides": [
                {
                    "name": container,
                    "command": ["python", "manage.py", kind.command, job_id],
                    "environment": [
                        {"name": kind.runner_env, "value": "1"},
                        *extra_env,
                        *({"name": n, "value": "1"} for n in THREAD_ENV),
                    ],
                }
            ],
        },
    )
    failures = resp.get("failures") or []
    tasks = resp.get("tasks") or []
    if failures or not tasks:
        raise RuntimeError(f"ECS could not start the {kind.label}: {failures}")
    return tasks[0]["taskArn"]


def stop_task(arn: str, reason: str, ecs) -> None:
    ecs.stop_task(cluster=cluster(), task=arn, reason=reason[:255])


def arm_runtime_limit(seconds: int, on_limit: Callable[[int, object], None]):
    """Call ``on_limit`` after ``seconds`` of wall clock. Returns the previous SIGALRM handler.

    A SIGALRM rather than a thread: a job may fork, and forking a multi-threaded process
    is unsafe. ``on_limit`` should end the process with ``os._exit`` -- an exception
    would wait in ``ProcessPoolExecutor.__exit__`` for the very children that are not
    finishing; the process is the container's PID 1, so its exit takes them with it.
    """
    previous = signal.signal(signal.SIGALRM, on_limit)
    signal.alarm(seconds)
    return previous


def disarm_runtime_limit(previous) -> None:
    signal.alarm(0)
    signal.signal(signal.SIGALRM, previous)


def job_id_of(kind: Kind, task: dict) -> str | None:
    """The job id a task of this kind runs: the last word of its command override."""
    for override in (task.get("overrides") or {}).get("containerOverrides") or []:
        command = override.get("command") or []
        if len(command) >= 2 and command[-2] == kind.command:
            return command[-1]
    return None


def describe(kind: Kind, status: str, ecs) -> list[dict]:
    arns = ecs.list_tasks(cluster=cluster(), startedBy=kind.started_by, desiredStatus=status).get("taskArns") or []
    tasks: list[dict] = []
    for i in range(0, len(arns), 100):  # DescribeTasks takes at most 100
        tasks.extend(ecs.describe_tasks(cluster=cluster(), tasks=arns[i : i + 100]).get("tasks") or [])
    return tasks


def stopped_reason(task: dict) -> str:
    parts = [task.get("stoppedReason") or task.get("stopCode") or "stopped"]
    for container in task.get("containers") or []:
        if container.get("reason"):
            parts.append(container["reason"])
        if container.get("exitCode") is not None:
            parts.append(f"exit code {container['exitCode']}")
    return "; ".join(parts)


def reap(
    kind: Kind,
    *,
    max_runtime_seconds: int,
    is_live: Callable[[str], bool],
    fail: Callable[[str, str], None],
    ecs,
    now,
) -> dict[str, list[str]]:
    """Stop this kind's tasks past the runtime limit; fail jobs whose task ended unrecorded.

    ``is_live(job_id)`` says whether a job has not yet recorded an ending; ``fail(job_id,
    message)`` records it failed. Stopped tasks stay listed for about an hour, so a tick
    every ten minutes sees each.
    """
    limit = max_runtime_seconds + REAP_GRACE_SECONDS
    stopped_overrun: list[str] = []
    failed: list[str] = []
    for task in describe(kind, "RUNNING", ecs):
        began = task.get("startedAt") or task.get("createdAt")
        if not began or (now - began).total_seconds() <= limit:
            continue
        job_id = job_id_of(kind, task) or "?"
        logger.error(
            "%s: job %s on %s, running since %s; stopping it", kind.overran_log, job_id, task["taskArn"], began
        )
        stop_task(task["taskArn"], f"{kind.label} overran its runtime limit", ecs)
        if job_id != "?" and is_live(job_id):
            fail(job_id, f"Ran over its {max_runtime_seconds // 3600}h limit on its own machine, so it was stopped.")
        stopped_overrun.append(task["taskArn"])
    for task in describe(kind, "STOPPED", ecs):
        job_id = job_id_of(kind, task)
        if not job_id or not is_live(job_id):
            continue
        reason = stopped_reason(task)
        logger.warning("%s %s: %s (%s); marking it failed", kind.label, job_id, MACHINE_DIED_LOG, reason)
        fail(job_id, f"Its machine stopped before it finished ({reason}). Start it again.")
        failed.append(job_id)
    return {"stopped_overrun": stopped_overrun, "failed": failed}
