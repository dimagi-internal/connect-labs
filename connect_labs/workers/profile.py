"""Assembling a Connect Worker's profile for a reader, within what that reader may see.

`build_profile(worker, view)` is the page's and `worker_get`'s answer: identity,
memberships with stage and facets, a timeline, facts, notes and an honest coverage
statement. Everything is filtered by a `access.WorkerView` before it leaves here.

`worker_brief_data(worker, program_id)` is the program-scoped, agent-safe subset the
messaging brief is built from: never a phone number, a suspension reason, a
restricted event or a private note, and nothing from another program.
"""

from __future__ import annotations

from collections import Counter

from django.db.models import Q

from . import events as ev
from .access import WorkerView
from .connect_facade.export import NO_EVENTS_NOTE, NO_NOTIFICATIONS_NOTE
from .models import (
    FactStatus,
    OpportunitySync,
    StaffNote,
    Visibility,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
    WorkerState,
)

TIMELINE_LIMIT = 200


def _iso(value):
    return value.isoformat() if value else None


def event_dict(e: WorkerEvent) -> dict:
    return {
        "id": e.pk,
        "kind": e.kind,
        "label": ev.KIND_LABELS.get(e.kind, e.kind.replace("_", " ")),
        "opportunity_id": e.opportunity_id,
        "program_id": e.program_id,
        "at": _iso(e.at),
        "occurred_at": _iso(e.occurred_at),
        "observed_at": _iso(e.observed_at),
        "source": e.source,
        "confidence": e.confidence,
        "visibility": e.visibility,
        "ref": e.ref,
        "payload": e.payload,
    }


def membership_dict(m: WorkerMembership, *, restricted: bool) -> dict:
    state = WorkerState.objects.filter(membership=m).first()
    out = {
        "opportunity_id": m.opportunity_id,
        "opportunity_name": m.opportunity_name,
        "program_id": m.program_id,
        "organization_slug": m.organization_slug,
        "invite_status": m.user_invite_status,
        "invited_date": _iso(m.invited_date),
        "learn_started": _iso(m.date_learn_started),
        "learn_completed": _iso(m.completed_learn_date),
        "claimed": _iso(m.date_claimed),
        "last_active": _iso(m.last_active),
        "payment_accrued": m.payment_accrued,
        "suspended": m.suspended,
        "suspension_date": _iso(m.suspension_date),
        "visible": m.visible,
        "last_synced_at": _iso(m.last_synced_at),
        "stage": state.stage if state else None,
        "facets": state.facets if state else {},
    }
    if restricted:
        out["suspension_reason"] = m.suspension_reason
    return out


def timeline_queryset(worker, *, opportunity_ids, program_ids=(), restricted=False, kinds=None, since=None):
    """Events the reader may see: in their opportunities, or worker-level (no opportunity) in their
    programs or global."""
    qs = WorkerEvent.objects.filter(worker=worker).filter(
        Q(opportunity_id__in=list(opportunity_ids))
        | Q(opportunity_id__isnull=True, program_id__in=list(program_ids))
        | Q(opportunity_id__isnull=True, program_id__isnull=True)
    )
    if not restricted:
        qs = qs.exclude(visibility=Visibility.RESTRICTED)
    if kinds:
        qs = qs.filter(kind__in=list(kinds))
    if since is not None:
        qs = qs.filter(Q(occurred_at__gte=since) | Q(occurred_at__isnull=True, observed_at__gte=since))
    return qs.order_by("-occurred_at", "-observed_at", "-id")


def coverage_for(worker, opportunity_ids) -> dict:
    """What this profile covers, in sentences a person can act on."""
    memberships = list(WorkerMembership.objects.filter(worker=worker, opportunity_id__in=list(opportunity_ids)))
    syncs = {s.opportunity_id: s for s in OpportunitySync.objects.filter(opportunity_id__in=list(opportunity_ids))}
    complete, stale, visits_partial = [], [], []
    for m in memberships:
        sync = syncs.get(m.opportunity_id)
        label = m.opportunity_name or f"opportunity {m.opportunity_id}"
        if not m.visible or sync is None or not sync.visible:
            stale.append(label)
            continue
        complete.append(label)
        visits = (sync.coverage or {}).get("visits_thin") or {}
        if visits and not visits.get("complete"):
            visits_partial.append(label)
    readable = OpportunitySync.objects.filter(visible=True).count()
    statements = []
    if complete:
        statements.append(f"Complete for {', '.join(sorted(complete))}, as of each one's last sync.")
    if stale:
        statements.append(f"No longer readable by labs, shown as last seen: {', '.join(sorted(stale))}.")
    if visits_partial:
        statements.append(
            f"Visits are recent only (labs holds no full visit cache) for: {', '.join(sorted(visits_partial))}."
        )
    statements.append(
        f"Labs reads {readable} opportunities; this worker's activity on any other opportunity is not here."
    )
    statements.append(NO_NOTIFICATIONS_NOTE)
    statements.append(NO_EVENTS_NOTE)
    return {
        "complete_for": sorted(complete),
        "not_readable": sorted(stale),
        "visits_recent_only": sorted(visits_partial),
        "readable_opportunities": readable,
        "statements": statements,
        "last_synced_at": _iso(worker.profile_synced_at),
    }


def facts_queryset(worker, program_ids, *, include_inactive=False):
    qs = WorkerFact.objects.filter(worker=worker).filter(
        Q(program_id__isnull=True) | Q(program_id__in=list(program_ids))
    )
    if not include_inactive:
        qs = qs.filter(status=FactStatus.ACTIVE)
    return qs.order_by("-created_at")


def fact_dict(f: WorkerFact) -> dict:
    return {
        "id": f.pk,
        "program_id": f.program_id,
        "kind": f.kind,
        "statement": f.statement,
        "status": f.status,
        "confidence": f.confidence,
        "asserted_by_kind": f.asserted_by_kind,
        "asserted_by": f.asserted_by,
        "created_at": _iso(f.created_at),
        "retracted_at": _iso(f.retracted_at),
        "retraction_reason": f.retraction_reason,
    }


def notes_queryset(worker, program_ids, *, private: bool):
    qs = StaffNote.objects.filter(worker=worker).filter(
        Q(program_id__isnull=True) | Q(program_id__in=list(program_ids))
    )
    if not private:
        qs = qs.filter(private=False)
    return qs


def note_dict(n: StaffNote) -> dict:
    return {
        "id": n.pk,
        "program_id": n.program_id,
        "body": n.body,
        "private": n.private,
        "author": n.author,
        "created_at": _iso(n.created_at),
    }


def build_profile(worker, view: WorkerView, *, kinds=None, opportunity_id=None, since=None, limit=TIMELINE_LIMIT):
    """The whole profile a reader may see. ``opportunity_id`` narrows the timeline to one of theirs."""
    opp_ids = set(view.opportunity_ids)
    if opportunity_id is not None:
        timeline_opps = {int(opportunity_id)} & opp_ids
    else:
        timeline_opps = opp_ids
    memberships = WorkerMembership.objects.filter(worker=worker, opportunity_id__in=list(opp_ids)).order_by(
        "-last_active", "opportunity_id"
    )
    events_qs = timeline_queryset(
        worker,
        opportunity_ids=timeline_opps,
        program_ids=view.program_ids,
        restricted=view.restricted,
        kinds=kinds,
        since=since,
    )
    counts = Counter(events_qs.values_list("kind", flat=True))
    return {
        "worker": {
            "username": worker.username,
            "name": worker.name,
            "record_uri": worker.record_uri,
            "canopy_sub": worker.canopy_sub,
            "languages": worker.languages,
            "timezone": worker.timezone,
            "profile_synced_at": _iso(worker.profile_synced_at),
        },
        "memberships": [membership_dict(m, restricted=view.restricted) for m in memberships],
        "timeline": [event_dict(e) for e in events_qs[:limit]],
        "timeline_total": sum(counts.values()),
        "timeline_counts": dict(counts),
        "facts": [fact_dict(f) for f in facts_queryset(worker, view.program_ids, include_inactive=view.restricted)],
        "notes": [note_dict(n) for n in notes_queryset(worker, view.program_ids, private=view.private_notes)],
        "coverage": coverage_for(worker, opp_ids),
    }


def worker_brief_data(worker, program_id: int | None, *, timeline_limit: int = 30) -> dict:
    """The agent-safe, program-scoped data a Worker Brief is built from.

    Only the program's opportunities; no phone, suspension reason, restricted event or
    private note; facts active and global or of this program. ``program_id=None`` gives
    the worker-level part only (identity, global facts), never every program's history.
    """
    memberships = (
        list(WorkerMembership.objects.filter(worker=worker, program_id=program_id)) if program_id is not None else []
    )
    opp_ids = {m.opportunity_id for m in memberships}
    programs = {program_id} if program_id is not None else set()
    events_qs = timeline_queryset(worker, opportunity_ids=opp_ids, program_ids=programs, restricted=False)
    return {
        "worker": {
            "username": worker.username,
            "name": worker.name,
            "record_uri": worker.record_uri,
            "languages": worker.languages,
            "timezone": worker.timezone,
        },
        "program_id": program_id,
        "memberships": [membership_dict(m, restricted=False) for m in memberships],
        "recent_timeline": [event_dict(e) for e in events_qs[:timeline_limit]],
        "facts": [fact_dict(f) for f in facts_queryset(worker, programs)],
        "notes": [note_dict(n) for n in notes_queryset(worker, programs, private=False)],
        "coverage": coverage_for(worker, opp_ids)["statements"],
    }
