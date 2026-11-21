"""Writing a Connect Worker's timeline: one idempotent door for every writer.

Every event has a ``dedupe_key`` that names the fact, not the moment it was
written, so re-running a sync (or a messaging hook) never duplicates a line.
Keys are ``<source>:<opportunity or ->:<kind>:<what>``.

``confidence``: ``exact`` when the source gave the time it happened; ``observed``
when labs noticed a difference between two pulls (Connect keeps no history, so a
suspension's lifting, a task's due-date change or a deleted payment are observed).
"""

from __future__ import annotations

from django.db import IntegrityError, transaction

from .models import Confidence, ConnectWorker, Visibility, WorkerEvent

# Connect activity (from the export, by the sync)
INVITED = "invited"
LEARN_STARTED = "learn_started"
MODULE_COMPLETED = "module_completed"
ASSESSMENT = "assessment"
LEARN_COMPLETED = "learn_completed"
CLAIMED = "claimed"
VISIT = "visit"
VISIT_REVIEWED = "visit_reviewed"
TASK_ASSIGNED = "task_assigned"
TASK_COMPLETED = "task_completed"
TASK_DUE_CHANGED = "task_due_changed"
SUSPENDED = "suspended"
UNSUSPENDED = "unsuspended"
PAYMENT = "payment"
WORK_AREA_ASSIGNED = "work_area_assigned"
AUDIT_RESULT = "audit_result"
PROFILE_CHANGED = "profile_changed"
# Derived by labs
STATE_CHANGED = "state_changed"
# OCS coaching sessions linked to a task
OCS_SESSION = "ocs_session"
# Messaging (written by connect_labs/messaging, not here)
MESSAGE_IN = "message_in"
MESSAGE_OUT = "message_out"

KIND_LABELS = {
    INVITED: "Invited",
    LEARN_STARTED: "Started learning",
    MODULE_COMPLETED: "Completed a learn module",
    ASSESSMENT: "Took the assessment",
    LEARN_COMPLETED: "Completed learning",
    CLAIMED: "Claimed the opportunity",
    VISIT: "Visit",
    VISIT_REVIEWED: "Visit reviewed",
    TASK_ASSIGNED: "Task assigned",
    TASK_COMPLETED: "Task completed",
    TASK_DUE_CHANGED: "Task due date changed",
    SUSPENDED: "Suspended",
    UNSUSPENDED: "Suspension lifted",
    PAYMENT: "Payment",
    WORK_AREA_ASSIGNED: "Work area assigned",
    AUDIT_RESULT: "Audit result",
    PROFILE_CHANGED: "Details changed",
    STATE_CHANGED: "Stage changed",
    OCS_SESSION: "Coaching conversation",
    MESSAGE_IN: "Message from the worker",
    MESSAGE_OUT: "Message to the worker",
}

SOURCES = ("connect", "labs", "ocs", "personalid")


def key(source: str, opportunity_id, kind: str, *what) -> str:
    """A dedupe key naming the fact. Long parts are kept within the column."""
    tail = ":".join("" if w is None else str(w) for w in what)
    out = f"{source}:{opportunity_id if opportunity_id is not None else '-'}:{kind}:{tail}"
    if len(out) > 200:
        import hashlib

        out = out[:150] + ":" + hashlib.sha256(out.encode()).hexdigest()[:40]
    return out


def build(
    worker: ConnectWorker,
    kind: str,
    *,
    source: str,
    dedupe_key: str,
    opportunity_id: int | None = None,
    program_id: int | None = None,
    occurred_at=None,
    confidence: str = Confidence.EXACT,
    visibility: str = Visibility.NORMAL,
    ref: dict | None = None,
    payload: dict | None = None,
) -> WorkerEvent:
    """An unsaved event (for `bulk_record`)."""
    return WorkerEvent(
        worker=worker,
        kind=kind,
        source=source,
        dedupe_key=dedupe_key,
        opportunity_id=opportunity_id,
        program_id=program_id,
        occurred_at=occurred_at,
        confidence=confidence,
        visibility=visibility,
        ref=ref or {},
        payload=payload or {},
    )


def record_event(worker: ConnectWorker, kind: str, **fields) -> tuple[WorkerEvent, bool]:
    """Write one event unless its dedupe_key is already there. Returns (event, created).

    The door L2/L3 use too (e.g. ``message_in`` / ``message_out`` with
    ``source="labs"`` and ``ref={"thread": ..., "message": ...}``).
    """
    event = build(worker, kind, **fields)
    try:
        with transaction.atomic():
            event.save()
        return event, True
    except IntegrityError:
        return WorkerEvent.objects.get(dedupe_key=event.dedupe_key), False


def bulk_record(events: list[WorkerEvent], batch_size: int = 1000) -> int:
    """Write many events, skipping any whose dedupe_key exists. Returns how many were new."""
    if not events:
        return 0
    seen: set[str] = set()
    unique = []
    for e in events:
        if e.dedupe_key not in seen:
            seen.add(e.dedupe_key)
            unique.append(e)
    existing = set()
    keys = [e.dedupe_key for e in unique]
    for start in range(0, len(keys), batch_size):
        existing.update(
            WorkerEvent.objects.filter(dedupe_key__in=keys[start : start + batch_size]).values_list(
                "dedupe_key", flat=True
            )
        )
    new = [e for e in unique if e.dedupe_key not in existing]
    WorkerEvent.objects.bulk_create(new, batch_size=batch_size, ignore_conflicts=True)
    return len(new)
