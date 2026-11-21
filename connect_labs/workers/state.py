"""A Connect Worker's lifecycle stage and facets on one opportunity, derived from the profile.

Stage, first match wins (design § B.3): suspended, invited (not accepted),
not_started, learning, ready_to_claim, at_cap, delivering (active within
WORKERS_ACTIVE_DAYS), inactive.

Facets computed here: learn, verification, payment, tasks. Performance (workflow
grading), supply and messaging belong to the apps that own them and are merged in
by them; this module leaves their keys alone when it rewrites the rest.

A change of stage, or a facet crossing a threshold (tasks going overdue, the
assessment being passed), writes a ``state_changed`` event.
"""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from django.conf import settings
from django.utils import timezone

from . import events as ev
from .connect_facade.shapes import parse_ts
from .models import Confidence, Stage, WorkerEvent, WorkerMembership, WorkerSnapshot, WorkerState

NOT_ACCEPTED = {"invited", "sms_delivered", "sms_not_delivered", "not_found"}
OWNED_FACETS = ("learn", "verification", "payment", "tasks")
# Facets whose crossing is worth a timeline line: (facet, field, predicate on the value).
THRESHOLDS = (
    ("tasks", "overdue", lambda v: bool(v)),
    ("learn", "assessment_passed", lambda v: bool(v)),
)


def active_days() -> int:
    return int(getattr(settings, "WORKERS_ACTIVE_DAYS", 14))


def _snapshots(membership: WorkerMembership, kind: str):
    return WorkerSnapshot.objects.filter(
        worker=membership.worker, opportunity_id=membership.opportunity_id, kind=kind, gone_at__isnull=True
    ).values_list("data", flat=True)


def stage_for(membership: WorkerMembership, *, works=None, now=None) -> str:
    now = now or timezone.now()
    if membership.suspended:
        return Stage.SUSPENDED
    if (membership.user_invite_status or "") in NOT_ACCEPTED and not membership.date_learn_started:
        return Stage.INVITED
    if not membership.date_learn_started:
        return Stage.NOT_STARTED
    if not membership.completed_learn_date:
        return Stage.LEARNING
    if not membership.date_claimed:
        return Stage.READY_TO_CLAIM
    if _at_cap(membership, works if works is not None else list(_snapshots(membership, "completed_works"))):
        return Stage.AT_CAP
    if membership.last_active and membership.last_active >= now - timedelta(days=active_days()):
        return Stage.DELIVERING
    return Stage.INACTIVE


def _at_cap(membership: WorkerMembership, works: list[dict]) -> bool:
    """Every payment unit the worker has a claim limit on has reached it."""
    limits = [c for c in (membership.claim_limits or []) if c.get("max_visits")]
    if not limits:
        return False
    used = Counter()
    for w in works:
        if (w.get("status") or "") in ("rejected", "incomplete"):
            continue
        used[str(w.get("payment_unit_id"))] += 1
    return all(used[str(c.get("payment_unit"))] >= int(c["max_visits"]) for c in limits)


def facets_for(membership: WorkerMembership, *, now=None) -> dict:
    now = now or timezone.now()
    today = now.date()
    from .models import OpportunitySync

    sync = OpportunitySync.objects.filter(opportunity_id=membership.opportunity_id).first()
    modules_total = len(((sync.meta if sync else {}) or {}).get("modules") or {})

    modules = list(_snapshots(membership, "completed_module"))
    assessments = list(_snapshots(membership, "assessment"))
    learn = {
        "modules_done": len({str(m.get("module")) for m in modules}),
        "modules_total": modules_total or None,
        "assessment_passed": any(a.get("passed") for a in assessments),
        "best_score": max((a.get("score") or 0 for a in assessments), default=None),
        "completed": bool(membership.completed_learn_date),
    }

    works = list(_snapshots(membership, "completed_works"))
    by_status = Counter((w.get("status") or "unknown") for w in works)
    total = sum(by_status.values())
    flagged = WorkerEvent.objects.filter(
        worker=membership.worker, opportunity_id=membership.opportunity_id, kind=ev.VISIT, payload__flagged=True
    ).count()
    verification = {
        "work_by_status": dict(by_status),
        "rejected_share": round(by_status["rejected"] / total, 3) if total else None,
        "pending_share": round(by_status["pending"] / total, 3) if total else None,
        "flagged_visits": flagged,
    }

    payments = list(_snapshots(membership, "payment"))
    paid_dates = [parse_ts(p.get("date_paid")) for p in payments]
    paid_dates = [d for d in paid_dates if d]
    payment = {
        "accrued": membership.payment_accrued,
        "paid_total": sum((p.get("amount") or 0) for p in payments),
        "payments": len(payments),
        "last_paid": max(paid_dates).isoformat() if paid_dates else None,
    }

    tasks = list(_snapshots(membership, "assigned_tasks"))
    open_tasks = [t for t in tasks if (t.get("status") or "") != "completed"]
    overdue = [t for t in open_tasks if (parse_ts(t.get("due_date")) or now).date() < today]
    task_facet = {
        "open": len(open_tasks),
        "overdue": len(overdue),
        "completed": sum(1 for t in tasks if (t.get("status") or "") == "completed"),
        "ocs_open": sum(1 for t in open_tasks if t.get("mode") == "ocs"),
    }
    return {"learn": learn, "verification": verification, "payment": payment, "tasks": task_facet}


def recompute(membership: WorkerMembership, *, now=None) -> tuple[WorkerState, list]:
    """Recompute stage + owned facets; returns (state, unsaved state_changed events)."""
    now = now or timezone.now()
    works = list(_snapshots(membership, "completed_works"))
    stage = stage_for(membership, works=works, now=now)
    facets = facets_for(membership, now=now)
    state = WorkerState.objects.filter(membership=membership).first()
    changes = []
    if state is None:
        state = WorkerState(membership=membership, stage=stage, facets=facets, computed_at=now)
        state.save()
        changes.append({"stage_from": None, "stage_to": stage})
    else:
        if state.stage != stage:
            changes.append({"stage_from": state.stage, "stage_to": stage})
        for facet, field, crosses in THRESHOLDS:
            before = crosses(((state.facets or {}).get(facet) or {}).get(field))
            after = crosses((facets.get(facet) or {}).get(field))
            if before != after:
                changes.append({"facet": f"{facet}.{field}", "from": before, "to": after})
        merged = {k: v for k, v in (state.facets or {}).items() if k not in OWNED_FACETS}
        merged.update(facets)
        state.stage, state.facets, state.computed_at = stage, merged, now
        state.save(update_fields=["stage", "facets", "computed_at"])

    out = []
    for change in changes:
        out.append(
            ev.build(
                membership.worker,
                ev.STATE_CHANGED,
                source="labs",
                dedupe_key=ev.key(
                    "labs",
                    membership.opportunity_id,
                    ev.STATE_CHANGED,
                    membership.worker.username,
                    change.get("facet") or "stage",
                    change.get("stage_to", change.get("to")),
                    now.isoformat(),
                ),
                opportunity_id=membership.opportunity_id,
                program_id=membership.program_id,
                occurred_at=now,
                confidence=Confidence.OBSERVED,
                payload=change,
            )
        )
    return state, out
