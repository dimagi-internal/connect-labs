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

How a machine turns off. Only by its command exiting: a standalone task has no
lifetime of its own, and the Celery time limits on these tasks do not apply here (the
worker's pool enforces them, and this runs the task body directly). So two things
bound it. :func:`run_job` arms a wall-clock limit (``SYNTHETIC_ECS_MAX_RUNTIME_SECONDS``)
that records the job failed and exits the process, which stops the task. And
:func:`reap`, on beat, stops any task that outlives that limit anyway and fails any job
whose task stopped without saying how it ended (killed for memory, an image that would
not pull, a task stopped by hand) -- otherwise the job reads "in progress" until its
result expires, and a repeat of the same request is handed the dead job back.
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
# Set on the task (a container override) to the vCPUs it was given, so the job forks
# one process per vCPU it actually has.
VCPUS_ENV = "SYNTHETIC_ECS_VCPUS"
# The reaper's margin past the runtime limit: the job's own limit fires first.
_REAP_GRACE_SECONDS = 15 * 60
# Searched for by the labs-jj-synthetic-job-overran alarm (infra/labs-monitoring.yml).
OVERRAN_LOG = "synthetic job overran"
# Celery states that mean a job has not ended.
_LIVE_STATES = frozenset({"PENDING", "RECEIVED", "STARTED", "PROGRESS", "RETRY"})

# Fargate sizes, smallest first, as (cpu units, memory MiB): 4 GB per vCPU, the share
# each forked process had on the original 8 vCPU / 32 GB machine.
_SMALL = ("2048", "8192")
_MEDIUM = ("4096", "16384")
# Worker tools that touch one opportunity (run_synthetic_tool's tool_name).
_SINGLE_OPP_TOOLS = frozenset(
    {
        "synthetic_generate_from_manifest",
        "synthetic_generate_opp",
        "synthetic_fidelity_vs_source",
    }
)


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


def max_runtime_seconds() -> int:
    return int(getattr(settings, "SYNTHETIC_ECS_MAX_RUNTIME_SECONDS", 6 * 3600))


def _opportunity_count(task_name: str, kwargs: dict[str, Any]) -> int | None:
    """How many opportunities a job works on, or None when its arguments do not say."""
    args = kwargs
    if task_name.endswith(".run_synthetic_tool"):
        if kwargs.get("tool_name") in _SINGLE_OPP_TOOLS:
            return 1
        args = kwargs.get("arguments") or {}
    for key in ("source_opportunity_ids", "opportunity_ids"):
        if isinstance(args.get(key), list | tuple):
            return len(args[key])
    for key in ("source_opportunity_id", "opportunity_id"):
        if args.get(key) is not None:
            return 1
    if args.get("spec_yaml"):
        from connect_labs.labs.synthetic.cohort import CohortSpec

        try:
            return len(CohortSpec.from_yaml(args["spec_yaml"]).opportunity_ids)
        except Exception:  # noqa: BLE001 -- the job reports a bad spec itself
            return None
    return None


def machine_size(task_name: str, kwargs: dict[str, Any]) -> tuple[str, str]:
    """The (cpu, memory) a job gets: as many vCPUs as it can use, up to the large size.

    One opportunity runs serially, so it gets 2 vCPU; a handful gets 4; a cohort, or
    a job whose size its arguments do not show, gets the large size (8 vCPU / 32 GB).
    """
    large = (
        str(getattr(settings, "SYNTHETIC_ECS_CPU", "8192")),
        str(getattr(settings, "SYNTHETIC_ECS_MEMORY", "32768")),
    )
    n = _opportunity_count(task_name, kwargs)
    if n is None:
        size = large
    elif n == 1:
        size = _SMALL
    elif n <= 4:
        size = _MEDIUM
    else:
        size = large
    # Never larger than the configured large size.
    return min(size, large, key=lambda s: int(s[0]))


def vcpus() -> int:
    """vCPUs this task was given (one forked process each), else the large size's."""
    raw = os.environ.get(VCPUS_ENV) or str(int(str(getattr(settings, "SYNTHETIC_ECS_CPU", "1024"))) // 1024)
    try:
        return max(1, int(raw))
    except ValueError:
        return 1


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
    cpu, memory = machine_size(task_name, kwargs)
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
                "cpu": cpu,
                "memory": memory,
                "containerOverrides": [
                    {
                        "name": container,
                        "command": ["python", "manage.py", "run_synthetic_job", job_id],
                        "environment": [
                            {"name": RUNNER_ENV, "value": "1"},
                            {"name": VCPUS_ENV, "value": str(int(cpu) // 1024)},
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
    logger.info("synthetic job %s (%s) launched as %s (cpu %s, memory %s)", job_id, task_name, arn, cpu, memory)
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


def _exit(code: int) -> None:  # replaced in tests
    os._exit(code)


def _arm_runtime_limit(job_id: str, seconds: int):
    """After ``seconds``, record the job failed and end this process, which stops the task.

    A SIGALRM rather than a thread: the job forks, and forking a multi-threaded
    process is unsafe. ``os._exit``, not an exception: an exception would wait in
    ``ProcessPoolExecutor.__exit__`` for the very children that are not finishing.
    This process is the container's PID 1, so its exit takes the children with it.
    """
    import signal

    from config import celery_app

    def _on_limit(signum, frame):
        message = f"Ran over its {seconds // 3600}h limit on its own machine, so it was stopped. Start it again."
        logger.error("%s: job %s ran %ss", OVERRAN_LOG, job_id, seconds)
        try:
            celery_app.backend.store_result(job_id, RuntimeError(message), "FAILURE")
        finally:
            _exit(124)

    previous = signal.signal(signal.SIGALRM, _on_limit)
    signal.alarm(seconds)
    return previous


def run_job(job_id: str) -> Any:
    """Run a launched job here, recording progress and the result under its own id.

    The task body is the Celery task's own function, bound to the task, with the job
    id pushed as its request -- so ``self.update_state`` and ``self.request.id`` behave
    exactly as they do on the worker. Every way it can fail here is recorded as the
    job's FAILURE (the first job launched died on a lookup that was not, and read
    "starting" until someone noticed), and it stops at ``max_runtime_seconds()``.
    """
    import importlib
    import signal

    from django.core.cache import cache

    from config import celery_app

    previous_handler = _arm_runtime_limit(job_id, max_runtime_seconds())
    previous = os.environ.get(RUNNER_ENV)
    # The task's container sets this already; set it for the body's duration either
    # way, so the body runs here rather than launching another task.
    os.environ[RUNNER_ENV] = "1"
    task = None
    try:
        payload = cache.get(_payload_key(job_id))
        if not payload:
            raise RuntimeError(f"No arguments stored for synthetic job {job_id}; it may have expired.")
        # A bare `manage.py` process has not autodiscovered the task modules (only a
        # Celery worker does), so the registry does not know the task until its module
        # is imported -- the first job run here died on a KeyError for exactly that.
        try:
            importlib.import_module(payload["task"].rsplit(".", 1)[0])
        except ImportError:
            pass  # not a module path; the lookup below says what is missing
        task = celery_app.tasks[payload["task"]]
        task.push_request(id=job_id)
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
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
        if task is not None:
            task.pop_request()
        # The arguments carry the person's OAuth token: gone once the job has ended,
        # however it ended.
        cache.delete(_payload_key(job_id))
        if previous is None:
            os.environ.pop(RUNNER_ENV, None)
        else:
            os.environ[RUNNER_ENV] = previous
    celery_app.backend.store_result(job_id, result, "SUCCESS")
    logger.info("synthetic job %s finished", job_id)
    return result


def _job_id_of(task: dict) -> str | None:
    """The job id a synthetic task runs: the last word of its command override."""
    for override in (task.get("overrides") or {}).get("containerOverrides") or []:
        command = override.get("command") or []
        if len(command) >= 2 and command[-2] == "run_synthetic_job":
            return command[-1]
    return None


def _job_state(job_id: str) -> str:
    from celery.result import AsyncResult

    from config import celery_app

    return AsyncResult(job_id, app=celery_app).state


def _describe(client, status: str) -> list[dict]:
    arns = client.list_tasks(cluster=_cluster(), startedBy=STARTED_BY, desiredStatus=status).get("taskArns") or []
    tasks: list[dict] = []
    for i in range(0, len(arns), 100):  # DescribeTasks takes at most 100
        tasks.extend(client.describe_tasks(cluster=_cluster(), tasks=arns[i : i + 100]).get("tasks") or [])
    return tasks


def _stopped_reason(task: dict) -> str:
    parts = [task.get("stoppedReason") or task.get("stopCode") or "stopped"]
    for container in task.get("containers") or []:
        if container.get("reason"):
            parts.append(container["reason"])
        if container.get("exitCode") is not None:
            parts.append(f"exit code {container['exitCode']}")
    return "; ".join(parts)


def reap(client=None, now=None) -> dict[str, list[str]]:
    """Stop synthetic tasks past the runtime limit; fail jobs whose task ended unrecorded.

    The backstop to :func:`run_job`'s own limit, for a process that cannot act on it.
    Stopped tasks stay listed for about an hour, so a tick every ten minutes sees each.
    """
    from datetime import UTC, datetime

    from config import celery_app

    client = client or _ecs()
    now = now or datetime.now(UTC)
    limit = max_runtime_seconds() + _REAP_GRACE_SECONDS
    stopped_overrun: list[str] = []
    failed: list[str] = []
    for task in _describe(client, "RUNNING"):
        began = task.get("startedAt") or task.get("createdAt")
        if not began or (now - began).total_seconds() <= limit:
            continue
        job_id = _job_id_of(task) or "?"
        logger.error("%s: job %s on %s, running since %s; stopping it", OVERRAN_LOG, job_id, task["taskArn"], began)
        client.stop_task(cluster=_cluster(), task=task["taskArn"], reason="synthetic job overran its runtime limit")
        if job_id != "?" and _job_state(job_id) in _LIVE_STATES:
            message = f"Ran over its {max_runtime_seconds() // 3600}h limit on its own machine, so it was stopped."
            celery_app.backend.store_result(job_id, RuntimeError(message), "FAILURE")
        stopped_overrun.append(task["taskArn"])
    for task in _describe(client, "STOPPED"):
        job_id = _job_id_of(task)
        if not job_id or _job_state(job_id) not in _LIVE_STATES:
            continue
        reason = _stopped_reason(task)
        logger.warning("synthetic job %s: its task stopped without a result (%s); marking it failed", job_id, reason)
        message = f"Its machine stopped before it finished ({reason}). Start it again."
        celery_app.backend.store_result(job_id, RuntimeError(message), "FAILURE")
        failed.append(job_id)
    return {"stopped_overrun": stopped_overrun, "failed": failed}
