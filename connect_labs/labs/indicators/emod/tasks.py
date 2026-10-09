"""Celery task that runs one queued PmcModelRun on the shared on-demand EMOD worker.

Registered with the worker through ``connect_labs/labs/indicators/tasks.py`` (autodiscovery only
imports ``<app>.tasks``).

Liveness, so a run killed mid-flight (a deploy's cold shutdown, an OOM) does not leave the page
waiting for ever:

* While waiting for the instance and while running, the task touches the row's ``updated_at``
  every ``ROW_HEARTBEAT_S`` (60 s). A ``running`` row untouched for three beats is dead:
  ``service.submit_run`` re-queues it on the next request and the new task's claim takes it over.
* The Redis lease that serialises runs has a short TTL (``LEASE_TTL_S``, 300 s) refreshed by the
  same heartbeat, so a lease orphaned by a killed worker frees itself within five minutes.

Concurrency: the task runs on the shared Celery pool (``docker/start_celery``, one queue,
concurrency 6). Waiting for the lease is a sleep loop, cheap in CPU, but each waiting task holds a
pool slot for up to ``LEASE_WAIT_S``. ``service.PMC_MAX_IN_FLIGHT`` (3) bounds that: at most one
run executes and two wait, so at most three of the six slots are ever held by EMOD work.
"""

from __future__ import annotations

import logging
import threading
import time
import uuid

from canopy_sdk.ondemand import Lease
from django.conf import settings
from django.db import connection
from django.utils import timezone

from config import celery_app
from connect_labs.labs.indicators.emod.runner import (
    ROW_HEARTBEAT_S,
    STALE_RUNNING_AFTER_S,
    default_bucket,
    default_instance,
    execute,
)
from connect_labs.labs.indicators.models import PmcModelRun

logger = logging.getLogger(__name__)

CAPABILITY = "emod"
#: Short, so a lease orphaned by a cold-killed worker frees the instance within five minutes. The
#: heartbeat refreshes it every HEARTBEAT_S while the run is alive, five beats inside the TTL.
LEASE_TTL_S = 300
#: How long a task waits for another run to free the instance before failing its row.
LEASE_WAIT_S = 900
#: Seconds between non-blocking attempts at the lease while waiting; each attempt touches the row.
LEASE_POLL_S = 5
#: A "running" row untouched this long belongs to a dead worker.
RECLAIM_STALE_AFTER_S = STALE_RUNNING_AFTER_S
#: One beat refreshes the lease and touches the row.
HEARTBEAT_S = ROW_HEARTBEAT_S

COORDINATION_UNAVAILABLE = "live model unavailable: coordination store unreachable"


def _redis():
    """Redis client for the lease. Lease requires decoded (str) responses."""
    import redis

    return redis.Redis.from_url(
        getattr(settings, "CELERY_BROKER_URL", None) or settings.REDIS_URL, decode_responses=True
    )


def _redis_errors() -> tuple[type[BaseException], ...]:
    from redis.exceptions import RedisError

    return (RedisError, OSError)


def _instance():
    return default_instance()


def _fail(run: PmcModelRun, message: str) -> None:
    """Record a failure only on a row this task still owns (never clobber a finished or running one)."""
    PmcModelRun.objects.filter(pk=run.pk, status__in=[PmcModelRun.QUEUED, PmcModelRun.FAILED]).update(
        status=PmcModelRun.FAILED, error=message
    )


def _touch(run_id: int, statuses=(PmcModelRun.QUEUED, PmcModelRun.RUNNING)) -> None:
    """Mark the row alive. queryset.update() skips auto_now, so updated_at is set explicitly."""
    PmcModelRun.objects.filter(pk=run_id, status__in=list(statuses)).update(updated_at=timezone.now())


def _heartbeat(
    lease,
    owner: str,
    stop: threading.Event,
    interval_s: float,
    lost: threading.Event | None = None,
    run_id: int | None = None,
) -> None:
    """Every ``interval_s``: refresh the lease and touch the row, until ``stop``.

    Runs in its own thread, so it closes its own database connection when it ends.
    """
    try:
        while not stop.wait(interval_s):
            if run_id is not None:
                try:
                    _touch(run_id)
                except Exception:  # noqa: BLE001 - a database blip must not kill the run
                    logger.exception("EMOD run %s: could not touch the row", run_id)
            try:
                if not lease.refresh(owner):
                    logger.error("EMOD lease lost while a run was in progress; another run may share the instance")
                    if lost is not None:
                        lost.set()
                    return
            except Exception:  # noqa: BLE001 - a Redis blip must not kill the run
                logger.exception("EMOD lease refresh failed")
    finally:
        if run_id is not None:
            connection.close()


def _wait_for_lease(lease, owner: str, run_id: int) -> bool:
    """Take the lease, trying without blocking every LEASE_POLL_S for at most LEASE_WAIT_S.

    Each attempt touches the row if it is queued, so a request arriving meanwhile sees a live
    queued run rather than one whose enqueue was lost (which it would re-enqueue). A ``running`` row
    is NOT touched here: it belongs to the task holding the lease, and if that task died its row
    must age past STALE_RUNNING_AFTER_S so this one can reclaim it. Redis errors propagate.
    """
    deadline = time.monotonic() + LEASE_WAIT_S
    while True:
        if lease.acquire(owner):
            return True
        _touch(run_id, statuses=(PmcModelRun.QUEUED,))
        if time.monotonic() >= deadline:
            return False
        time.sleep(min(LEASE_POLL_S, max(0.0, deadline - time.monotonic())))


@celery_app.task()
def run_pmc_model(run_id: int) -> None:
    """Run PmcModelRun ``run_id`` once the single EMOD instance is free.

    The instance runs one job at a time, so a Redis lease serialises runs across workers. A task
    for a row that is already completed (a duplicate enqueue) does nothing.
    """
    run = PmcModelRun.objects.get(pk=run_id)
    if run.status == PmcModelRun.COMPLETED:
        return
    try:
        instance, bucket = _instance(), default_bucket()
    except RuntimeError as exc:
        _fail(run, f"live model unavailable: {exc}")
        return
    owner = f"pmc-run-{run_id}-{uuid.uuid4().hex[:8]}"
    try:
        lease = Lease(_redis(), CAPABILITY, ttl_s=LEASE_TTL_S)
        acquired = _wait_for_lease(lease, owner, run_id)
    except _redis_errors() as exc:
        logger.warning("EMOD run %s: lease store unreachable: %s", run_id, exc)
        _fail(run, COORDINATION_UNAVAILABLE)
        return
    if not acquired:
        _fail(run, f"runner busy: another model run held the worker for over {LEASE_WAIT_S // 60} minutes; try again")
        return
    stop, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(lease, owner, stop, HEARTBEAT_S, lost, run_id), daemon=True)
    beat.start()
    try:
        run.refresh_from_db()
        if run.status == PmcModelRun.COMPLETED:
            # A duplicate task that waited behind the one that finished it.
            return
        execute(run, instance, bucket, reclaim_stale_after_s=RECLAIM_STALE_AFTER_S)
        if lost.is_set():
            run.refresh_from_db()
            run.timings = {**(run.timings or {}), "lease_lost": True}
            run.save(update_fields=["timings"])
    finally:
        stop.set()
        beat.join(timeout=5)
        try:
            lease.release(owner)
        except _redis_errors():
            logger.exception("EMOD lease release failed; it expires within %s s", LEASE_TTL_S)


@celery_app.task
def sweep_dead_pmc_runs() -> int:
    """Heal every live run whose task died (``service.heal``): complete it from S3 when the instance
    finished it, else re-queue it. Every deploy hard-kills running tasks with no drain, so without this
    a run finished on the instance stays 'running' until someone polls or re-submits it."""
    from connect_labs.labs.indicators.emod import service

    healed = 0
    for run in PmcModelRun.objects.filter(status=PmcModelRun.RUNNING):
        if service.is_dead(run):
            service.heal(run)
            healed += 1
    return healed
