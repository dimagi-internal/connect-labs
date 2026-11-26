"""The one door for every message: sending, hearing, consent, closing.

Everything that sends to a worker -- a flow, a member of staff in the Inbox, the canopy
bridge's agent -- calls `send` (or `Thread.send`, the same thing). Everything we hear from a
worker comes in through `record_inbound`. Both store the message (encrypted) and write it to
the worker's timeline as a `WorkerEvent`, so a worker's record holds every message from
every author.

The rails live here, so no handler can skip them:

* Nothing is sent while `MESSAGING_ENABLED` is off, or while the program's own switch is
  off (`config.program_enabled`).
* Nothing is sent on a channel the worker turned off (consent) or asked us to stop (STOP);
  either closes the worker's open threads on that channel.
* The first message on a channel carries the AI disclosure and how to stop.
* Bare URLs become Markdown links (they are not tappable otherwise).
* Sending happens in the background (`tasks.deliver_message`), with the message's own id as
  the idempotency key, so a retry never sends twice.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from django.conf import settings
from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_datetime

from connect_labs.messaging import disclosure
from connect_labs.messaging.models import (
    Address,
    AddressPurpose,
    AuthorKind,
    DeliveryStatus,
    Direction,
    HandlerKind,
    Message,
    Thread,
    ThreadState,
    TransportKind,
)
from connect_labs.workers.models import ConnectWorker, Visibility, WorkerEvent

logger = logging.getLogger(__name__)

MAX_TEXT = 4000
#: A card is one self-contained page; this keeps it far inside PersonalID's 2.5 MiB per attachment.
MAX_CARD_BYTES = 256 * 1024
CARD_ENTRY_NAME = "index.html"

#: The `author_kind`s that are a handler's automated turn (counted against a playbook's max_turns).
AUTOMATED_AUTHORS = frozenset({AuthorKind.AGENT, AuthorKind.FLOW})


class MessagingError(Exception):
    """Something Messaging will not do. `code` is stable; the message is for a person."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.public_message = message
        super().__init__(message)


def enabled() -> bool:
    return bool(getattr(settings, "MESSAGING_ENABLED", False))


def require_enabled() -> None:
    if not enabled():
        raise MessagingError("messaging_off", "Messaging is turned off on this labs server.")


def program_enabled(program_id) -> bool:
    """The program's own switch (Settings › messaging.settings › enabled). Off unless turned on."""
    from connect_labs.messaging import config

    return config.program_enabled(program_id)


def require_program_enabled(program_id) -> None:
    if program_id is not None and not program_enabled(program_id):
        raise MessagingError(
            "program_off",
            f"Messaging is turned off for program {program_id} (Settings › Messaging › enabled).",
        )


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


def channel_source(program_id, purpose: str = AddressPurpose.PROGRAM) -> str:
    """Our stable key for a channel: one per (worker, program), plus dedicated ones by purpose."""
    if purpose == AddressPurpose.PROGRAM:
        return f"labs-program-{program_id}" if program_id is not None else "labs"
    return f"labs-{purpose}" + (f"-{program_id}" if program_id is not None else "")


def address_for(
    worker: ConnectWorker,
    program_id: int | None,
    *,
    purpose: str = AddressPurpose.PROGRAM,
    channel_name: str = "",
    create_channel: bool = True,
) -> Address:
    """The worker's channel for this program (or purpose), made on PersonalID the first time."""
    from connect_labs.messaging import crypto
    from connect_labs.messaging.transport import get_transport

    source = channel_source(program_id, purpose)
    # A test worker on a simulated channel stays on it: nothing for them reaches PersonalID.
    simulated = Address.objects.filter(worker=worker, transport=TransportKind.SIMULATED, channel_source=source).first()
    if simulated is not None:
        return simulated
    address, _ = Address.objects.get_or_create(
        worker=worker,
        transport=TransportKind.PERSONALID,
        channel_source=source,
        defaults={
            "program_id": program_id,
            "purpose": purpose,
            "channel_name": channel_name[:255],
            "key": crypto.new_key(),
        },
    )
    if create_channel and not address.channel_id:
        require_enabled()
        get_transport(address.transport).ensure_channel(address)
    return address


# ---------------------------------------------------------------------------
# Threads
# ---------------------------------------------------------------------------


def open_thread(
    worker: ConnectWorker,
    *,
    address: Address | None,
    direction: str,
    program_id: int | None = None,
    opportunity_id: int | None = None,
    handler_kind: str = HandlerKind.HUMAN,
    handler_ref: str = "",
    playbook_key: str = "",
    playbook_version: int | None = None,
    variant: str = "",
    started_by: str = "",
    approval: dict | None = None,
    state_snapshot: dict | None = None,
    context: dict | None = None,
    needs_attention: bool = False,
    attention_reason: str = "",
) -> Thread:
    return Thread.objects.create(
        worker=worker,
        address=address,
        program_id=program_id if program_id is not None else getattr(address, "program_id", None),
        opportunity_id=opportunity_id,
        direction=direction,
        handler_kind=handler_kind,
        handler_ref=handler_ref,
        playbook_key=playbook_key,
        playbook_version=playbook_version,
        variant=variant,
        started_by=started_by[:200],
        approval=approval or {},
        state_snapshot=state_snapshot or {},
        context=context or {},
        needs_attention=needs_attention,
        attention_reason=attention_reason[:300],
    )


def close_thread(thread: Thread, reason: str, *, by: str = "") -> None:
    """Close a thread, telling its handler to stop. Closing a closed thread does nothing."""
    from connect_labs.messaging import handlers

    with transaction.atomic():
        locked = Thread.objects.select_for_update().get(pk=thread.pk)
        if locked.state == ThreadState.CLOSED:
            return
        locked.state = ThreadState.CLOSED
        locked.closed_at = timezone.now()
        locked.close_reason = f"{reason}{f' (by {by})' if by else ''}"[:200]
        locked.needs_attention = False
        locked.inbound_due_at = None
        locked.save(update_fields=["state", "closed_at", "close_reason", "needs_attention", "inbound_due_at"])
    handlers.stop(locked, reason)
    thread.refresh_from_db()


def open_threads_on(address: Address):
    return Thread.objects.filter(address=address, state=ThreadState.OPEN)


# ---------------------------------------------------------------------------
# Sending
# ---------------------------------------------------------------------------


def sender_name(address: Address | None) -> str:
    return (address.channel_name if address else "") or "your program"


def send(
    thread: Thread,
    text: str,
    *,
    author_kind: str,
    author_ref: str = "",
    visibility: str = Visibility.NORMAL,
    scheduled_for: datetime | None = None,
    canopy_turn_id: str = "",
    canopy_session_id: str = "",
    brief_hash: str = "",
    attachments: list | None = None,
    card_html: str = "",
) -> Message:
    """Store an outbound message on `thread` and queue it for delivery. Returns the Message.

    With `card_html` the message is a card: the app shows that page inline and `text` (if any)
    is its caption.

    Raises `MessagingError` (with a stable `code`) when it may not be sent: messaging or the
    program is off, the thread is closed, the worker turned the channel off or asked us to
    stop, or (for an agent or flow) the playbook's turn limit is reached.
    """
    if author_kind not in AuthorKind.values or author_kind == AuthorKind.WORKER:
        raise MessagingError("invalid", f"cannot send as {author_kind!r}")
    text = (text or "").strip()
    card_html = (card_html or "").strip()
    if card_html:
        size = len(card_html.encode())
        if size > MAX_CARD_BYTES:
            raise MessagingError("invalid", f"a card is at most {MAX_CARD_BYTES // 1024} KB")
        attachments = [{"name": CARD_ENTRY_NAME, "type": "text/html", "size": size}]
    if not text and not attachments:
        raise MessagingError("invalid", "a message needs text")
    if len(text) > MAX_TEXT:
        raise MessagingError("invalid", f"a message is at most {MAX_TEXT} characters")
    require_enabled()
    require_program_enabled(thread.program_id)

    with transaction.atomic():
        thread = Thread.objects.select_for_update(of=("self",)).select_related("address", "worker").get(pk=thread.pk)
        if thread.state != ThreadState.OPEN:
            raise MessagingError("thread_closed", "This conversation is closed.")
        if author_kind in AUTOMATED_AUTHORS:
            # Under the thread's lock: two agent replies at once cannot both pass the limit.
            _check_turn_limit(thread)
        address = Address.objects.select_for_update().get(pk=thread.address_id) if thread.address_id else None
        if address is None:
            raise MessagingError("no_channel", "This conversation has no channel to send on.")
        reason = address.stop_reason
        if reason:
            raise MessagingError("not_reachable", f"Not sent: {reason}.")
        body = disclosure.linkify(text)
        now = timezone.now()
        if address.disclosed_at is None:
            body = disclosure.with_disclosure(body, sender_name(address))
            address.disclosed_at = now
            address.save(update_fields=["disclosed_at", "updated_at"])
        message = Message.objects.create(
            thread=thread,
            worker=thread.worker,
            direction=Direction.OUTBOUND,
            author_kind=author_kind,
            author_ref=author_ref[:200],
            text=body,
            attachments=attachments or [],
            card_html=card_html,
            transport=address.transport,
            delivery_status=DeliveryStatus.QUEUED,
            created_at=now,
            scheduled_for=scheduled_for if scheduled_for and scheduled_for > now else None,
            canopy_turn_id=canopy_turn_id[:64],
            canopy_session_id=canopy_session_id[:64],
            visibility=visibility,
            brief_hash=brief_hash[:64],
        )
        thread.last_message_at = now
        thread.save(update_fields=["last_message_at"])
        _write_timeline(message)
        transaction.on_commit(lambda: enqueue_delivery(message))
    return message


def add_note(thread: Thread, text: str, *, author_kind: str = AuthorKind.SYSTEM, author_ref: str = "") -> Message:
    """A note on the thread for staff (a handoff's reason, a takeover). Never sent to the worker."""
    return Message.objects.create(
        thread=thread,
        worker=thread.worker,
        direction=Direction.OUTBOUND,
        author_kind=author_kind,
        author_ref=author_ref[:200],
        text=(text or "")[:MAX_TEXT],
        transport_message_id=None,
        delivery_status=DeliveryStatus.NOTE,
        visibility=Visibility.RESTRICTED,
    )


def record_summary(
    thread: Thread,
    text: str,
    *,
    author_kind: str,
    author_ref: str = "",
    outcome: str = "",
    visibility: str = Visibility.NORMAL,
):
    """What a thread came to (an agent's `thread_record_outcome`, a person's note), for whoever is next."""
    from connect_labs.messaging.models import ThreadSummary

    text = (text or "").strip()
    if not text:
        raise MessagingError("invalid", "a summary needs text")
    last = thread.messages.exclude(delivery_status=DeliveryStatus.NOTE).order_by("-created_at", "-id").first()
    return ThreadSummary.objects.create(
        thread=thread,
        text=text[:MAX_TEXT],
        author_kind=author_kind,
        author_ref=author_ref[:200],
        outcome=outcome[:64],
        visibility=visibility,
        through_message=last,
    )


def automated_turns(thread: Thread) -> int:
    return (
        thread.messages.filter(direction=Direction.OUTBOUND, author_kind__in=AUTOMATED_AUTHORS)
        .exclude(delivery_status=DeliveryStatus.NOTE)
        .count()
    )


def _check_turn_limit(thread: Thread) -> None:
    from connect_labs.messaging import config

    max_turns = config.max_turns_for(thread)
    if max_turns is not None and automated_turns(thread) >= max_turns:
        raise MessagingError(
            "turn_limit", f"This conversation has reached its playbook's limit of {max_turns} automated messages."
        )


def enqueue_delivery(message: Message) -> None:
    """Queue the send. A broker that is down is logged; the sweep sends what was left queued."""
    from connect_labs.messaging import tasks

    try:
        if message.scheduled_for:
            tasks.deliver_message.apply_async(args=[message.pk], eta=message.scheduled_for)
        else:
            tasks.deliver_message.delay(message.pk)
    except Exception:  # noqa: BLE001 -- the sweep picks up anything left queued
        logger.warning("messaging: could not queue delivery of message %s", message.pk, exc_info=True)


def _fail(message: Message, reason: str) -> None:
    message.delivery_status = DeliveryStatus.FAILED
    message.failed_at = timezone.now()
    message.failure_reason = reason[:300]
    message.save(update_fields=["delivery_status", "failed_at", "failure_reason"])
    thread = message.thread
    if thread.state == ThreadState.OPEN:
        Thread.objects.filter(pk=thread.pk).update(
            needs_attention=True, attention_reason=f"Not delivered: {reason}"[:300]
        )


def deliver(message_id: int) -> str:
    """Send one queued message. Returns what happened; raises `TransportUnavailable` to be retried."""
    from connect_labs.messaging.transport import ChannelGone, ConsentRefused, TransportError, get_transport

    with transaction.atomic():
        message = (
            Message.objects.select_for_update(of=("self",))
            .select_related("thread__address__worker", "worker")
            .filter(pk=message_id)
            .first()
        )
        if message is None or message.delivery_status != DeliveryStatus.QUEUED:
            return "skipped"
        if message.scheduled_for and message.scheduled_for > timezone.now():
            return "later"
        address = message.thread.address
        if not enabled():
            _fail(message, "messaging is turned off")
            return "failed"
        if message.thread.program_id is not None and not program_enabled(message.thread.program_id):
            _fail(message, "messaging is turned off for this program")
            return "failed"
        reason = address.stop_reason if address else "no channel"
        if reason:
            _fail(message, reason)
            return "failed"
        message.attempts += 1
        message.save(update_fields=["attempts"])

    try:
        get_transport(message.transport).send(message)
    except ConsentRefused:
        _fail(message, "the Connect Worker has turned this channel off")
        set_consent(address, False, source="create_message")
        return "failed"
    except ChannelGone as e:
        _fail(message, str(e))
        return "failed"
    except TransportError as e:
        if e.retryable:
            raise
        _fail(message, str(e))
        return "failed"
    Message.objects.filter(pk=message.pk, delivery_status=DeliveryStatus.QUEUED).update(
        delivery_status=DeliveryStatus.SENT, sent_at=timezone.now()
    )
    return "sent"


def give_up(message_id: int, reason: str) -> None:
    message = (
        Message.objects.select_related("thread").filter(pk=message_id, delivery_status=DeliveryStatus.QUEUED).first()
    )
    if message is not None:
        _fail(message, reason)


# ---------------------------------------------------------------------------
# Hearing
# ---------------------------------------------------------------------------


def _default_inbound_thread(address: Address, text: str) -> tuple[Thread, bool]:
    """The open thread on this channel, else a new one for a person (the Inbox). (thread, is_new)"""
    thread = open_threads_on(address).order_by("-opened_at", "-id").first()
    if thread is not None:
        return thread, False
    return (
        open_thread(
            address.worker,
            address=address,
            direction=Direction.INBOUND,
            handler_kind=HandlerKind.HUMAN,
            started_by="worker",
            needs_attention=True,
            attention_reason="The Connect Worker wrote in",
        ),
        True,
    )


#: Chooses the thread a worker's message belongs to. The router (`router.thread_for_inbound`)
#: replaces this when it is installed; see `apps.py`.
INBOUND_THREAD: Callable[[Address, str], tuple[Thread, bool]] = _default_inbound_thread
#: Called after a worker's message is stored (debounce + handler); set by the router.
AFTER_INBOUND: Callable[[Thread, Message, bool], None] | None = None


def record_inbound(
    address: Address,
    *,
    transport_message_id: str,
    text: str,
    sent_at: datetime | None = None,
    attachments: list | None = None,
) -> Message | None:
    """Store a message the worker sent. Returns it, or None if we already had it (a replay).

    A STOP keyword turns the channel off from labs' side and closes its open threads; START
    turns it back on. Either way the message itself is stored.

    Everything here runs under a lock on the channel: two posts for one channel (two Celery
    workers) are placed one after the other, so they cannot both open a thread, and STOP/START
    read the channel as it is now, not as the caller last loaded it. A start
    (`router.starting.open_and_send`) takes the same lock.
    """
    now = timezone.now()
    with transaction.atomic():
        address = Address.objects.select_for_update(of=("self",)).select_related("worker").get(pk=address.pk)
        if Message.objects.filter(transport_message_id=transport_message_id).exists():
            return None
        if disclosure.is_stop(text) and address.opted_out_at is None:
            Address.objects.filter(pk=address.pk).update(opted_out_at=now)
            address.opted_out_at = now
            stopping = True
        else:
            stopping = False
        if disclosure.is_start(text) and address.opted_out_at is not None:
            Address.objects.filter(pk=address.pk).update(opted_out_at=None)
            address.opted_out_at = None
        thread, is_new = INBOUND_THREAD(address, text)
        message = Message.objects.create(
            thread=thread,
            worker=address.worker,
            direction=Direction.INBOUND,
            author_kind=AuthorKind.WORKER,
            author_ref=address.worker.username,
            text=text,
            attachments=attachments or [],
            transport=address.transport,
            transport_message_id=transport_message_id,
            delivery_status=DeliveryStatus.RECEIVED,
            created_at=sent_at or now,
        )
        Thread.objects.filter(pk=thread.pk).update(last_message_at=now)
        _write_timeline(message)
    if stopping:
        for open_one in open_threads_on(address):
            close_thread(open_one, "unsubscribed", by="the Connect Worker (STOP)")
        return message
    if thread.state != ThreadState.OPEN:
        return message
    if AFTER_INBOUND is not None:
        AFTER_INBOUND(thread, message, is_new)
    else:
        from connect_labs.messaging import handlers

        thread.refresh_from_db()
        if is_new:
            handlers.start(thread, {})
        else:
            handlers.on_message(thread, [message])
        Message.objects.filter(pk=message.pk).update(handled_at=timezone.now())
    return message


def record_receipts(address: Address, receipts: list[dict]) -> int:
    """The worker's phone received these of our messages. Returns how many were updated."""
    updated = 0
    for item in receipts:
        message_id = item.get("message_id")
        if not message_id:
            continue
        received = parse_datetime(str(item.get("received_on") or "")) or timezone.now()
        if timezone.is_naive(received):
            received = timezone.make_aware(received, UTC)
        updated += Message.objects.filter(
            transport_message_id=str(message_id),
            thread__address=address,
            direction=Direction.OUTBOUND,
            delivery_status__in=[DeliveryStatus.QUEUED, DeliveryStatus.SENT],
        ).update(delivery_status=DeliveryStatus.DELIVERED, delivered_at=received)
    return updated


def set_consent(address: Address, consent: bool, *, source: str) -> None:
    """Record the worker's switch for this channel. Turning it off closes its open threads."""
    with transaction.atomic():
        locked = Address.objects.select_for_update().get(pk=address.pk)
        changed = locked.record_consent(consent, source=source)
        locked.save(update_fields=["consent", "consent_updated_at", "consent_history", "updated_at"])
        if changed:
            WorkerEvent.objects.get_or_create(
                dedupe_key=f"messaging:consent:{locked.pk}:{len(locked.consent_history)}",
                defaults={
                    "worker_id": locked.worker_id,
                    "program_id": locked.program_id,
                    "kind": "consent_changed",
                    "occurred_at": locked.consent_updated_at,
                    "source": "personalid",
                    "ref": {"address_id": locked.pk, "channel_id": locked.channel_id},
                    "payload": {"consent": bool(consent), "source": source},
                },
            )
    address.refresh_from_db()
    if not consent:
        for thread in open_threads_on(address):
            close_thread(thread, "unsubscribed", by="the Connect Worker (turned the channel off)")


# ---------------------------------------------------------------------------
# The worker's timeline
# ---------------------------------------------------------------------------


def _write_timeline(message: Message) -> None:
    inbound = message.direction == Direction.INBOUND
    WorkerEvent.objects.get_or_create(
        dedupe_key=f"messaging:message:{message.pk}",
        defaults={
            "worker_id": message.worker_id,
            "opportunity_id": message.thread.opportunity_id,
            "program_id": message.thread.program_id,
            "kind": "message_in" if inbound else "message_out",
            "occurred_at": message.created_at,
            "source": "personalid" if inbound else "labs",
            "visibility": message.visibility,
            "ref": {"thread_id": message.thread_id, "message_id": message.pk},
            # Who and how much, never the text: the timeline points at the message.
            "payload": {
                "author_kind": message.author_kind,
                "author_ref": message.author_ref,
                "playbook": message.thread.playbook_key,
                "chars": len(message.text or ""),
            },
        },
    )


# ---------------------------------------------------------------------------
# What connect-id posted (tasks.process_transport_event)
# ---------------------------------------------------------------------------


def _consent_value(value) -> bool:
    """connect-id's consent flag, read strictly: only a true value (or "true" / "1") turns sending on.

    ``bool("false")`` is True, and a consent post is the worker's opt-out switch.
    """
    if isinstance(value, str):
        return value.strip().lower() in ("true", "1")
    return value is True or value == 1


def process_event(event) -> str:
    """Do the work for one stored `TransportEvent`. Raises on a failure worth retrying.

    Returns what could not be done but is not worth retrying (a message that cannot be
    decrypted), for the event's `error`. It does not raise for that: raising would roll back
    the messages of the same post that WERE stored, and connect-id never sends them again.
    """
    from connect_labs.messaging import crypto
    from connect_labs.messaging.models import TransportEventKind

    address = (
        Address.objects.select_related("worker").filter(channel_id=event.channel_id, transport=event.transport).first()
        if event.channel_id
        else None
    )
    if address is None:
        raise LookupError(f"no channel {event.channel_id!r} of ours")
    payload = event.payload or {}
    if event.kind == TransportEventKind.CONSENT:
        set_consent(address, _consent_value(payload.get("consent")), source="personalid")
    elif event.kind == TransportEventKind.CALLBACK:
        record_receipts(address, payload.get("messages") or [])
    elif event.kind == TransportEventKind.DELIVERY:
        problems = []
        seen = set()
        for item in payload.get("messages") or []:
            message_id = str(item.get("message_id") or "")
            if not message_id or message_id in seen:
                continue
            seen.add(message_id)
            try:
                text = crypto.decrypt_text(address.key, item)
            except crypto.DecryptionError as e:
                problems.append(f"{message_id}: cannot decrypt ({e})")
                continue
            sent_at = parse_datetime(str(item.get("timestamp") or ""))
            if sent_at is not None and timezone.is_naive(sent_at):
                sent_at = timezone.make_aware(sent_at, UTC)
            record_inbound(address, transport_message_id=message_id, text=text, sent_at=sent_at)
        return "; ".join(problems)[:480]
    return ""
