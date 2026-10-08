"""Celery task that runs one queued PmcModelRun on the shared on-demand EMOD worker."""

from __future__ import annotations

import logging
import threading
import uuid

from canopy_sdk.ondemand import Lease
from django.conf import settings

from config import celery_app
from connect_labs.labs.indicators.emod.runner import default_bucket, default_instance, execute
from connect_labs.labs.indicators.models import PmcModelRun

logger = logging.getLogger(__name__)

CAPABILITY = "emod"
LEASE_TTL_S = 1800
LEASE_WAIT_S = 900
# A "running" row untouched this long is assumed to belong to a dead worker (run timeout is 2400 s).
RECLAIM_STALE_AFTER_S = 3000
# Refresh well inside the TTL so one missed beat does not lose the lease.
HEARTBEAT_S = LEASE_TTL_S / 3


def _redis():
    """Redis client for the lease. Lease requires decoded (str) responses."""
    import redis

    return redis.Redis.from_url(
        getattr(settings, "CELERY_BROKER_URL", None) or settings.REDIS_URL, decode_responses=True
    )


def _instance():
    return default_instance()


def _fail(run: PmcModelRun, message: str) -> None:
    """Record a failure only on a row this task still owns (never clobber a finished or running one)."""
    PmcModelRun.objects.filter(pk=run.pk, status__in=[PmcModelRun.QUEUED, PmcModelRun.FAILED]).update(
        status=PmcModelRun.FAILED, error=message
    )


def _heartbeat(
    lease, owner: str, stop: threading.Event, interval_s: float, lost: threading.Event | None = None
) -> None:
    while not stop.wait(interval_s):
        try:
            if not lease.refresh(owner):
                logger.error("EMOD lease lost while a run was in progress; another run may share the instance")
                if lost is not None:
                    lost.set()
                return
        except Exception:  # noqa: BLE001 - a Redis blip must not kill the run
            logger.exception("EMOD lease refresh failed")


@celery_app.task()
def run_pmc_model(run_id: int) -> None:
    """Run PmcModelRun ``run_id`` once the single EMOD instance is free.

    The instance runs one job at a time, so a Redis lease serialises runs across workers. Lease
    TTL stays 1800 s (a crashed worker frees the instance within half an hour) but the run can
    take up to 2400 s, so a heartbeat thread refreshes the lease while the run executes.
    """
    run = PmcModelRun.objects.get(pk=run_id)
    try:
        instance, bucket = _instance(), default_bucket()
    except RuntimeError as exc:
        _fail(run, f"live model unavailable: {exc}")
        return
    lease = Lease(_redis(), CAPABILITY, ttl_s=LEASE_TTL_S)
    owner = f"pmc-run-{run_id}-{uuid.uuid4().hex[:8]}"
    if not lease.acquire_wait(owner, timeout_s=LEASE_WAIT_S):
        _fail(run, f"runner busy: another model run held the worker for over {LEASE_WAIT_S // 60} minutes; try again")
        return
    stop, lost = threading.Event(), threading.Event()
    beat = threading.Thread(target=_heartbeat, args=(lease, owner, stop, HEARTBEAT_S, lost), daemon=True)
    beat.start()
    try:
        run.refresh_from_db()
        execute(run, instance, bucket, reclaim_stale_after_s=RECLAIM_STALE_AFTER_S)
        if lost.is_set():
            run.refresh_from_db()
            run.timings = {**(run.timings or {}), "lease_lost": True}
            run.save(update_fields=["timings"])
    finally:
        stop.set()
        beat.join(timeout=5)
        lease.release(owner)
