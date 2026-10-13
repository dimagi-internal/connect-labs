"""Synthetic PROFILING jobs per person: one job per request, and a record of them.

Every tool that queues a profiling job goes through ``admit`` first and ``record``
once it has a task id:

* **Identical request already running -> the same job back.** A retried or repeated
  call (agents retry) returns the running job's ``task_id`` instead of queueing a
  second copy of the same work.
* **The record** behind the synthetic jobs page (``recent_jobs``).

There are no per-person caps. There were three -- jobs in flight, opportunities a
day, opportunities per call -- because profiling ran on the Celery worker, one small
box shared with audits, AI reviews and beat. Since #2498 each job runs on a Fargate
task of its own, and the cluster-wide ``SYNTHETIC_ECS_MAX_TASKS`` bounds how many
run at once; the caps only stopped people re-running work a deploy had killed (a
cohort of 12, tried three times, met the 40-a-day cap the same afternoon).
"""

from __future__ import annotations

import json
import time
from datetime import UTC, datetime

from django.core.cache import cache

#: A job that has not reported a terminal state after this long no longer holds a slot.
_MAX_AGE_SECONDS = 3 * 3600
#: Celery states that mean the job is still queued or running.
_LIVE_STATES = frozenset({"PENDING", "RECEIVED", "STARTED", "PROGRESS", "RETRY"})


def _inflight_key(user) -> str:
    return f"synth:profile:inflight:{user.pk}"


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
    """Before anything is queued: the ``task_id`` of an identical job this person already
    has running (the caller hands that back instead of queueing), else ``None``.

    ``opportunity_ids`` and ``restricted`` are no longer limits (see the module doc);
    they stay in the signature so every caller keeps working unchanged.
    """
    if user is None or not getattr(user, "is_authenticated", False):
        return None
    signature = _signature(kind, args)
    for job in _live_jobs(user):
        if job.get("signature") == signature:
            return job["task_id"]
    return None


def record(user, task_id: str, *, kind: str, opportunity_ids: list[int], args: dict) -> None:
    """Record a newly queued job, so a repeat of it is handed back and the jobs page lists it."""
    if user is None or not getattr(user, "is_authenticated", False):
        return
    jobs = _live_jobs(user)
    jobs.append({"task_id": task_id, "signature": _signature(kind, args), "at": time.time()})
    cache.set(_inflight_key(user), jobs, _MAX_AGE_SECONDS)


def recent_jobs(user) -> list[dict]:
    """This person's synthetic jobs from the last few hours, newest first, each with its state now.

    The same records ``admit`` checks for a repeat, read for a page: what was asked (kind and
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
