"""Erasing a Connect Worker: in labs, and then in canopy (S6).

Labs is the system of record, so erasure starts here and cascades. ``erase_worker`` deletes, in
one transaction, everything labs holds about the worker -- their messages, threads and summaries,
channels (and connect-id's stored posts on them), facts, staff notes, timeline, memberships and
Connect snapshots, the briefs agents saw, commitments and canopy sessions -- and blanks the
record itself (name, phone, languages, time zone). It keeps only the username, marked
``erased_at`` (so the sync never writes the record again), and an ``Erasure`` row counting what
went. Then canopy is told to forget them (``erase_contacts``, ``POST /api/sites/contacts/erase``),
retried by the sweep until canopy answers. canopy's ``503 files_remain`` (a stored file it could
not delete yet; what it did stays done) is retried for as long as it takes: it never fails the
erasure after ``MAX_ATTEMPTS``, since sending it again is exactly how canopy finishes it. The sweep
takes the least-tried erasures first, so a few stuck on files never starve newer ones.

Staff only (Dimagi staff), from the Connect Worker page or the admin.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from connect_labs.messaging.canopy_bridge import conf, sdk
from connect_labs.messaging.canopy_bridge.models import (
    CanopySession,
    Erasure,
    ProfilePush,
    WorkerBrief,
    WorkerCommitment,
)
from connect_labs.workers.models import (
    ConnectWorker,
    StaffNote,
    WorkerEvent,
    WorkerFact,
    WorkerMembership,
    WorkerSnapshot,
)

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 20


def erase_worker(worker: ConnectWorker, *, by: str) -> Erasure:
    from connect_labs.messaging.models import Address, Message, Thread, TransportEvent

    counts = {}
    with transaction.atomic():
        worker = ConnectWorker.objects.select_for_update().get(pk=worker.pk)
        channels = list(
            Address.objects.filter(worker=worker).exclude(channel_id=None).values_list("channel_id", flat=True)
        )
        steps = [
            ("canopy_sessions", CanopySession.objects.filter(thread__worker=worker)),
            ("briefs", WorkerBrief.objects.filter(worker=worker)),
            ("commitments", WorkerCommitment.objects.filter(worker=worker)),
            ("messages", Message.objects.filter(worker=worker)),
            ("threads", Thread.objects.filter(worker=worker)),
            ("transport_events", TransportEvent.objects.filter(channel_id__in=channels)),
            ("addresses", Address.objects.filter(worker=worker)),
            ("facts", WorkerFact.objects.filter(worker=worker)),
            ("notes", StaffNote.objects.filter(worker=worker)),
            ("events", WorkerEvent.objects.filter(worker=worker)),
            ("snapshots", WorkerSnapshot.objects.filter(worker=worker)),
            ("memberships", WorkerMembership.objects.filter(worker=worker)),
            ("profile_push", ProfilePush.objects.filter(worker=worker)),
        ]
        for name, qs in steps:
            counts[name] = qs.count()
            qs.delete()
        worker.name = ""
        worker.phone = ""
        worker.languages = []
        worker.timezone = ""
        worker.connect_user_id = None
        worker.canopy_contact_id = ""
        worker.erased_at = timezone.now()
        worker.save()
        erasure = Erasure.objects.create(
            username=worker.username,
            sub=worker.canopy_sub,
            requested_by=(by or "")[:200],
            labs_counts=counts,
            canopy_status="pending" if conf.site_configured() else "off",
        )
        erasure_id = erasure.pk

        def enqueue():
            from connect_labs.messaging.canopy_bridge import tasks

            try:
                tasks.erase_in_canopy.delay(erasure_id)
            except Exception:  # noqa: BLE001 -- the sweep retries it
                logger.warning("canopy bridge: could not queue canopy erasure %s", erasure_id, exc_info=True)

        if erasure.canopy_status == "pending":
            transaction.on_commit(enqueue)
    return erasure


def erase_in_canopy(erasure: Erasure, *, post=None) -> str:
    """Tell canopy to forget the person (S6). Returns the status recorded."""
    config = conf.host_config()
    if config is None:
        Erasure.objects.filter(pk=erasure.pk).update(
            canopy_status="off", canopy_detail="the canopy site is not configured"
        )
        return "off"
    try:
        answer = sdk.erase_contacts(config, [erasure.sub], post=post)
    except Exception as exc:  # noqa: BLE001 -- retried by the sweep
        code = getattr(exc, "code", "") or type(exc).__name__
        if code == sdk.FILES_REMAIN:
            # Partly done in canopy; sending it again finishes it. Not a failure to give up on.
            logger.warning("canopy bridge: canopy erasure %s not finished (files_remain); retrying", erasure.pk)
            Erasure.objects.filter(pk=erasure.pk).update(
                canopy_status="error", canopy_detail=f"{code}: {exc}"[:300], attempts=erasure.attempts + 1
            )
            return "error"
        # canopy already erased part of this person (files_remain earlier): never give up on it.
        partly_done = (erasure.canopy_detail or "").startswith(sdk.FILES_REMAIN)
        status = "error" if partly_done or erasure.attempts + 1 < MAX_ATTEMPTS else "failed"
        Erasure.objects.filter(pk=erasure.pk).update(
            canopy_status=status,
            # Keep the files_remain mark at the front, so a later failure still knows.
            canopy_detail=((f"{sdk.FILES_REMAIN} earlier; " if partly_done else "") + f"{code}: {exc}")[:300],
            attempts=erasure.attempts + 1,
        )
        return status
    result = next((r for r in (answer or {}).get("results") or [] if r.get("sub") == erasure.sub), {})
    status = result.get("status") if result.get("status") in ("erased", "not_found") else "error"
    Erasure.objects.filter(pk=erasure.pk).update(
        canopy_status=status,
        canopy_detail=str(result.get("reason") or "")[:300],
        canopy_done_at=timezone.now() if status in ("erased", "not_found") else None,
        attempts=erasure.attempts + 1,
    )
    return status


def retry_pending() -> int:
    """The sweep's part: canopy erasures not yet confirmed."""
    done = 0
    # Least tried first: an erasure canopy keeps answering files_remain is retried without end, and
    # must not hold the 100 places a newer one needs.
    due = Erasure.objects.filter(canopy_status__in=("pending", "error")).order_by("attempts", "requested_at")
    for erasure in due[:100]:
        erase_in_canopy(erasure)
        done += 1
    return done
