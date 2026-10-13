"""Run each synthetic job on a machine of its own: a standalone ECS Fargate task.

Why. The worker is a SERVICE, and every labs deploy replaces it. A deploy stops the
old worker with SIGTERM and ECS kills it 30 seconds later, so a profile or clone that
takes ten minutes or an hour never survived one -- and labs deploys several times a
day. #2265 made the loss visible (the job is marked failed and you start it again);
this removes it. A standalone task is not part of any service, so a deploy does not
touch it: it runs until its command exits.

It also stops synthetic work competing for the worker's one vCPU with audits and AI
reviews, and lets a job use as much machine as it needs. A task is billed per second
while it runs and costs nothing once it exits, so the capacity is there for the job
and gone after it -- nothing to scale back down.

How. ``holds_a_synthetic_slot`` (tasks.py) asks :func:`should_launch`. When it says
yes, the Celery task does not run the job: it stores the job's arguments here, starts
a Fargate task from the worker's CURRENT task definition with the command
``python manage.py run_synthetic_job <job id>``, and returns. That command
(:func:`run_job`) runs the very same task body and writes progress and the result to
the result backend under the SAME job id, so ``synthetic_job_status`` and every MCP
tool that waits on a job read it unchanged.

Off unless ``SYNTHETIC_JOBS_ON_ECS`` is set (the labs worker task definition sets it),
so local runs and tests keep running jobs on the worker as before.
"""

from __future__ import annotations

import logging
import os
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

# Set on the standalone task (a container override), so the job body runs here
# instead of launching yet another task.
RUNNER_ENV = "SYNTHETIC_ECS_RUNNER"
STARTED_BY = "synthetic-job"
# The job's arguments wait here until the task picks them up. A day is generous: a
# task starts within a minute or two.
_THREAD_ENV = ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "NUMEXPR_NUM_THREADS")
_PAYLOAD_TTL = 24 * 3600
_LAUNCH_TTL = 3 * 24 * 3600


def _payload_key(job_id: str) -> str:
    return f"synthetic:ecs-job:{job_id}"


def _launched_key(job_id: str) -> str:
    return f"synthetic:ecs-launched:{job_id}"


def enabled() -> bool:
    return bool(getattr(settings, "SYNTHETIC_JOBS_ON_ECS", False))


def in_runner() -> bool:
    return os.environ.get(RUNNER_ENV) == "1"


def should_launch() -> bool:
    """Whether a synthetic Celery task should hand its job to a task of its own."""
    return enabled() and not in_runner()


def _ecs():
    import boto3

    return boto3.client("ecs", region_name=getattr(settings, "SYNTHETIC_ECS_REGION", "us-east-1"))


def _cluster() -> str:
    return getattr(settings, "SYNTHETIC_ECS_CLUSTER", "labs-jj-cluster")


def running_count(client=None) -> int:
    """Synthetic tasks running or starting now, across the cluster."""
    client = client or _ecs()
    arns: list[str] = []
    for status in ("RUNNING", "PENDING"):
        resp = client.list_tasks(cluster=_cluster(), startedBy=STARTED_BY, desiredStatus=status)
        arns.extend(resp.get("taskArns") or [])
    return len(set(arns))


def at_capacity(client=None) -> bool:
    return running_count(client) >= int(getattr(settings, "SYNTHETIC_ECS_MAX_TASKS", 6))


def launch(task_name: str, job_id: str, kwargs: dict[str, Any], client=None) -> str | None:
    """Start the job on a task of its own. Returns the task ARN, or None if it was already launched.

    Idempotent per job id: Redis can redeliver a Celery message (a deploy restores the
    worker's unacknowledged ones), and a second launch would run the job twice.
    """
    from django.core.cache import cache

    if not cache.add(_launched_key(job_id), "launching", _LAUNCH_TTL):
        logger.warning("synthetic job %s was already launched; not launching it again", job_id)
        return None
    cache.set(_payload_key(job_id), {"task": task_name, "kwargs": kwargs}, _PAYLOAD_TTL)
    client = client or _ecs()
    try:
        service = client.describe_services(
            cluster=_cluster(), services=[getattr(settings, "SYNTHETIC_ECS_SERVICE", "labs-jj-worker")]
        )["services"][0]
        # The worker's CURRENT task definition: the same image and environment the
        # worker runs, so the job runs the code that is deployed right now.
        task_definition = service["taskDefinition"]
        container = client.describe_task_definition(taskDefinition=task_definition)["taskDefinition"][
            "containerDefinitions"
        ][0]["name"]
        resp = client.run_task(
            cluster=_cluster(),
            launchType="FARGATE",
            taskDefinition=task_definition,
            count=1,
            startedBy=STARTED_BY,
            networkConfiguration=service["networkConfiguration"],
            overrides={
                "cpu": str(getattr(settings, "SYNTHETIC_ECS_CPU", "8192")),
                "memory": str(getattr(settings, "SYNTHETIC_ECS_MEMORY", "32768")),
                "containerOverrides": [
                    {
                        "name": container,
                        "command": ["python", "manage.py", "run_synthetic_job", job_id],
                        "environment": [
                            {"name": RUNNER_ENV, "value": "1"},
                            # One math-library thread per process: the job forks a
                            # process per vCPU, and each NumPy/BLAS pool sized to the
                            # whole machine oversubscribed it (the first parallel
                            # profile ran several times slower per opportunity).
                            *({"name": n, "value": "1"} for n in _THREAD_ENV),
                        ],
                    }
                ],
            },
        )
    except Exception:
        # Not launched: let a retry try again.
        cache.delete(_launched_key(job_id))
        raise
    failures = resp.get("failures") or []
    tasks = resp.get("tasks") or []
    if failures or not tasks:
        cache.delete(_launched_key(job_id))
        raise RuntimeError(f"ECS could not start the synthetic job: {failures}")
    arn = tasks[0]["taskArn"]
    cache.set(_launched_key(job_id), arn, _LAUNCH_TTL)
    logger.info("synthetic job %s (%s) launched as %s", job_id, task_name, arn)
    return arn


def task_arn(job_id: str) -> str | None:
    from django.core.cache import cache

    value = cache.get(_launched_key(job_id))
    return value if value and value != "launching" else None


def stop(job_id: str, client=None) -> bool:
    """Stop the job's task, if it has one. Returns whether a task was stopped."""
    arn = task_arn(job_id)
    if not arn:
        return False
    (client or _ecs()).stop_task(cluster=_cluster(), task=arn, reason="synthetic job cancelled")
    return True


class _Request:
    def __init__(self, job_id: str):
        self.id = job_id


def run_job(job_id: str) -> Any:
    """Run a launched job here, recording progress and the result under its own id.

    The task body is the Celery task's own function, bound to the task, with the job
    id pushed as its request -- so ``self.update_state`` and ``self.request.id`` behave
    exactly as they do on the worker.
    """
    from django.core.cache import cache

    from config import celery_app

    payload = cache.get(_payload_key(job_id))
    if not payload:
        raise RuntimeError(f"No arguments stored for synthetic job {job_id}; it may have expired.")
    # A bare `manage.py` process has not autodiscovered the task modules (only a
    # Celery worker does), so the registry does not know the task until its module
    # is imported -- the first job run here died on a KeyError for exactly that.
    import importlib

    try:
        importlib.import_module(payload["task"].rsplit(".", 1)[0])
    except ImportError:
        pass  # not a module path; the lookup below says what is missing
    task = celery_app.tasks[payload["task"]]
    # The task's container sets this already; set it for the body's duration either
    # way, so the body runs here rather than launching another task.
    previous = os.environ.get(RUNNER_ENV)
    os.environ[RUNNER_ENV] = "1"
    task.push_request(id=job_id)
    try:
        task.update_state(
            task_id=job_id,
            state="PROGRESS",
            meta={"current": 0, "total": 1, "message": "Running on its own machine"},
        )
        result = task.run(**payload["kwargs"])
    except BaseException as exc:
        celery_app.backend.store_result(job_id, exc, "FAILURE")
        logger.exception("synthetic job %s failed", job_id)
        raise
    finally:
        task.pop_request()
        if previous is None:
            os.environ.pop(RUNNER_ENV, None)
        else:
            os.environ[RUNNER_ENV] = previous
    celery_app.backend.store_result(job_id, result, "SUCCESS")
    cache.delete(_payload_key(job_id))
    logger.info("synthetic job %s finished", job_id)
    return result
