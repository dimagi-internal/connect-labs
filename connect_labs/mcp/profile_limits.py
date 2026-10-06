"""Per-user limits on synthetic PROFILING, so one person cannot exhaust connect-labs.

Profiling reads every visit of a real opportunity (full form JSON) into memory on the
Celery worker, which is one small box that also runs audits, AI reviews and beat. The
write limiter (``rate_limit.enforce_write_limit``) never sees it: the profile tools are
reads. So every tool that queues a profiling job goes through ``admit`` first and
``record`` once it has a task id:

* **Identical request already running → the same job back.** A retried or repeated call
  (agents retry) returns the running job's ``task_id`` instead of queueing a second
  copy of the same work.
* **Jobs in flight per person** (``SYNTHETIC_PROFILE_MAX_INFLIGHT_PER_USER``, default 2).
  Liveness is read from Celery, so a finished or failed job frees its slot at once; a
  job that never reports is dropped after ``_MAX_AGE_SECONDS``.
* **Opportunities profiled per person per UTC day**
  (``SYNTHETIC_PROFILE_DAILY_OPPS_PER_USER``, default 40).
* **Opportunities per call without access to user visit data**
  (``SYNTHETIC_PROFILE_MAX_OPPS_PER_CALL_RESTRICTED``, default 10): a bulk or cohort call
  is one task that profiles its opportunities one after another, holding a worker slot
  for all of them.

Counts live in Django's cache and are windowed, not transactional -- like the write
limiter, a safety cap that can be off by one under contention, not an exact quota.
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from django.conf import settings
from django.core.cache import cache

from .tool_registry import MCPToolError

#: A job that has not reported a terminal state after this long no longer holds a slot.
_MAX_AGE_SECONDS = 3 * 3600
#: Celery states that mean the job is still queued or running.
_LIVE_STATES = frozenset({"PENDING", "RECEIVED", "STARTED", "PROGRESS", "RETRY"})


def _setting(name: str, default: int) -> int:
    return int(getattr(settings, name, default))


def max_inflight() -> int:
    return _setting("SYNTHETIC_PROFILE_MAX_INFLIGHT_PER_USER", 2)


def daily_opps() -> int:
    return _setting("SYNTHETIC_PROFILE_DAILY_OPPS_PER_USER", 40)


def max_opps_per_call_restricted() -> int:
    return _setting("SYNTHETIC_PROFILE_MAX_OPPS_PER_CALL_RESTRICTED", 10)


def _inflight_key(user) -> str:
    return f"synth:profile:inflight:{user.pk}"


def _daily_key(user) -> str:
    return f"synth:profile:daily:{user.pk}:{datetime.now(UTC):%Y%m%d}"


def _signature(kind: str, args: dict) -> str:
    return json.dumps({"kind": kind, **args}, sort_keys=True, default=str)


def _state(task_id: str) -> str:
    from celery.result import AsyncResult

    from config import celery_app

    return AsyncResult(task_id, app=celery_app).state


def _live_jobs(user) -> list[dict]:
    """This person's profiling jobs that are still queued or running."""
    now = time.time()
    live = []
    for job in cache.get(_inflight_key(user)) or []:
        if now - job.get("at", 0) > _MAX_AGE_SECONDS:
            continue
        if _state(job["task_id"]) in _LIVE_STATES:
            live.append(job)
    return live


def admit(user, *, kind: str, opportunity_ids: list[int], args: dict, restricted: bool) -> str | None:
    """Check a profiling request against the limits before anything is queued.

    Returns the ``task_id`` of an identical job this person already has running (the
    caller hands that back instead of queueing), ``None`` when a new job may start, and
    raises ``RATE_LIMITED`` with what to do next when it may not.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    signature = _signature(kind, args)
    live = _live_jobs(user)
    for job in live:
        if job.get("signature") == signature:
            return job["task_id"]

    count = len(opportunity_ids)
    per_call = max_opps_per_call_restricted()
    if restricted and count > per_call:
        raise MCPToolError(
            "RATE_LIMITED",
            f"This call asks to profile {count} opportunities; without access to user visit data "
            f"one call may profile at most {per_call}. Split it into calls of {per_call} or fewer.",
        )
    cap = max_inflight()
    if len(live) >= cap:
        ids = ", ".join(job["task_id"] for job in live)
        raise MCPToolError(
            "RATE_LIMITED",
            f"You already have {len(live)} profiling job(s) running ({ids}); the limit is {cap} at "
            "a time. Check them with synthetic_profile_status -- a new one can start when one finishes.",
        )
    used = cache.get(_daily_key(user), 0)
    daily = daily_opps()
    if used + count > daily:
        raise MCPToolError(
            "RATE_LIMITED",
            f"Daily profiling limit reached: {used} of {daily} opportunities profiled today "
            f"(this call adds {count}). The count resets at 00:00 UTC.",
        )
    return None


def record(user, task_id: str, *, kind: str, opportunity_ids: list[int], args: dict) -> None:
    """Count a newly queued job against this person's limits. Call before enqueueing it."""
    if user is None or not getattr(user, "is_authenticated", False):
        return
    jobs = _live_jobs(user)
    jobs.append({"task_id": task_id, "signature": _signature(kind, args), "at": time.time()})
    cache.set(_inflight_key(user), jobs, _MAX_AGE_SECONDS)
    key = _daily_key(user)
    if not cache.add(key, len(opportunity_ids), 2 * 86400):
        try:
            cache.incr(key, len(opportunity_ids))
        except ValueError:
            cache.set(key, len(opportunity_ids), 2 * 86400)


def recent_jobs(user) -> list[dict]:
    """This person's synthetic jobs from the last few hours, newest first, each with its state now.

    The same records the limits count, read for a page: what was asked (kind and
    opportunities), when, and where it stands in Celery.
    """
    now = time.time()
    rows = []
    for job in cache.get(_inflight_key(user)) or []:
        if now - job.get("at", 0) > _MAX_AGE_SECONDS:
            continue
        asked = json.loads(job.get("signature") or "{}")
        rows.append(
            {
                "task_id": job["task_id"],
                "kind": asked.get("kind", ""),
                "opportunity_ids": asked.get("opportunity_ids") or [],
                "started": datetime.fromtimestamp(job.get("at", now), UTC),
                "state": _state(job["task_id"]),
            }
        )
    return sorted(rows, key=lambda r: r["started"], reverse=True)


def is_live(state: str) -> bool:
    return state in _LIVE_STATES
