"""S2: keep canopy's anchor, pointer and profile cache of each messaged Connect Worker current.

For every Connect Worker labs messages (one with an ``Address``), labs tells canopy:

    {sub: "personalid:<username>", display_name, record_uri: "labs-worker://<username>",
     profile: {stage, program, opportunity, language, consent, timezone}, version}

The profile is LABELS an agent may see on every turn, never history; everything else stays in
labs and an agent reads it through the worker tools. It is pushed:

* when a channel is first made for the worker (``Address`` created), so canopy has the contact
  before the worker's first message;
* when something it is made of changes: the sweep reads new ``state_changed``,
  ``profile_changed`` and ``consent_changed`` timeline events past a cursor (the sync writes
  events in bulk, with no signals -- the same approach the router uses);
* just before a worker's first canopy session (``ensure_pushed``).

Only a profile whose hash changed is sent, at most 100 per request. ``version`` is
``ConnectWorker.canopy_profile_version``, raised on every push to at least the time in
milliseconds, so canopy's "newer than what I hold" check survives a restored database.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time

from django.db import transaction
from django.db.models import F, Max
from django.db.models.functions import Greatest
from django.utils import timezone

from connect_labs.messaging.canopy_bridge import conf, sdk
from connect_labs.messaging.canopy_bridge.models import ProfilePush, PushStatus
from connect_labs.workers.models import ConnectWorker, WorkerMembership, WorkerState

logger = logging.getLogger(__name__)

BATCH = 100
#: The timeline kinds after which a worker's profile may differ.
PROFILE_EVENT_KINDS = ("state_changed", "profile_changed", "consent_changed", "invited", "suspended", "unsuspended")
CURSOR = "canopy_profile"


def _primary_membership(worker: ConnectWorker) -> WorkerMembership | None:
    """The membership the profile describes: the most recently active one labs can still read,
    preferring a program labs messages the worker in."""
    memberships = WorkerMembership.objects.filter(worker=worker, visible=True)
    messaged = set(worker.addresses.exclude(program_id=None).values_list("program_id", flat=True))
    if messaged:
        in_messaged = memberships.filter(program_id__in=messaged)
        if in_messaged.exists():
            memberships = in_messaged
    return memberships.order_by(F("last_active").desc(nulls_last=True), "-opportunity_id").first()


def consent_label(worker: ConnectWorker) -> str:
    """``on`` (some channel may be sent on), ``stopped`` (the worker asked labs to stop), ``off``
    (the worker turned the channel off), or ``none`` (no channel yet)."""
    addresses = list(worker.addresses.exclude(channel_id=None).exclude(channel_id=""))
    if not addresses:
        return "none"
    if any(a.can_send for a in addresses):
        return "on"
    if any(a.opted_out_at for a in addresses):
        return "stopped"
    return "off"


def profile_for(worker: ConnectWorker) -> dict:
    """The labels canopy caches for this worker. Never a phone number, a reason or any history."""
    membership = _primary_membership(worker)
    state = WorkerState.objects.filter(membership=membership).first() if membership else None
    profile = {
        "stage": state.stage if state else None,
        "program": membership.program_id if membership else None,
        "opportunity": membership.opportunity_id if membership else None,
        "language": (worker.languages or [None])[0] if isinstance(worker.languages, list) else None,
        "consent": consent_label(worker),
        "timezone": worker.timezone or None,
    }
    return {k: v for k, v in profile.items() if v not in (None, "")}


def item_for(worker: ConnectWorker, version: int) -> dict:
    item = {
        "sub": worker.canopy_sub,
        "record_uri": worker.record_uri,
        "profile": sdk.check_profile(profile_for(worker)),
        "version": int(version),
    }
    if worker.name:
        item["display_name"] = worker.name[:200]
    return item


def _hash(worker: ConnectWorker) -> str:
    content = {"name": worker.name, "profile": profile_for(worker)}
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def mark_dirty(worker_ids) -> int:
    """Note that these workers' profiles may have changed (only workers labs messages are pushed)."""
    ids = list({int(i) for i in worker_ids})
    if not ids:
        return 0
    with_address = set(
        ConnectWorker.objects.filter(pk__in=ids, addresses__isnull=False, erased_at__isnull=True)
        .values_list("pk", flat=True)
        .distinct()
    )
    existing = set(ProfilePush.objects.filter(worker_id__in=with_address).values_list("worker_id", flat=True))
    ProfilePush.objects.bulk_create(
        [ProfilePush(worker_id=i) for i in with_address - existing], ignore_conflicts=True, batch_size=500
    )
    return ProfilePush.objects.filter(worker_id__in=with_address).update(dirty=True)


def _next_version(worker: ConnectWorker) -> int:
    """Raise and return the worker's canopy profile version (at least now, in ms)."""
    now_ms = int(time.time() * 1000)
    ConnectWorker.objects.filter(pk=worker.pk).update(
        canopy_profile_version=Greatest(F("canopy_profile_version") + 1, now_ms)
    )
    worker.refresh_from_db(fields=["canopy_profile_version"])
    return worker.canopy_profile_version


def push(workers, *, force: bool = False, post=None) -> dict:
    """Push these workers' profiles to canopy (only changed ones unless ``force``). Returns counts.

    Never raises for canopy: an unreachable canopy leaves each row dirty for the next sweep.
    """
    config = conf.host_config()
    counts = {"pushed": 0, "unchanged": 0, "failed": 0}
    if config is None or not conf.enabled():
        return counts
    batch: list[tuple[ConnectWorker, ProfilePush, str]] = []
    for worker in workers:
        if worker.erased_at is not None:
            continue
        row, _ = ProfilePush.objects.get_or_create(worker=worker)
        digest = _hash(worker)
        if not force and row.profile_hash == digest and row.status in (PushStatus.CREATED, PushStatus.UPDATED):
            ProfilePush.objects.filter(pk=row.pk).update(dirty=False)
            counts["unchanged"] += 1
            continue
        batch.append((worker, row, digest))
    for start in range(0, len(batch), BATCH):
        chunk = batch[start : start + BATCH]
        items = {}
        for worker, row, digest in chunk:
            items[worker.canopy_sub] = (worker, row, digest, item_for(worker, _next_version(worker)))
        try:
            answer = sdk.update_contacts(config, [v[3] for v in items.values()], post=post)
        except Exception as exc:  # noqa: BLE001 -- canopy refused or unreachable: retried by the sweep
            reason = f"{getattr(exc, 'code', type(exc).__name__)}: {exc}"[:300]
            logger.warning("canopy bridge: profile push of %d workers failed: %s", len(items), reason)
            ProfilePush.objects.filter(pk__in=[v[1].pk for v in items.values()]).update(
                status=PushStatus.ERROR, reason=reason, attempts=F("attempts") + 1
            )
            counts["failed"] += len(items)
            continue
        answered = {r.get("sub"): r for r in (answer or {}).get("results") or [] if isinstance(r, dict)}
        now = timezone.now()
        for sub, (worker, row, digest, item) in items.items():
            result = answered.get(sub) or {"status": PushStatus.ERROR, "reason": "canopy did not answer for it"}
            status = result.get("status") if result.get("status") in PushStatus.values else PushStatus.ERROR
            ok = status in (PushStatus.CREATED, PushStatus.UPDATED)
            ProfilePush.objects.filter(pk=row.pk).update(
                status=status,
                reason=str(result.get("reason") or "")[:300],
                profile_hash=digest if ok else row.profile_hash,
                pushed_version=item["version"] if ok else row.pushed_version,
                pushed_at=now if ok else row.pushed_at,
                # A stale answer means canopy holds a newer one: nothing to retry. A refusal is
                # retried only when something changes.
                dirty=(
                    False if status in (PushStatus.CREATED, PushStatus.UPDATED, PushStatus.STALE, "refused") else True
                ),
                attempts=F("attempts") + 1,
            )
            if ok and result.get("contact_id") is not None:
                ConnectWorker.objects.filter(pk=worker.pk).update(canopy_contact_id=str(result["contact_id"])[:64])
            counts["pushed" if ok else "failed"] += 1
    return counts


def ensure_pushed(worker: ConnectWorker, *, post=None) -> bool:
    """Before a worker's first canopy session: make sure canopy has their anchor and profile.
    Returns whether canopy holds a current one."""
    row = ProfilePush.objects.filter(worker=worker).first()
    if row is not None and not row.dirty and row.status in (PushStatus.CREATED, PushStatus.UPDATED):
        return True
    push([worker], post=post)
    row = ProfilePush.objects.filter(worker=worker).first()
    return bool(row and row.status in (PushStatus.CREATED, PushStatus.UPDATED, PushStatus.STALE))


def scan_events(limit: int = 5000) -> int:
    """Mark dirty the workers whose timeline gained a profile-changing event since the last scan."""
    from connect_labs.messaging.models import EventCursor
    from connect_labs.workers.models import WorkerEvent

    with transaction.atomic():
        cursor, created = EventCursor.objects.select_for_update().get_or_create(name=CURSOR)
        if created:
            cursor.last_id = WorkerEvent.objects.aggregate(m=Max("id"))["m"] or 0
            cursor.save(update_fields=["last_id", "updated_at"])
            return 0
        rows = list(
            WorkerEvent.objects.filter(id__gt=cursor.last_id)
            .order_by("id")
            .values_list("id", "worker_id", "kind")[:limit]
        )
        if not rows:
            return 0
        cursor.last_id = rows[-1][0]
        cursor.save(update_fields=["last_id", "updated_at"])
    return mark_dirty(worker_id for _id, worker_id, kind in rows if kind in PROFILE_EVENT_KINDS)


def push_due(limit: int = 1000, *, post=None) -> dict:
    """The sweep's part: read new events, then push every dirty profile (and any messaged worker
    canopy has never been told about)."""
    if not conf.enabled():
        return {"pushed": 0, "unchanged": 0, "failed": 0}
    scan_events()
    never = ConnectWorker.objects.filter(
        addresses__isnull=False, erased_at__isnull=True, canopy_profile_push__isnull=True
    ).values_list("pk", flat=True)
    mark_dirty(list(never.distinct()[:limit]))
    due = ConnectWorker.objects.filter(
        canopy_profile_push__dirty=True, erased_at__isnull=True, addresses__isnull=False
    ).distinct()[:limit]
    return push(list(due), post=post)
