"""`connect_worker_sync`: keep labs' Connect Worker records current from Connect.

Three entry points:

* `refresh_opportunities(source)` -- which opportunities the sync identity can read
  (listed, plus probed supervising/funder ones). Their `OpportunitySync` rows with
  `visible=True` are every profile's coverage.
* `sync_opportunity(source, opp)` -- the cheap tier for one opportunity: the small
  exports in full, every worker upserted, events written from differences with the
  last pull, visits read thin from labs' caches, state recomputed.
* `sync_worker(username)` -- the same, narrowed to one worker, for the opportunities
  labs knows they are on (or, with ``discover``, every opportunity it can read).

Everything goes through the Connect facade (`connect_facade.get_source`), so when
Connect ships an endpoint only the facade changes.
"""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import timedelta

from django.db import transaction
from django.utils import timezone

from . import events as ev
from . import state as worker_state
from .connect_facade import (
    ASSESSMENT,
    ASSIGNED_TASKS,
    AUDIT_REPORT_ENTRIES,
    COMPLETED_MODULE,
    COMPLETED_WORKS,
    PAYMENT,
    USER_DATA,
    VISITS_THIN,
    WORK_AREAS,
    OpportunityRef,
)
from .connect_facade.shapes import parse_ts
from .models import Confidence, ConnectWorker, OpportunitySync, WorkerMembership, WorkerSnapshot

logger = logging.getLogger(__name__)

# The cheap tier: small rows, pulled whole each time and diffed.
SNAPSHOT_KINDS = (
    COMPLETED_MODULE,
    ASSESSMENT,
    COMPLETED_WORKS,
    PAYMENT,
    ASSIGNED_TASKS,
    AUDIT_REPORT_ENTRIES,
    WORK_AREAS,
)
# Never kept in a snapshot (the phone lives encrypted on ConnectWorker; the reason, encrypted on the membership).
PRIVATE_USER_FIELDS = ("phone", "suspension_reason")
MODULES_MAX_AGE = timedelta(hours=24)


def _hash(data: dict) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True, default=str).encode()).hexdigest()


def _date(value):
    parsed = parse_ts(value)
    return parsed.date() if parsed else None


# ---------------------------------------------------------------------- coverage


def probe_candidates() -> set[int]:
    """Opportunity ids labs already knows of, to ask Connect about beyond its listing (ask #7)."""
    from connect_labs.labs.analysis.backends.sql.models import RawVisitCache
    from connect_labs.labs.analysis.config import USER_VISITS_RAW_SLOT
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    ids = set(OpportunitySync.objects.values_list("opportunity_id", flat=True))
    ids |= set(WorkerMembership.objects.values_list("opportunity_id", flat=True).distinct())
    ids |= set(
        RawVisitCache.objects.filter(pipeline_id=USER_VISITS_RAW_SLOT)
        .values_list("opportunity_id", flat=True)
        .distinct()
    )
    ids -= set(SyntheticOpportunity.objects.filter(labs_only=True).values_list("opportunity_id", flat=True))
    return {i for i in ids if i and i > 0}


def refresh_opportunities(source, *, now=None) -> dict:
    now = now or timezone.now()
    found = source.accessible_opportunities(probe_ids=probe_candidates())
    seen: set[int] = set()
    for ref in found.opportunities:
        seen.add(ref.id)
        _upsert_opportunity(ref)
    gone = OpportunitySync.objects.filter(visible=True).exclude(opportunity_id__in=seen)
    gone_ids = list(gone.values_list("opportunity_id", flat=True))
    gone.update(visible=False)
    WorkerMembership.objects.filter(opportunity_id__in=gone_ids).update(visible=False)
    return {
        "visible": len(seen),
        "listed": sum(1 for r in found.opportunities if r.via == "listed"),
        "probed": sum(1 for r in found.opportunities if r.via == "probed"),
        "no_longer_visible": len(gone_ids),
        "coverage": found.coverage.as_dict(),
    }


def _upsert_opportunity(ref: OpportunityRef) -> OpportunitySync:
    opp, _ = OpportunitySync.objects.get_or_create(opportunity_id=ref.id)
    opp.name = ref.name or opp.name
    if ref.program_id is not None:
        opp.program_id = ref.program_id
    opp.organization_slug = ref.organization_slug or opp.organization_slug
    opp.is_active, opp.is_test, opp.via, opp.visible = ref.is_active, ref.is_test, ref.via, True
    opp.save()
    return opp


# ---------------------------------------------------------------------- one opportunity


def sync_opportunity(source, opp: OpportunitySync, *, usernames=None, now=None) -> dict:
    """Sync one opportunity (optionally narrowed to some workers). Records failure on the row and re-raises.

    Everything is read from Connect first, then written in one transaction, so no
    database transaction is held open across a network call.
    """
    now = now or timezone.now()
    usernames = set(usernames) if usernames else None
    opp.last_attempt_at = now
    try:
        pulled = _pull(source, opp, usernames=usernames, now=now)
        with transaction.atomic():
            report = _apply(opp, pulled, usernames=usernames, now=now)
    except Exception as exc:
        opp.consecutive_failures += 1
        opp.last_error = str(exc)[:2000]
        opp.save(update_fields=["last_attempt_at", "consecutive_failures", "last_error", "updated_at"])
        raise
    opp.last_synced_at = now
    opp.consecutive_failures = 0
    opp.last_error = ""
    opp.save()
    return report


def _pull(source, opp: OpportunitySync, *, usernames, now) -> dict:
    opp_id = opp.opportunity_id
    single = next(iter(usernames)) if usernames and len(usernames) == 1 else None
    _refresh_modules(source, opp, now)
    pulled = {USER_DATA: source.entities(opp_id, USER_DATA, username=single)}
    for kind in SNAPSHOT_KINDS:
        pulled[kind] = source.entities(opp_id, kind, username=single)
    pulled[VISITS_THIN] = source.entities(
        opp_id, VISITS_THIN, username=single, since=None if usernames else opp.visits_watermark
    )
    return pulled


def _apply(opp: OpportunitySync, pulled: dict, *, usernames, now) -> dict:
    opp_id = opp.opportunity_id
    new_events: list = []
    coverage = dict(opp.coverage or {})
    for kind, result in pulled.items():
        coverage[kind] = result.coverage.as_dict()

    workers: dict[str, ConnectWorker] = {}
    memberships: dict[str, WorkerMembership] = {}
    # A Connect Worker erased in labs stays erased: the sync never writes their record again.
    erased = set(
        ConnectWorker.objects.filter(
            username__in=[r.get("username") for r in pulled[USER_DATA].rows if r.get("username")],
            erased_at__isnull=False,
        ).values_list("username", flat=True)
    )
    for row in pulled[USER_DATA].rows:
        username = row.get("username")
        if not username or (usernames and username not in usernames) or username in erased:
            continue
        worker, membership, events = _apply_user_data(opp, row, now)
        workers[username], memberships[username] = worker, membership
        new_events.extend(events)

    for kind in SNAPSHOT_KINDS:
        new_events.extend(_diff_kind(opp, kind, pulled[kind].rows, workers, scoped=bool(usernames), now=now))

    visits = pulled[VISITS_THIN].rows
    new_events.extend(_visit_events(opp, visits, workers))
    if not usernames:
        stamps = [parse_ts(v.get("date_created")) for v in visits]
        stamps = [s for s in stamps if s]
        if stamps:
            opp.visits_watermark = max(stamps)

    written = ev.bulk_record(new_events)
    state_events = []
    for membership in memberships.values():
        _, changes = worker_state.recompute(membership, now=now)
        state_events.extend(changes)
    written += ev.bulk_record(state_events)

    opp.coverage = coverage
    return {"opportunity_id": opp_id, "workers": len(workers), "events": written}


def _refresh_modules(source, opp: OpportunitySync, now) -> None:
    meta = dict(opp.meta or {})
    fetched = parse_ts(meta.get("modules_at"))
    if fetched and now - fetched < MODULES_MAX_AGE:
        return
    result = source.learn_modules(opp.opportunity_id)
    if result.coverage.complete:
        meta["modules"] = {str(m.get("id")): m.get("name") or m.get("slug") or "" for m in result.rows}
        meta["modules_at"] = now.isoformat()
        opp.meta = meta


def _apply_user_data(opp: OpportunitySync, row: dict, now) -> tuple[ConnectWorker, WorkerMembership, list]:
    username = row["username"]
    worker, _ = ConnectWorker.objects.get_or_create(username=username)
    events = []
    changed_fields = []
    if row.get("name") and row["name"] != worker.name:
        if worker.name:
            events.append(
                ev.build(
                    worker,
                    ev.PROFILE_CHANGED,
                    source="connect",
                    dedupe_key=ev.key(
                        "connect", None, ev.PROFILE_CHANGED, username, "name", _hash({"n": row["name"]})
                    ),
                    occurred_at=now,
                    confidence=Confidence.OBSERVED,
                    payload={"field": "name", "from": worker.name, "to": row["name"]},
                )
            )
        worker.name = row["name"]
        changed_fields.append("name")
    if row.get("phone") and row["phone"] != worker.phone:
        if worker.phone:
            # The number itself is never in an event: phones are never shown to an agent.
            events.append(
                ev.build(
                    worker,
                    ev.PROFILE_CHANGED,
                    source="connect",
                    dedupe_key=ev.key(
                        "connect", None, ev.PROFILE_CHANGED, username, "phone", _hash({"p": row["phone"]})
                    ),
                    occurred_at=now,
                    confidence=Confidence.OBSERVED,
                    visibility="restricted",
                    payload={"field": "phone"},
                )
            )
        worker.phone = row["phone"]
        changed_fields.append("phone")
    worker.profile_synced_at = now
    worker.save()

    membership, created = WorkerMembership.objects.get_or_create(
        worker=worker, opportunity_id=opp.opportunity_id, defaults={"first_seen_at": now}
    )
    prev = dict(membership.snapshot or {})
    snapshot = {k: v for k, v in row.items() if k not in PRIVATE_USER_FIELDS}
    events.extend(_membership_events(worker, opp, prev, row, first=created or not prev, now=now))

    membership.opportunity_name = opp.name or membership.opportunity_name
    membership.program_id = opp.program_id if opp.program_id is not None else membership.program_id
    membership.organization_slug = opp.organization_slug or membership.organization_slug
    membership.user_invite_status = row.get("user_invite_status") or ""
    membership.invited_date = parse_ts(row.get("invited_date"))
    membership.date_learn_started = parse_ts(row.get("date_learn_started"))
    membership.completed_learn_date = parse_ts(row.get("completed_learn_date"))
    membership.date_claimed = _date(row.get("date_claimed"))
    membership.last_active = parse_ts(row.get("last_active"))
    membership.payment_accrued = int(row.get("payment_accrued") or 0)
    membership.suspended = bool(row.get("suspended"))
    membership.suspension_date = parse_ts(row.get("suspension_date"))
    membership.suspension_reason = row.get("suspension_reason") or ""
    membership.claim_limits = row.get("claim_limits") or []
    membership.snapshot = snapshot
    membership.snapshot_hash = _hash(snapshot)
    membership.last_synced_at = now
    membership.visible = True
    membership.save()
    return worker, membership, events


def _membership_events(worker, opp, prev: dict, row: dict, *, first: bool, now) -> list:
    """Events from one user_data row against the last one (Connect keeps no history of these)."""
    username, opp_id, program_id = worker.username, opp.opportunity_id, opp.program_id
    out = []

    def add(kind, *what, occurred=None, confidence=Confidence.EXACT, payload=None):
        out.append(
            ev.build(
                worker,
                kind,
                source="connect",
                dedupe_key=ev.key("connect", opp_id, kind, username, *what),
                opportunity_id=opp_id,
                program_id=program_id,
                occurred_at=occurred,
                confidence=confidence,
                payload=payload or {},
            )
        )

    # Milestones Connect dates itself: exact, written the first time labs sees them.
    for field, kind in (
        ("invited_date", ev.INVITED),
        ("date_learn_started", ev.LEARN_STARTED),
        ("completed_learn_date", ev.LEARN_COMPLETED),
        ("date_claimed", ev.CLAIMED),
    ):
        if row.get(field) and not prev.get(field):
            add(kind, occurred=parse_ts(row[field]))

    was, now_suspended = bool(prev.get("suspended")), bool(row.get("suspended"))
    if now_suspended and (first or not was):
        # The reason is restricted: it stays encrypted on the membership, never in an event.
        stamp = row.get("suspension_date")
        add(
            ev.SUSPENDED,
            stamp or now.date().isoformat(),
            occurred=parse_ts(stamp) if stamp else now,
            confidence=Confidence.EXACT if stamp else Confidence.OBSERVED,
        )
    elif was and not now_suspended:
        add(ev.UNSUSPENDED, prev.get("suspension_date") or "", occurred=now, confidence=Confidence.OBSERVED)
    return out


# ---------------------------------------------------------------------- snapshot kinds


def _diff_kind(opp: OpportunitySync, kind: str, rows: list[dict], workers: dict, *, scoped: bool, now) -> list:
    """Upsert snapshots for one kind and return the events their differences mean.

    Only rows for workers in this sync are processed. A snapshot that stops appearing
    is marked gone -- in a scoped (one-worker) sync, only that worker's snapshots.
    """
    opp_id = opp.opportunity_id
    # A work area moves between workers, so its snapshot is looked up whoever held it.
    narrow = scoped and kind != WORK_AREAS
    existing = {
        s.key: s
        for s in WorkerSnapshot.objects.filter(opportunity_id=opp_id, kind=kind).filter(
            **({"worker__in": list(workers.values())} if narrow else {})
        )
    }
    in_scope = {w.pk for w in workers.values()}
    out = []
    present: set[str] = set()
    to_create, to_update = [], []
    for row in rows:
        key = row.get("key")
        if not key:
            continue
        present.add(key)
        username = row.get("username") or ""
        worker = workers.get(username)
        prev = existing.get(key)
        if worker is None and not (kind == WORK_AREAS and prev is not None and prev.worker_id in in_scope):
            continue  # a worker this sync does not cover
        data = {k: v for k, v in row.items() if k != "key"}
        digest = _hash(data)
        prev_data = prev.data if prev is not None and prev.gone_at is None else None
        if worker is not None:
            out.extend(_kind_events(kind, opp, worker, prev_data, data, key, now))
        if prev is None:
            to_create.append(
                WorkerSnapshot(
                    worker=worker,
                    opportunity_id=opp_id,
                    kind=kind,
                    key=key,
                    data=data,
                    data_hash=digest,
                    first_seen_at=now,
                    last_seen_at=now,
                )
            )
        else:
            prev.worker, prev.data, prev.data_hash, prev.last_seen_at, prev.gone_at = worker, data, digest, now, None
            to_update.append(prev)
    WorkerSnapshot.objects.bulk_create(to_create, batch_size=1000, ignore_conflicts=True)
    WorkerSnapshot.objects.bulk_update(
        to_update, ["worker", "data", "data_hash", "last_seen_at", "gone_at"], batch_size=1000
    )

    vanished = [
        s
        for k, s in existing.items()
        if k not in present and s.gone_at is None and (not scoped or s.worker_id in in_scope)
    ]
    for snap in vanished:
        snap.gone_at = now
        if kind == PAYMENT and snap.worker_id:
            worker = next((w for w in workers.values() if w.pk == snap.worker_id), None) or snap.worker
            out.append(
                ev.build(
                    worker,
                    ev.PAYMENT,
                    source="connect",
                    dedupe_key=ev.key("connect", opp_id, "payment_removed", snap.key),
                    opportunity_id=opp_id,
                    program_id=opp.program_id,
                    occurred_at=now,
                    confidence=Confidence.OBSERVED,
                    payload={**_payment_payload(snap.data), "removed": True},
                )
            )
    WorkerSnapshot.objects.bulk_update(vanished, ["gone_at"], batch_size=1000)
    return out


def _payment_payload(data: dict) -> dict:
    return {
        "amount": data.get("amount"),
        "amount_usd": data.get("amount_usd"),
        "date_paid": data.get("date_paid"),
        "confirmed": data.get("confirmed"),
        "payment_unit": data.get("payment_unit"),
        "payment_method": data.get("payment_method"),
    }


def _kind_events(kind, opp, worker, prev: dict | None, row: dict, key: str, now) -> list:
    opp_id, program_id, username = opp.opportunity_id, opp.program_id, worker.username
    out = []

    def add(event_kind, *what, occurred=None, confidence=Confidence.EXACT, payload=None, ref=None):
        out.append(
            ev.build(
                worker,
                event_kind,
                source="connect",
                dedupe_key=ev.key("connect", opp_id, event_kind, *what),
                opportunity_id=opp_id,
                program_id=program_id,
                occurred_at=occurred,
                confidence=confidence,
                payload=payload or {},
                ref=ref or {"kind": kind, "key": key},
            )
        )

    if kind == COMPLETED_MODULE and prev is None:
        module = str(row.get("module"))
        add(
            ev.MODULE_COMPLETED,
            key,
            occurred=parse_ts(row.get("date")),
            payload={
                "module": row.get("module"),
                "module_name": ((opp.meta or {}).get("modules") or {}).get(module, ""),
                "duration": row.get("duration"),
            },
        )
    elif kind == ASSESSMENT and prev is None:
        add(
            ev.ASSESSMENT,
            key,
            occurred=parse_ts(row.get("date")),
            payload={k: row.get(k) for k in ("score", "passing_score", "passed")},
        )
    elif kind == PAYMENT and prev is None:
        add(
            ev.PAYMENT,
            key,
            occurred=parse_ts(row.get("date_paid") or row.get("created_at")),
            payload=_payment_payload(row),
        )
    elif kind == ASSIGNED_TASKS:
        summary = {k: row.get(k) for k in ("task_type_name", "task_type_slug", "mode", "due_date", "status")}
        ref = {"kind": kind, "key": key, "assigned_task_id": row.get("assigned_task_id")}
        if prev is None:
            add(ev.TASK_ASSIGNED, key, occurred=parse_ts(row.get("date_created")), payload=summary, ref=ref)
        if row.get("status") == "completed" and (prev is None or prev.get("status") != "completed"):
            stamp = parse_ts(row.get("completed_at"))
            add(
                ev.TASK_COMPLETED,
                key,
                occurred=stamp or now,
                confidence=Confidence.EXACT if stamp else Confidence.OBSERVED,
                payload=summary,
                ref=ref,
            )
        if prev is not None and prev.get("due_date") != row.get("due_date"):
            add(
                ev.TASK_DUE_CHANGED,
                key,
                row.get("due_date"),
                occurred=now,
                confidence=Confidence.OBSERVED,
                payload={**summary, "from": prev.get("due_date"), "to": row.get("due_date")},
                ref=ref,
            )
    elif kind == AUDIT_REPORT_ENTRIES:
        if prev is None or prev.get("review_action") != row.get("review_action"):
            add(
                ev.AUDIT_RESULT,
                key,
                row.get("review_action") or "",
                occurred=parse_ts(row.get("date_modified") or row.get("date_created")) if prev is None else now,
                confidence=Confidence.EXACT if prev is None else Confidence.OBSERVED,
                payload={k: row.get(k) for k in ("flagged", "reviewed", "review_action", "audit_report_uuid")},
            )
    elif kind == WORK_AREAS:
        if prev is None or prev.get("username") != username:
            add(
                ev.WORK_AREA_ASSIGNED,
                key,
                username,
                occurred=now,
                confidence=Confidence.OBSERVED,
                payload={k: row.get(k) for k in ("slug", "ward", "status", "work_area_group_name")},
            )
    return out


# ---------------------------------------------------------------------- visits


def _visit_events(opp: OpportunitySync, rows: list[dict], workers: dict) -> list:
    opp_id, program_id = opp.opportunity_id, opp.program_id
    out = []
    for v in rows:
        worker = workers.get(v.get("username"))
        if worker is None or not v.get("visit_id"):
            continue
        payload = {
            k: v.get(k) for k in ("status", "flagged", "flag_type", "review_status", "deliver_unit", "entity_key")
        }
        ref = {"visit_id": v["visit_id"]}
        out.append(
            ev.build(
                worker,
                ev.VISIT,
                source="connect",
                dedupe_key=ev.key("connect", opp_id, ev.VISIT, v["visit_id"]),
                opportunity_id=opp_id,
                program_id=program_id,
                occurred_at=parse_ts(v.get("visit_date")) or parse_ts(v.get("date_created")),
                payload=payload,
                ref=ref,
            )
        )
        if v.get("review_status") in ("agree", "disagree"):
            stamp = parse_ts(v.get("review_created_on"))
            out.append(
                ev.build(
                    worker,
                    ev.VISIT_REVIEWED,
                    source="connect",
                    dedupe_key=ev.key("connect", opp_id, ev.VISIT_REVIEWED, v["visit_id"], v["review_status"]),
                    opportunity_id=opp_id,
                    program_id=program_id,
                    occurred_at=stamp,
                    confidence=Confidence.EXACT if stamp else Confidence.OBSERVED,
                    payload={"review_status": v["review_status"], "status": v.get("status")},
                    ref=ref,
                )
            )
    return out


# ---------------------------------------------------------------------- one worker


def opportunity_for(opp_id: int) -> OpportunitySync:
    opp, _ = OpportunitySync.objects.get_or_create(opportunity_id=opp_id)
    return opp


def sync_worker(username: str, *, source=None, discover: bool = False, opportunity_ids=None, now=None) -> dict:
    """Sync one Connect Worker on the opportunities labs knows them on (or every readable one, ``discover``)."""
    from .connect_facade import get_source

    if source is None:
        with get_source() as own:
            return sync_worker(username, source=own, discover=discover, opportunity_ids=opportunity_ids, now=now)
    now = now or timezone.now()
    ids = set(opportunity_ids or ())
    if not ids:
        ids = set(
            WorkerMembership.objects.filter(worker__username=username, visible=True).values_list(
                "opportunity_id", flat=True
            )
        )
    discovered = None
    if discover or not ids:
        visible = list(OpportunitySync.objects.filter(visible=True).values_list("opportunity_id", flat=True))
        found = source.worker_opportunities(username, opportunity_ids=visible or None)
        discovered = found.coverage.as_dict()
        ids |= {int(r["opportunity_id"]) for r in found.rows}
    results, errors = [], {}
    for opp_id in sorted(ids):
        try:
            results.append(sync_opportunity(source, opportunity_for(opp_id), usernames={username}, now=now))
        except Exception as exc:  # noqa: BLE001 -- one unreadable opportunity must not hide the rest
            logger.warning("[workers] sync of %s on opp %s failed: %s", username, opp_id, exc)
            errors[opp_id] = str(exc)[:500]
    worker = ConnectWorker.objects.filter(username=username, erased_at__isnull=True).first()
    if worker is not None:
        try:
            from .ocs_link import link_ocs_tasks

            link_ocs_tasks(worker)
        except Exception as exc:  # noqa: BLE001 -- OCS is optional context, never a sync failure
            logger.info("[workers] OCS link for %s skipped: %s", username, exc)
    return {
        "username": username,
        "opportunities": [r["opportunity_id"] for r in results],
        "events": sum(r["events"] for r in results),
        "errors": errors,
        "discovery": discovered,
    }
