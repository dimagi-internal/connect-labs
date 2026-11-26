"""Starting: labs opens a thread with a Connect Worker and sends its opening.

There are three ways in, and one way through (`open_and_send`):

* **A person**, in two calls like a workflow action: `preview` says exactly who would get what
  (and who is left out, and why) and returns a single-use `confirm` token bound to the person,
  the program, the playbook version and that list; `commit` must carry it. ``per_send``
  playbooks start one Connect Worker per confirm, ``per_batch`` a list. `start_confirmed` is
  the same start for another framework whose own preview and confirm were the person's click
  (`workflow/actions.py` ``start_playbook``), and `describe_start` its preview.
* **Nobody clicking** (a schedule, a state event, the API): `start_automatic`, which only a
  playbook approved ``auto_within_caps`` (with the person who approved that, `approved_by`)
  allows.
* **Nothing sent**: `dry_run`, who in the program is eligible now and what each would get.

Every start judges each worker again at the moment it starts (`rules.candidate`), so a
cap or a consent that changed since the preview still holds. One worker's failure never stops
the rest of a batch, a schedule or an event.
"""

from __future__ import annotations

import hashlib
import json
import logging
import secrets

from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from connect_labs.messaging import config, handlers, playbooks, service
from connect_labs.messaging.models import Address, AuthorKind, Direction, Thread
from connect_labs.messaging.service import MessagingError
from connect_labs.workers.models import WorkerMembership

from .rules import (
    Candidate,
    blocked_now,
    candidate,
    flow_of,
    handler_ref,
    is_one_way,
    outbound_playbook,
    purpose_of,
)

logger = logging.getLogger(__name__)

CONFIRM_MAX_AGE_SECONDS = 15 * 60
#: Unchanged since this lived in ``router.py``: a preview made before a deploy still confirms after it.
_CONFIRM_SALT = "connect_labs.messaging.router.confirm"
#: The most Connect Workers one start may name (one decision a person can review).
MAX_WORKERS = 200


# ---------------------------------------------------------------------------
# The one way through
# ---------------------------------------------------------------------------


def open_and_send(
    playbook: dict, key: str, program_id, cand: Candidate, *, started_by: str, approval: dict, context: dict
) -> Thread:
    """Open `cand`'s thread and send (or hold, for quiet hours) its opening. Raises on failure.

    The thread opens under a lock on the worker's channel, after the channel and the caps are
    judged again (`blocked_now`): two starts at once (two people, a schedule and a state event)
    or a start while the worker's own message is being placed (`service.record_inbound` takes
    the same lock) cannot both open a conversation. Making the channel on PersonalID, an HTTP
    call, happens before the lock, never under it.
    """
    address = service.address_for(
        cand.worker,
        program_id,
        purpose=purpose_of(cand.handler),
        channel_name=config.program_settings(program_id).get("channel_name") or "Connect",
    )
    with transaction.atomic():
        Address.objects.select_for_update().filter(pk=address.pk).first()
        reason = blocked_now(playbook, key, program_id, cand)
        if reason:
            raise MessagingError("not_eligible", reason)
        thread = service.open_thread(
            cand.worker,
            address=address,
            direction=Direction.OUTBOUND,
            program_id=program_id,
            opportunity_id=cand.opportunity_id,
            handler_kind=cand.handler["kind"],
            handler_ref=handler_ref(cand.handler),
            playbook_key=key,
            playbook_version=playbook["version"],
            variant=cand.variant,
            started_by=started_by,
            approval=approval,
            state_snapshot=cand.snapshot,
            context=context or {},
        )
    try:
        service.send(
            thread,
            cand.opening,
            author_kind=AuthorKind.FLOW if flow_of(cand.handler) else AuthorKind.SYSTEM,
            author_ref=f"playbook:{key}@{playbook['version']}" + (f"/{cand.variant}" if cand.variant else ""),
            scheduled_for=cand.send_at,
        )
    except MessagingError as e:
        service.close_thread(thread, f"not started: {e.public_message}")
        raise
    handlers.start(thread, {"context": context or {}, "opening": cand.opening, "playbook": playbook})
    if is_one_way(cand.handler):
        service.close_thread(thread, "sent (one way)")
    thread.refresh_from_db()
    return thread


def _try_open(playbook: dict, key: str, program_id, cand: Candidate, **kwargs) -> Thread | str:
    """`open_and_send`, for a batch: the thread, or why it did not start (logged, never raised)."""
    try:
        return open_and_send(playbook, key, program_id, cand, **kwargs)
    except Exception as e:  # noqa: BLE001 -- one worker's failure never stops the rest
        logger.warning("messaging: could not start %s for %s", key, cand.username, exc_info=True)
        return getattr(e, "public_message", None) or type(e).__name__


# ---------------------------------------------------------------------------
# What a person starts, and what they confirmed
# ---------------------------------------------------------------------------


def _workers_arg(workers) -> list[tuple[str, int | None]]:
    if not isinstance(workers, list) or not workers:
        raise MessagingError("invalid", "name at least one Connect Worker")
    if len(workers) > MAX_WORKERS:
        raise MessagingError("invalid", f"at most {MAX_WORKERS} Connect Workers at a time")
    out, seen = [], set()
    for w in workers:
        username, opp = (w, None) if isinstance(w, str) else (w.get("username"), w.get("opportunity_id"))
        if not username:
            raise MessagingError("invalid", "each Connect Worker is named by their PersonalID username")
        if username.lower() in seen:
            raise MessagingError("invalid", f"{username} is named twice")
        seen.add(username.lower())
        out.append((username, int(opp) if opp not in (None, "") else None))
    return sorted(out)


def _approval_mode(playbook: dict, key: str, named: list) -> str:
    """The playbook's approval mode, refusing a batch for a ``per_send`` playbook."""
    mode = (playbook.get("approval") or {}).get("mode", "per_send")
    if mode == "per_send" and len(named) > 1:
        raise MessagingError("invalid", f"Playbook {key!r} is approved per send: start one Connect Worker at a time.")
    return mode


def _approved_by_person(playbook: dict, user, via: str) -> dict:
    """The approval a thread records when a person's click started it."""
    return {
        "mode": (playbook.get("approval") or {}).get("mode", "per_send"),
        "approved_by": user.username,
        "approved_at": timezone.now().isoformat(),
        "via": via or "labs",
    }


def _digest(user_pk, program_id, key, version, workers, context) -> str:
    raw = json.dumps([user_pk, int(program_id), key, version, workers, context or {}], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _spend_confirm(confirm: str, digest: str) -> None:
    """Check `confirm` is a live token from a preview of exactly this start, and spend it."""
    try:
        signed = signing.loads(confirm or "", salt=_CONFIRM_SALT, max_age=CONFIRM_MAX_AGE_SECONDS)
    except signing.SignatureExpired as e:
        raise MessagingError(
            "confirm_expired", "That preview has expired. Preview again and confirm the new one."
        ) from e
    except signing.BadSignature as e:
        raise MessagingError("confirm_invalid", "`confirm` is not a token from a preview of this start.") from e
    if signed.get("d") != digest:
        raise MessagingError(
            "confirm_mismatch", "This is not what was previewed (or the playbook changed since). Preview again."
        )
    if not cache.add(f"messaging-confirm:{signed.get('n')}", 1, CONFIRM_MAX_AGE_SECONDS):
        raise MessagingError("confirm_used", "That preview was already confirmed.")


def _caller_user(caller, program_id):
    """The person starting, once they may start for `program_id` and messaging is on there."""
    from connect_labs.messaging import access

    service.require_enabled()
    service.require_program_enabled(program_id)
    reason = access.may_use_program(caller, program_id)
    if reason:
        raise MessagingError("forbidden", reason)
    return access.caller_for(caller).user


def preview(caller, *, program_id, playbook_key: str, workers, context: dict | None = None) -> dict:
    """What starting `playbook_key` for `workers` would do, and the token to confirm it.

    `workers` are PersonalID usernames, or ``{username, opportunity_id}``. Nothing is sent.
    """
    user = _caller_user(caller, program_id)
    playbook = outbound_playbook(program_id, playbook_key)
    named = _workers_arg(workers)
    mode = _approval_mode(playbook, playbook_key, named)
    now = timezone.now()
    cands = [candidate(playbook, playbook_key, program_id, u, o, context, now) for u, o in named]
    ready = [c for c in cands if c.blocked is None]
    result = {
        "program_id": int(program_id),
        "playbook": {
            "key": playbook_key,
            "version": playbook["version"],
            "label": playbook.get("label") or playbook_key,
            "approval": mode,
            "handler": playbook["handler"],
        },
        "workers": [c.describe() for c in cands],
        "summary": f"{playbook.get('label') or playbook_key}: {len(ready)} of {len(cands)} Connect Worker"
        f"{'s' if len(cands) != 1 else ''} would get a message",
    }
    if ready:
        result["confirm"] = signing.dumps(
            {
                "d": _digest(user.pk, program_id, playbook_key, playbook["version"], named, context),
                "n": secrets.token_urlsafe(9),
            },
            salt=_CONFIRM_SALT,
            compress=True,
        )
        result["confirm_expires_in"] = CONFIRM_MAX_AGE_SECONDS
    else:
        result["needs"] = ["workers"]
    return result


def commit(
    caller, *, program_id, playbook_key: str, workers, confirm: str, context: dict | None = None, via: str = ""
) -> dict:
    """Start what a preview showed. `workers` and `context` must be what was previewed; `confirm` is spent."""
    user = _caller_user(caller, program_id)
    playbook = outbound_playbook(program_id, playbook_key)
    named = _workers_arg(workers)
    _spend_confirm(confirm, _digest(user.pk, program_id, playbook_key, playbook["version"], named, context))
    approval = _approved_by_person(playbook, user, via)
    started, skipped = [], []
    for username, opp in named:
        # Re-judged now: something may have changed since the preview (a cap, consent).
        cand = candidate(playbook, playbook_key, program_id, username, opp, context)
        result = cand.blocked or _try_open(
            playbook,
            playbook_key,
            program_id,
            cand,
            started_by=f"person:{user.username}",
            approval=approval,
            context=context or {},
        )
        if isinstance(result, Thread):
            started.append({"username": username, "thread_id": result.pk})
        else:
            skipped.append({"username": username, "blocked": result})
    return {"started": started, "skipped": skipped}


def start_confirmed(
    caller, *, program_id, playbook_key: str, username: str, opportunity_id=None, context=None, via: str = ""
) -> Thread:
    """Start a playbook for one worker on a person's confirmation made elsewhere.

    For a workflow action (`workflow/actions.py` ``start_playbook``), whose own preview and
    single-use confirm are the person's click: the approval is recorded as theirs. Raises
    `MessagingError` (``code`` ``not_eligible`` and the reason) when the worker is left out.
    """
    user = _caller_user(caller, program_id)
    playbook = outbound_playbook(program_id, playbook_key)
    cand = candidate(playbook, playbook_key, program_id, username, opportunity_id, context)
    if cand.blocked:
        raise MessagingError("not_eligible", cand.blocked)
    return open_and_send(
        playbook,
        playbook_key,
        program_id,
        cand,
        started_by=f"person:{user.username}",
        approval=_approved_by_person(playbook, user, via),
        context=context or {},
    )


def describe_start(program_id, playbook_key: str, workers, context: dict | None = None) -> dict:
    """What starting `playbook_key` for `workers` would send, per worker, without a token (for another
    framework's own preview). Raises `MessagingError` if the playbook cannot be started at all."""
    service.require_enabled()
    service.require_program_enabled(program_id)
    playbook = outbound_playbook(program_id, playbook_key)
    named = _workers_arg(workers)
    mode = _approval_mode(playbook, playbook_key, named)
    now = timezone.now()
    return {
        "playbook": {"key": playbook_key, "version": playbook["version"], "approval": mode},
        "workers": [candidate(playbook, playbook_key, program_id, u, o, context, now).describe() for u, o in named],
    }


# ---------------------------------------------------------------------------
# What starts with nobody clicking, and what would
# ---------------------------------------------------------------------------


def start_automatic(*, program_id, playbook_key: str, username: str, trigger: str, opportunity_id=None, context=None):
    """Start a playbook with nobody clicking (a schedule, a state event, the API).

    Only a playbook approved ``auto_within_caps`` may; caps and eligibility apply as always.
    Returns the thread, or the reason it did not start (never raises for one worker).
    """
    if not service.enabled() or not config.program_enabled(program_id):
        return "messaging is off"
    try:
        playbook = outbound_playbook(program_id, playbook_key)
    except MessagingError as e:
        return e.public_message
    approval = playbook.get("approval") or {}
    if approval.get("mode") != "auto_within_caps" or not approval.get("approved_by"):
        return "this playbook is not approved to start without a person"
    cand = candidate(playbook, playbook_key, program_id, username, opportunity_id, context)
    return cand.blocked or _try_open(
        playbook,
        playbook_key,
        program_id,
        cand,
        started_by=f"system:{trigger}",
        approval={**approval, "via": trigger},
        context=context or {},
    )


def dry_run(caller, *, program_id, playbook_key: str, limit: int = MAX_WORKERS, context: dict | None = None) -> dict:
    """Who in the program is eligible for this playbook now, and the exact opening each would get. Sends nothing."""
    from connect_labs.messaging import access

    reason = access.may_use_program(caller, program_id)
    if reason:
        raise MessagingError("forbidden", reason)
    playbook = playbooks.get(program_id, playbook_key)
    if playbook is None:
        raise MessagingError("not_found", f"Program {program_id} has no messaging playbook {playbook_key!r}.")
    usernames = list(
        WorkerMembership.objects.filter(program_id=program_id, worker__erased_at__isnull=True)
        .values_list("worker__username", flat=True)
        .distinct()
        .order_by("worker__username")[: max(1, min(int(limit), 1000))]
    )
    now = timezone.now()
    cands = [candidate(playbook, playbook_key, program_id, u, None, context, now) for u in usernames]
    return {
        "program_id": int(program_id),
        "playbook": {"key": playbook_key, "version": playbook.get("version"), "direction": playbook.get("direction")},
        "messaging_on": service.enabled() and config.program_enabled(program_id),
        "eligible": [c.describe() for c in cands if c.blocked is None],
        "not_eligible": [c.describe() for c in cands if c.blocked is not None],
        "counted": len(cands),
        "warnings": playbooks.warnings(playbook_key, playbook),
    }
