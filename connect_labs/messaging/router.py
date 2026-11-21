"""The router: which conversations start, and where a Connect Worker's message goes.

**Outbound** (labs begins): eligibility, then caps, then approval, then the thread opens and
its opening is sent.

* A person starts a playbook in two calls, like a workflow action: `preview` says exactly who
  would get what (and who is left out, and why) and returns a single-use `confirm` token bound
  to the person, the program, the playbook version and that list; `commit` must carry it.
  ``per_send`` playbooks start one Connect Worker per confirm, ``per_batch`` a list.
* Nothing starts without a person's click unless the playbook's approval is
  ``auto_within_caps`` (with the person who approved that, `approved_by`): that is what
  `start_automatic` (schedules, state events, the API) requires.
* Caps: the playbook's ``threads_per_worker_per_week``, one open conversation per channel at
  a time (one-way notices excepted), and quiet hours in the worker's time zone, which hold the
  opening back until they end rather than refusing it.

**Inbound** (the worker writes): their open thread's handler; else the first of the program's
inbound playbooks (lowest `priority`) whose eligibility the worker meets; else the Inbox.
Messages are debounced per thread (`MESSAGING_DEBOUNCE_SECONDS`): the handler is called once
with every message that arrived in the meantime. Exit keywords close a thread, and a
playbook's keyword handoffs move it, before the handler sees the message.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import logging
import secrets
from dataclasses import dataclass, field
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.conf import settings
from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.utils import timezone

from connect_labs.messaging import config, flows, handlers, playbooks, service
from connect_labs.messaging.models import (
    Address,
    AddressPurpose,
    AuthorKind,
    Direction,
    HandlerKind,
    Thread,
    ThreadState,
)
from connect_labs.messaging.service import MessagingError
from connect_labs.workers.models import ConnectWorker, WorkerMembership

logger = logging.getLogger(__name__)

CONFIRM_MAX_AGE_SECONDS = 15 * 60
_CONFIRM_SALT = "connect_labs.messaging.router.confirm"
#: The most Connect Workers one start may name (one decision a person can review).
MAX_WORKERS = 200


# ---------------------------------------------------------------------------
# Who, and whether
# ---------------------------------------------------------------------------


@dataclass
class Candidate:
    """One Connect Worker a start would reach, and what would happen to them."""

    username: str
    worker: ConnectWorker | None = None
    opportunity_id: int | None = None
    blocked: str | None = None
    opening: str = ""
    variant: str = ""
    handler: dict = field(default_factory=dict)
    send_at: dt.datetime | None = None
    snapshot: dict = field(default_factory=dict)

    def describe(self) -> dict:
        out = {
            "username": self.username,
            "name": getattr(self.worker, "name", "") or self.username,
            "opportunity_id": self.opportunity_id,
            "eligible": self.blocked is None,
        }
        if self.blocked:
            out["blocked"] = self.blocked
        else:
            out.update({"opening": self.opening, "handler": self.handler})
            if self.variant:
                out["variant"] = self.variant
            if self.send_at:
                out["held_until"] = self.send_at.isoformat()
        if self.snapshot.get("stage"):
            out["stage"] = self.snapshot["stage"]
        return out


def _compare(value, ops: dict) -> bool:
    for op, target in ops.items():
        if op == "exists":
            if (value is not None) != bool(target):
                return False
            continue
        if value is None:
            return False
        try:
            if op == "eq" and not value == target:
                return False
            if op == "ne" and not value != target:
                return False
            if op == "gt" and not value > target:
                return False
            if op == "gte" and not value >= target:
                return False
            if op == "lt" and not value < target:
                return False
            if op == "lte" and not value <= target:
                return False
            if op == "in" and value not in (target or []):
                return False
        except TypeError:
            return False
    return True


def _facet(facets: dict, path: str):
    value = facets
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _snapshot(membership) -> dict:
    state = getattr(membership, "state", None) if membership is not None else None
    if state is None:
        return {"opportunity_id": membership.opportunity_id} if membership is not None else {}
    return {
        "opportunity_id": membership.opportunity_id,
        "stage": state.stage,
        "facets": state.facets,
        "computed_at": state.computed_at.isoformat(),
    }


def eligibility(
    playbook: dict, worker: ConnectWorker, program_id, opportunity_id=None
) -> tuple[str | None, dict, int | None]:
    """(why not eligible, or None; the state it was judged on; the opportunity it came from)."""
    rule = playbook.get("eligible") or {}
    memberships = WorkerMembership.objects.filter(worker=worker, program_id=program_id).select_related("state")
    if opportunity_id is not None:
        memberships = memberships.filter(opportunity_id=opportunity_id)
    memberships = list(memberships.order_by("opportunity_id"))
    if not rule:
        # Anyone may get it; the state is still recorded, from the opportunity named (or the only one).
        chosen = memberships[0] if len(memberships) == 1 else None
        return (
            None,
            _snapshot(chosen),
            opportunity_id if opportunity_id is not None else getattr(chosen, "opportunity_id", None),
        )
    if not memberships:
        return "not on an opportunity in this program (as labs last synced)", {}, opportunity_id
    reasons = []
    for membership in memberships:
        state = getattr(membership, "state", None)
        if state is None:
            reasons.append("no state computed yet")
            continue
        if rule.get("stages") and state.stage not in rule["stages"]:
            reasons.append(f"stage is {state.stage}")
            continue
        failing = [
            p for p, ops in (rule.get("facets") or {}).items() if not _compare(_facet(state.facets or {}, p), ops)
        ]
        if failing:
            reasons.append(f"{', '.join(failing)} not as required")
            continue
        return None, _snapshot(membership), membership.opportunity_id
    return "not eligible: " + "; ".join(sorted(set(reasons))), {}, opportunity_id


def _zone(worker: ConnectWorker, program_id) -> ZoneInfo:
    for name in (worker.timezone, config.program_settings(program_id).get("timezone"), "UTC"):
        if name:
            try:
                return ZoneInfo(name)
            except (ZoneInfoNotFoundError, ValueError):
                continue
    return ZoneInfo("UTC")


def quiet_until(
    playbook: dict, worker: ConnectWorker, program_id, now: dt.datetime | None = None
) -> dt.datetime | None:
    """If it is quiet hours for the worker now, when they end (UTC); else None."""
    quiet = (playbook.get("caps") or {}).get("quiet_hours")
    if not quiet:
        return None
    now = now or timezone.now()
    zone = _zone(worker, program_id)
    local = now.astimezone(zone)
    start = dt.time.fromisoformat(quiet["start"])
    end = dt.time.fromisoformat(quiet["end"])
    t = local.time()
    if start == end:
        return None
    inside = (start <= t < end) if start < end else (t >= start or t < end)
    if not inside:
        return None
    release = dt.datetime.combine(local.date(), end, tzinfo=zone)
    if release <= local:
        release += dt.timedelta(days=1)
    return release.astimezone(dt.UTC)


def _week_cap_reason(playbook: dict, key: str, worker: ConnectWorker, program_id, now) -> str | None:
    cap = (playbook.get("caps") or {}).get("threads_per_worker_per_week")
    if cap is None:
        return None
    started = Thread.objects.filter(
        worker=worker,
        program_id=program_id,
        playbook_key=key,
        direction=Direction.OUTBOUND,
        opened_at__gte=now - dt.timedelta(days=7),
    ).count()
    if started >= cap:
        return f"already started {started} time{'s' if started != 1 else ''} this week (cap {cap})"
    return None


def _flow_of(handler: dict) -> flows.Flow | None:
    return flows.FLOWS.get(handler.get("key")) if handler.get("kind") == "flow" else None


def _is_one_way(handler: dict) -> bool:
    flow = _flow_of(handler)
    return handler.get("kind") == "none" or (flow is not None and flow.one_way)


def _busy_reason(worker: ConnectWorker, program_id, purpose: str, handler: dict) -> str | None:
    if _is_one_way(handler):
        return None
    open_one = (
        Thread.objects.filter(
            worker=worker,
            state=ThreadState.OPEN,
            address__channel_source=service.channel_source(program_id, purpose),
        )
        .order_by("-opened_at")
        .first()
    )
    if open_one is not None:
        return f"a conversation is already open with them ({open_one.playbook_key or 'thread'} {open_one.pk})"
    return None


def _channel_reason(worker: ConnectWorker, program_id, purpose: str) -> str | None:
    address = Address.objects.filter(worker=worker, channel_source=service.channel_source(program_id, purpose)).first()
    if address is None:
        return None
    if not address.consent:
        return "turned this channel off in their app"
    if address.opted_out_at:
        return "asked us to stop (STOP)"
    return None


def _values(worker: ConnectWorker, program_id, context: dict) -> dict:
    name = worker.name or worker.username
    return {
        **flows.prepare(context or {}),
        "name": name,
        "first_name": name.split()[0] if name else worker.username,
        "program": config.program_settings(program_id).get("channel_name") or "your program",
    }


def _candidate(
    playbook: dict, key: str, program_id, username: str, opportunity_id=None, context=None, now=None
) -> Candidate:
    now = now or timezone.now()
    cand = Candidate(username=username, opportunity_id=opportunity_id)
    worker = ConnectWorker.objects.filter(username__iexact=username, erased_at__isnull=True).first()
    if worker is None:
        cand.blocked = "not a Connect Worker labs knows"
        return cand
    cand.worker = worker
    reason, snapshot, opp = eligibility(playbook, worker, program_id, opportunity_id)
    cand.snapshot, cand.opportunity_id = snapshot, opp
    if reason:
        cand.blocked = reason
        return cand
    variant = playbooks.assign_variant(playbook, key, worker.username) or {}
    cand.variant = variant.get("key", "")
    cand.handler = dict(variant.get("handler") or playbook["handler"])
    flow = _flow_of(cand.handler)
    purpose = flow.purpose if flow else AddressPurpose.PROGRAM
    cand.blocked = (
        _channel_reason(worker, program_id, purpose)
        or _week_cap_reason(playbook, key, worker, program_id, now)
        or _busy_reason(worker, program_id, purpose, cand.handler)
    )
    if cand.blocked:
        return cand
    values = _values(worker, program_id, context or {})
    try:
        text = variant.get("opening") or playbook.get("opening")
        cand.opening = playbooks.fill(text, values) if text else flow.opening(values)
    except (flows.FlowError, AttributeError) as e:
        cand.blocked = str(e) if isinstance(e, flows.FlowError) else "no opening to send"
        return cand
    cand.send_at = quiet_until(playbook, worker, program_id, now)
    return cand


def _playbook(program_id, key: str) -> dict:
    playbook = playbooks.get(program_id, key)
    if playbook is None:
        raise MessagingError("not_found", f"Program {program_id} has no messaging playbook {key!r}.")
    if not playbooks.is_enabled(playbook):
        raise MessagingError("playbook_off", f"Playbook {key!r} is turned off.")
    if playbook.get("direction") != "outbound":
        raise MessagingError("invalid", f"Playbook {key!r} is inbound: a Connect Worker starts it, not labs.")
    return playbook


# ---------------------------------------------------------------------------
# Starting: one place that opens a thread and sends its opening
# ---------------------------------------------------------------------------


def _open(
    playbook: dict, key: str, program_id, cand: Candidate, *, started_by: str, approval: dict, context: dict
) -> Thread:
    flow = _flow_of(cand.handler)
    purpose = flow.purpose if flow else AddressPurpose.PROGRAM
    address = service.address_for(
        cand.worker,
        program_id,
        purpose=purpose,
        channel_name=config.program_settings(program_id).get("channel_name") or "Connect",
    )
    handler_ref = {
        "agent": f"{cand.handler.get('agent', '')}:{cand.handler.get('skill', '')}",
        "flow": cand.handler.get("key", ""),
        "human": cand.handler.get("queue", "inbox"),
        "none": "",
    }[cand.handler["kind"]]
    thread = service.open_thread(
        cand.worker,
        address=address,
        direction=Direction.OUTBOUND,
        program_id=program_id,
        opportunity_id=cand.opportunity_id,
        handler_kind=cand.handler["kind"],
        handler_ref=handler_ref,
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
            author_kind=AuthorKind.FLOW if flow else AuthorKind.SYSTEM,
            author_ref=f"playbook:{key}@{playbook['version']}" + (f"/{cand.variant}" if cand.variant else ""),
            scheduled_for=cand.send_at,
        )
    except MessagingError as e:
        service.close_thread(thread, f"not started: {e.public_message}")
        raise
    handlers.start(thread, {"context": context or {}, "opening": cand.opening, "playbook": playbook})
    if _is_one_way(cand.handler):
        service.close_thread(thread, "sent (one way)")
    thread.refresh_from_db()
    return thread


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


def _digest(user_pk, program_id, key, version, workers, context) -> str:
    raw = json.dumps([user_pk, int(program_id), key, version, workers, context or {}], sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()


def _require_can_start(caller, program_id) -> None:
    from connect_labs.messaging import access

    service.require_enabled()
    service.require_program_enabled(program_id)
    reason = access.may_use_program(caller, program_id)
    if reason:
        raise MessagingError("forbidden", reason)


def preview(caller, *, program_id, playbook_key: str, workers, context: dict | None = None) -> dict:
    """What starting `playbook_key` for `workers` would do, and the token to confirm it.

    `workers` are PersonalID usernames, or ``{username, opportunity_id}``. Nothing is sent.
    """
    from connect_labs.messaging import access

    _require_can_start(caller, program_id)
    user = access.caller_for(caller).user
    playbook = _playbook(program_id, playbook_key)
    named = _workers_arg(workers)
    mode = (playbook.get("approval") or {}).get("mode", "per_send")
    if mode == "per_send" and len(named) > 1:
        raise MessagingError(
            "invalid", f"Playbook {playbook_key!r} is approved per send: start one Connect Worker at a time."
        )
    now = timezone.now()
    cands = [_candidate(playbook, playbook_key, program_id, u, o, context, now) for u, o in named]
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
    from connect_labs.messaging import access

    _require_can_start(caller, program_id)
    user = access.caller_for(caller).user
    playbook = _playbook(program_id, playbook_key)
    named = _workers_arg(workers)
    try:
        signed = signing.loads(confirm or "", salt=_CONFIRM_SALT, max_age=CONFIRM_MAX_AGE_SECONDS)
    except signing.SignatureExpired as e:
        raise MessagingError(
            "confirm_expired", "That preview has expired. Preview again and confirm the new one."
        ) from e
    except signing.BadSignature as e:
        raise MessagingError("confirm_invalid", "`confirm` is not a token from a preview of this start.") from e
    if signed.get("d") != _digest(user.pk, program_id, playbook_key, playbook["version"], named, context):
        raise MessagingError(
            "confirm_mismatch", "This is not what was previewed (or the playbook changed since). Preview again."
        )
    if not cache.add(f"messaging-confirm:{signed.get('n')}", 1, CONFIRM_MAX_AGE_SECONDS):
        raise MessagingError("confirm_used", "That preview was already confirmed.")

    mode = (playbook.get("approval") or {}).get("mode", "per_send")
    approval = {
        "mode": mode,
        "approved_by": user.username,
        "approved_at": timezone.now().isoformat(),
        "via": via or "labs",
    }
    started, skipped = [], []
    for username, opp in named:
        # Re-judged now: something may have changed since the preview (a cap, consent).
        cand = _candidate(playbook, playbook_key, program_id, username, opp, context)
        if cand.blocked:
            skipped.append({"username": username, "blocked": cand.blocked})
            continue
        try:
            thread = _open(
                playbook,
                playbook_key,
                program_id,
                cand,
                started_by=f"person:{user.username}",
                approval=approval,
                context=context or {},
            )
        except Exception as e:  # noqa: BLE001 -- one worker's failure never stops the rest
            logger.warning("messaging: could not start %s for %s", playbook_key, username, exc_info=True)
            skipped.append({"username": username, "blocked": getattr(e, "public_message", None) or type(e).__name__})
            continue
        started.append({"username": username, "thread_id": thread.pk})
    return {"started": started, "skipped": skipped}


def start_automatic(*, program_id, playbook_key: str, username: str, trigger: str, opportunity_id=None, context=None):
    """Start a playbook with nobody clicking (a schedule, a state event, the API).

    Only a playbook approved ``auto_within_caps`` may; caps and eligibility apply as always.
    Returns the thread, or the reason it did not start.
    """
    if not service.enabled() or not config.program_enabled(program_id):
        return "messaging is off"
    try:
        playbook = _playbook(program_id, playbook_key)
    except MessagingError as e:
        return e.public_message
    approval = playbook.get("approval") or {}
    if approval.get("mode") != "auto_within_caps" or not approval.get("approved_by"):
        return "this playbook is not approved to start without a person"
    cand = _candidate(playbook, playbook_key, program_id, username, opportunity_id, context)
    if cand.blocked:
        return cand.blocked
    return _open(
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
    cands = [_candidate(playbook, playbook_key, program_id, u, None, context, now) for u in usernames]
    return {
        "program_id": int(program_id),
        "playbook": {"key": playbook_key, "version": playbook.get("version"), "direction": playbook.get("direction")},
        "messaging_on": service.enabled() and config.program_enabled(program_id),
        "eligible": [c.describe() for c in cands if c.blocked is None],
        "not_eligible": [c.describe() for c in cands if c.blocked is not None],
        "counted": len(cands),
    }


# ---------------------------------------------------------------------------
# Inbound
# ---------------------------------------------------------------------------


def thread_for_inbound(address: Address, text: str) -> tuple[Thread, bool]:
    """The thread a worker's message belongs to: open one, an inbound playbook's, or the Inbox's."""
    thread = service.open_threads_on(address).order_by("-opened_at", "-id").first()
    if thread is not None:
        return thread, False
    worker, program_id = address.worker, address.program_id
    if program_id is not None and service.enabled() and config.program_enabled(program_id):
        for key, playbook in playbooks.inbound_for(program_id):
            reason, snapshot, opp = eligibility(playbook, worker, program_id)
            if reason:
                continue
            variant = playbooks.assign_variant(playbook, key, worker.username) or {}
            handler = variant.get("handler") or playbook["handler"]
            return (
                service.open_thread(
                    worker,
                    address=address,
                    direction=Direction.INBOUND,
                    program_id=program_id,
                    opportunity_id=opp,
                    handler_kind=handler["kind"],
                    handler_ref=handler.get("key")
                    or handler.get("queue")
                    or (
                        f"{handler.get('agent', '')}:{handler.get('skill', '')}" if handler["kind"] == "agent" else ""
                    ),
                    playbook_key=key,
                    playbook_version=playbook["version"],
                    variant=variant.get("key", ""),
                    started_by="worker",
                    state_snapshot=snapshot,
                ),
                True,
            )
    return (
        service.open_thread(
            worker,
            address=address,
            direction=Direction.INBOUND,
            program_id=program_id,
            handler_kind=HandlerKind.HUMAN,
            handler_ref="inbox",
            playbook_key="",
            started_by="worker",
        ),
        True,
    )


def _matches(text: str, keywords) -> bool:
    words = {w.strip().lower() for w in keywords or []}
    said = (text or "").strip().lower().rstrip(".!")
    return said in words


def after_inbound(thread: Thread, message, is_new: bool) -> None:
    """What happens once a worker's message is stored: exits, handoffs, then the (debounced) handler."""
    playbook = playbooks.for_thread(thread) or {}
    if is_new:
        handlers.start(thread, {"reason": "The Connect Worker wrote in", "playbook": playbook})
        thread.refresh_from_db()
        if not thread.is_open:
            return
    if _matches(message.text, (playbook.get("exit") or {}).get("keywords")):
        service.close_thread(thread, "the Connect Worker ended it", by="exit keyword")
        return
    for rule in playbook.get("handoffs") or []:
        if _matches(message.text, rule["when"].get("keywords")):
            handoff_to(
                thread, rule["to"], note=rule.get("note") or f"The Connect Worker said {message.text.strip()!r}"
            )
            thread.refresh_from_db()
            break
    schedule_handling(thread)


def handoff_to(thread: Thread, target: str, *, note: str = "", by: str = "") -> None:
    """Hand a thread to a person ("human") or to another of its program's playbooks."""
    if target == "human":
        handlers.to_human(thread, note or "Handed to a person", by=by)
        return
    playbook = playbooks.get(thread.program_id, target)
    if playbook is None or not playbooks.is_enabled(playbook):
        handlers.to_human(thread, f"{note} (no playbook {target!r} to hand to)".strip(), by=by)
        return
    handler = playbook["handler"]
    ref = (
        handler.get("key")
        or handler.get("queue")
        or (f"{handler.get('agent', '')}:{handler.get('skill', '')}" if handler["kind"] == "agent" else "")
    )
    Thread.objects.filter(pk=thread.pk).update(playbook_key=target, playbook_version=playbook["version"])
    thread.playbook_key, thread.playbook_version = target, playbook["version"]
    handlers.handoff(thread, to_kind=handler["kind"], to_ref=ref, note=note, by=by)


def schedule_handling(thread: Thread) -> None:
    """(Re)start the debounce: the handler sees this thread's new messages once the worker pauses."""
    from connect_labs.messaging import tasks

    delay = int(getattr(settings, "MESSAGING_DEBOUNCE_SECONDS", 8))
    due = timezone.now() + dt.timedelta(seconds=delay)
    Thread.objects.filter(pk=thread.pk).update(inbound_due_at=due)
    thread_id = thread.pk

    def enqueue():
        try:
            tasks.handle_inbound.apply_async(args=[thread_id], countdown=delay)
        except Exception:  # noqa: BLE001 -- the sweep handles any thread left due
            logger.warning("messaging: could not queue handling of thread %s", thread_id, exc_info=True)

    transaction.on_commit(enqueue)


def handle_due(thread_id: int, now: dt.datetime | None = None) -> str:
    """Give the handler every message the worker sent since it last looked, once they have paused."""
    from connect_labs.messaging.models import Message

    now = now or timezone.now()
    with transaction.atomic():
        thread = Thread.objects.select_for_update().filter(pk=thread_id).first()
        if thread is None or thread.inbound_due_at is None:
            return "nothing due"
        # A later message moved the time on; the task queued for it will do the work.
        if thread.inbound_due_at > now + dt.timedelta(milliseconds=500):
            return "not yet"
        pending = list(
            Message.objects.filter(thread=thread, direction=Direction.INBOUND, handled_at__isnull=True).order_by(
                "created_at", "id"
            )
        )
        Message.objects.filter(pk__in=[m.pk for m in pending]).update(handled_at=now)
        thread.inbound_due_at = None
        thread.save(update_fields=["inbound_due_at"])
    if not pending or not thread.is_open:
        return "nothing to handle"
    handlers.on_message(thread, pending)
    return f"handled {len(pending)}"


# ---------------------------------------------------------------------------
# Automatic triggers: state events and schedules
# ---------------------------------------------------------------------------


def on_worker_event(event) -> list:
    """A `WorkerEvent` happened: start any state_event playbook of its program that asks for it."""
    if event.program_id is None or not service.enabled() or not config.program_enabled(event.program_id):
        return []
    results = []
    for key, playbook in playbooks.all_for(event.program_id).items():
        trigger = playbook.get("trigger") or {}
        if (
            playbook.get("direction") != "outbound"
            or not playbooks.is_enabled(playbook)
            or trigger.get("type") != "state_event"
            or event.kind not in (trigger.get("kinds") or [])
        ):
            continue
        payload = event.payload or {}
        stage = payload.get("stage_to") or payload.get("stage") or payload.get("to")
        if trigger.get("stages") and stage not in trigger["stages"]:
            continue
        results.append(
            start_automatic(
                program_id=event.program_id,
                playbook_key=key,
                username=event.worker.username,
                trigger=f"state_event:{event.kind}",
                opportunity_id=event.opportunity_id,
            )
        )
    return results


#: Events older than this when first read never start anything (a backlog is not a reason to message).
EVENT_MAX_AGE = dt.timedelta(hours=6)


def scan_worker_events(limit: int = 2000, now: dt.datetime | None = None) -> int:
    """Read the timeline past the cursor and run state-event triggers on what is new. Returns how many ran.

    A cursor, not a signal: the sync writes events with `bulk_create`, which sends none. The first
    scan starts from the newest event, so turning this on never replays history.
    """
    from django.db.models import Max

    from connect_labs.messaging.models import EventCursor
    from connect_labs.workers.models import WorkerEvent

    now = now or timezone.now()
    with transaction.atomic():
        cursor, created = EventCursor.objects.select_for_update().get_or_create(name="state_event")
        if created:
            cursor.last_id = WorkerEvent.objects.aggregate(m=Max("id"))["m"] or 0
            cursor.save(update_fields=["last_id", "updated_at"])
            return 0
        events = list(
            WorkerEvent.objects.filter(id__gt=cursor.last_id, program_id__isnull=False)
            .exclude(kind__in=("message_in", "message_out", "consent_changed"))
            .select_related("worker")
            .order_by("id")[:limit]
        )
        if not events:
            return 0
        cursor.last_id = events[-1].id
        cursor.save(update_fields=["last_id", "updated_at"])
    ran = 0
    if not service.enabled():
        return 0
    for event in events:
        if event.observed_at < now - EVENT_MAX_AGE:
            continue
        try:
            on_worker_event(event)
            ran += 1
        except Exception:  # noqa: BLE001 -- one event's failure never stops the rest
            logger.exception("messaging: trigger for worker event %s failed", event.pk)
    return ran


def due_schedules(now: dt.datetime) -> list[tuple[int, str, dict, str]]:
    """(program_id, key, playbook, period) for every schedule playbook due this hour (UTC)."""
    from connect_labs.scope_config.models import ScopeConfig

    due = []
    rows = ScopeConfig.objects.filter(namespace=playbooks.PLAYBOOKS, scope_type="program")
    for row in rows:
        program_id = int(row.scope_key)
        for key, playbook in ((row.data or {}).get("playbooks") or {}).items():
            trigger = playbook.get("trigger") or {}
            if trigger.get("type") != "schedule" or not playbooks.is_enabled(playbook):
                continue
            if trigger.get("hour") != now.hour:
                continue
            if trigger.get("every") == "weekly":
                if trigger.get("weekday") != now.weekday():
                    continue
                period = f"{now.isocalendar().year}-W{now.isocalendar().week}"
            else:
                period = now.date().isoformat()
            due.append((program_id, key, playbook, period))
    return due


def run_schedules(now: dt.datetime | None = None, limit: int = 500) -> list[dict]:
    """Start every schedule playbook due now, once per period, for its eligible Connect Workers."""
    from connect_labs.messaging.models import PlaybookRun

    now = now or timezone.now()
    out = []
    if not service.enabled():
        return out
    for program_id, key, playbook, period in due_schedules(now):
        if not config.program_enabled(program_id):
            continue
        run, created = PlaybookRun.objects.get_or_create(program_id=program_id, playbook_key=key, period=period)
        if not created:
            continue
        started = blocked = 0
        usernames = (
            WorkerMembership.objects.filter(program_id=program_id, worker__erased_at__isnull=True)
            .values_list("worker__username", flat=True)
            .distinct()[:limit]
        )
        for username in usernames:
            result = start_automatic(program_id=program_id, playbook_key=key, username=username, trigger="schedule")
            if isinstance(result, Thread):
                started += 1
            else:
                blocked += 1
        run.started, run.blocked, run.finished_at = started, blocked, timezone.now()
        run.save(update_fields=["started", "blocked", "finished_at"])
        out.append(
            {"program_id": program_id, "playbook": key, "period": period, "started": started, "blocked": blocked}
        )
    return out


def close_idle(now: dt.datetime | None = None) -> int:
    """Close open threads whose playbook's `exit.idle_hours` has passed with no message."""
    now = now or timezone.now()
    closed = 0
    for thread in Thread.objects.filter(state=ThreadState.OPEN).exclude(playbook_key="").iterator():
        hours = ((playbooks.for_thread(thread) or {}).get("exit") or {}).get("idle_hours")
        last = thread.last_message_at or thread.opened_at
        if hours and last < now - dt.timedelta(hours=hours):
            service.close_thread(thread, f"no messages for {hours} hours", by="exit rule")
            closed += 1
    return closed


def install() -> None:
    """Route the worker's messages through the router (`service.record_inbound` calls these)."""
    service.INBOUND_THREAD = thread_for_inbound
    service.AFTER_INBOUND = after_inbound
