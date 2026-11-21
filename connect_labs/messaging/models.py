"""Labs Messaging: every message labs exchanges with a Connect Worker, and how it got there.

Labs is its own PersonalID message server (`transport/personalid.py`): it creates a channel
per (Connect Worker, program) on connect-id, holds that channel's AES-256 key, sends with
`create_message`, and hears the worker's replies, consent changes and delivery receipts on
four endpoints (`personalid_views.py`).

    Address   one channel to one worker, with its consent history and its key
    Thread    one conversation, with who handles it (agent, flow, person or none) and why it began
    Message   one message, either way, by anyone; stored encrypted, and on the worker's timeline
    ThreadSummary  what a thread came to, for whoever picks the worker up next
    TransportEvent  what connect-id posted to us, kept before any work is done on it

Everything a worker said or was told is stored here, whoever wrote it -- an agent, a flow, a
member of staff or labs itself -- so continuity across threads comes from this record, never
from a chat session somewhere else.

Design: docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md (Part C).
"""

from __future__ import annotations

import uuid

from django.db import models
from django.utils import timezone

from connect_labs.utils.encrypted_fields import EncryptedTextField
from connect_labs.workers.models import ConnectWorker, Visibility


class TransportKind(models.TextChoices):
    PERSONALID = "personalid"


class AddressPurpose(models.TextChoices):
    PROGRAM = "program"  # the default: one channel per (worker, program)
    SECURITY = "security"  # a dedicated channel for one-time codes, kept apart from conversation


class Address(models.Model):
    """One channel to one Connect Worker on one transport.

    `channel_source` is our stable key for the channel (connect-id's `create_channel` is
    idempotent on it per worker); `channel_name` is what the worker's app shows.
    """

    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="addresses")
    transport = models.CharField(max_length=20, choices=TransportKind, default=TransportKind.PERSONALID)
    channel_id = models.CharField(max_length=64, unique=True, null=True, blank=True)
    channel_source = models.CharField(max_length=100)
    channel_name = models.CharField(max_length=255, blank=True, default="")
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    purpose = models.CharField(max_length=20, choices=AddressPurpose, default=AddressPurpose.PROGRAM)
    # What connect-id says: the worker's own switch for this channel in their app.
    consent = models.BooleanField(default=True)
    consent_updated_at = models.DateTimeField(null=True, blank=True)
    consent_history = models.JSONField(default=list, blank=True)  # [{consent, at, source}]
    # The worker asked labs to stop (replied STOP). Labs-side: connect-id's consent is theirs to flip.
    opted_out_at = models.DateTimeField(null=True, blank=True)
    # The channel's AES-256-GCM key, base64. Served only to the worker's own phone (the key endpoint).
    key = EncryptedTextField(blank=True, default="")
    key_served_at = models.DateTimeField(null=True, blank=True)
    # When the first message carrying the AI disclosure went out on this channel.
    disclosed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name_plural = "addresses"
        constraints = [
            models.UniqueConstraint(fields=["worker", "transport", "channel_source"], name="uniq_address_channel"),
        ]

    def __str__(self):
        return f"{self.worker.username} via {self.transport} ({self.channel_source})"

    @property
    def stop_reason(self) -> str | None:
        """Why nothing may be sent on this channel now, or None."""
        if not self.channel_id:
            return "no channel yet"
        if not self.consent:
            return "the Connect Worker turned this channel off"
        if self.opted_out_at:
            return "the Connect Worker asked us to stop"
        return None

    @property
    def can_send(self) -> bool:
        return self.stop_reason is None

    def record_consent(self, consent: bool, *, source: str, at=None) -> bool:
        """Set consent, keeping its history. Returns whether it changed. Caller saves."""
        at = at or timezone.now()
        changed = self.consent != bool(consent)
        self.consent = bool(consent)
        self.consent_updated_at = at
        self.consent_history = [
            *(self.consent_history or []),
            {"consent": bool(consent), "at": at.isoformat(), "source": source},
        ][-100:]
        return changed


class Direction(models.TextChoices):
    INBOUND = "in"  # the worker began it (or wrote first)
    OUTBOUND = "out"  # labs began it


class HandlerKind(models.TextChoices):
    AGENT = "agent"
    FLOW = "flow"
    HUMAN = "human"
    NONE = "none"


class ThreadState(models.TextChoices):
    OPEN = "open"
    CLOSED = "closed"


class Thread(models.Model):
    """One conversation with one worker, and who is handling it.

    A thread's handler (`handler_kind` + `handler_ref`) answers the worker; a person can take
    it over (`taken_over_by`) and hand it back (`previous_handler`). `needs_attention` is
    what puts it at the top of the Inbox.
    """

    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="threads")
    address = models.ForeignKey(Address, null=True, blank=True, on_delete=models.SET_NULL, related_name="threads")
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    opportunity_id = models.IntegerField(null=True, blank=True, db_index=True)
    direction = models.CharField(max_length=3, choices=Direction)
    playbook_key = models.CharField(max_length=64, blank=True, default="", db_index=True)
    playbook_version = models.IntegerField(null=True, blank=True)
    variant = models.CharField(max_length=64, blank=True, default="")
    handler_kind = models.CharField(max_length=10, choices=HandlerKind, default=HandlerKind.HUMAN)
    handler_ref = models.CharField(max_length=200, blank=True, default="")  # agent:skill, flow key, queue
    previous_handler = models.JSONField(default=dict, blank=True)  # {kind, ref} before a takeover
    taken_over_by = models.CharField(max_length=200, blank=True, default="")
    state = models.CharField(max_length=10, choices=ThreadState, default=ThreadState.OPEN, db_index=True)
    needs_attention = models.BooleanField(default=False, db_index=True)
    attention_reason = models.CharField(max_length=300, blank=True, default="")
    opened_at = models.DateTimeField(default=timezone.now)
    closed_at = models.DateTimeField(null=True, blank=True)
    close_reason = models.CharField(max_length=200, blank=True, default="")
    started_by = models.CharField(max_length=200, blank=True, default="")  # person:<u> | worker | system:<why>
    approval = models.JSONField(default=dict, blank=True)  # {mode, approved_by, approved_at, via}
    state_snapshot = models.JSONField(default=dict, blank=True)  # the worker's state when it opened
    context = models.JSONField(default=dict, blank=True)  # what the opener passed (a flow's facts)
    last_message_at = models.DateTimeField(null=True, blank=True)
    # Debounce: when the worker's latest messages are due to be handled.
    inbound_due_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-last_message_at", "-id"]
        indexes = [
            models.Index(fields=["worker", "state"], name="thread_worker_state"),
            models.Index(fields=["program_id", "state", "needs_attention"], name="thread_inbox"),
        ]

    def __str__(self):
        return f"thread {self.pk} with {self.worker.username} ({self.playbook_key or self.direction})"

    @property
    def is_open(self) -> bool:
        return self.state == ThreadState.OPEN

    # The API other code (handlers, the canopy bridge, staff views) uses. See service.py.

    def send(self, text: str, author_kind: str, author_ref: str = "", **kwargs) -> Message:
        from connect_labs.messaging import service

        return service.send(self, text, author_kind=author_kind, author_ref=author_ref, **kwargs)

    def close(self, reason: str, *, by: str = "") -> None:
        from connect_labs.messaging import service

        service.close_thread(self, reason, by=by)


class AuthorKind(models.TextChoices):
    WORKER = "worker"
    AGENT = "agent"
    FLOW = "flow"
    STAFF = "staff"
    SYSTEM = "system"


class DeliveryStatus(models.TextChoices):
    QUEUED = "queued"  # outbound, not yet accepted by the transport
    SENT = "sent"  # accepted by the transport
    DELIVERED = "delivered"  # the worker's phone confirmed it
    FAILED = "failed"
    RECEIVED = "received"  # inbound
    NOTE = "note"  # never sent: a note on the thread for staff (a handoff, a system remark)


def _new_transport_id() -> str:
    return str(uuid.uuid4())


class Message(models.Model):
    """One message in a thread, either way, by anyone.

    An outbound message gets its transport id when it is created, so every retry of the send
    carries the same id and connect-id's MESSAGE_ID_ALREADY_EXISTS means "already delivered".
    """

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="messages")
    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="messages")
    direction = models.CharField(max_length=3, choices=Direction)
    author_kind = models.CharField(max_length=10, choices=AuthorKind)
    author_ref = models.CharField(max_length=200, blank=True, default="")
    text = EncryptedTextField(blank=True, default="")
    attachments = models.JSONField(default=list, blank=True)  # [{name, type, size}]
    transport = models.CharField(max_length=20, choices=TransportKind, default=TransportKind.PERSONALID)
    transport_message_id = models.CharField(
        max_length=64, unique=True, null=True, blank=True, default=_new_transport_id
    )
    delivery_status = models.CharField(max_length=12, choices=DeliveryStatus, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    scheduled_for = models.DateTimeField(null=True, blank=True)  # held back (quiet hours)
    sent_at = models.DateTimeField(null=True, blank=True)
    delivered_at = models.DateTimeField(null=True, blank=True)
    failed_at = models.DateTimeField(null=True, blank=True)
    failure_reason = models.CharField(max_length=300, blank=True, default="")
    attempts = models.IntegerField(default=0)
    # Inbound: when its handler was given it (debounced, so several arrive together).
    handled_at = models.DateTimeField(null=True, blank=True)
    canopy_turn_id = models.CharField(max_length=64, blank=True, default="")
    canopy_session_id = models.CharField(max_length=64, blank=True, default="")
    visibility = models.CharField(max_length=10, choices=Visibility, default=Visibility.NORMAL)
    brief_hash = models.CharField(max_length=64, blank=True, default="")

    class Meta:
        ordering = ["created_at", "id"]
        indexes = [models.Index(fields=["thread", "created_at"], name="message_thread_time")]

    def __str__(self):
        return f"{self.direction} {self.author_kind} message {self.pk} on thread {self.thread_id}"


class ThreadSummary(models.Model):
    """What a thread came to, written when it closes (or along the way) for whoever is next."""

    thread = models.ForeignKey(Thread, on_delete=models.CASCADE, related_name="summaries")
    text = EncryptedTextField()
    author_kind = models.CharField(max_length=10, choices=AuthorKind)
    author_ref = models.CharField(max_length=200, blank=True, default="")
    through_message = models.ForeignKey(Message, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    outcome = models.CharField(max_length=64, blank=True, default="")
    visibility = models.CharField(max_length=10, choices=Visibility, default=Visibility.NORMAL)
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        ordering = ["-created_at", "-id"]


class TransportEventKind(models.TextChoices):
    DELIVERY = "delivery"  # the worker wrote to us
    CONSENT = "consent"  # the worker turned the channel on or off
    CALLBACK = "callback"  # the worker's phone received our messages


class TransportEvent(models.Model):
    """What connect-id posted to us, kept as it came (ciphertext only) before any work is done.

    connect-id never retries a delivery, so its endpoint stores this and answers 200 at once;
    `tasks.process_transport_event` does the work, and the sweep picks up any it missed.
    """

    transport = models.CharField(max_length=20, choices=TransportKind, default=TransportKind.PERSONALID)
    kind = models.CharField(max_length=10, choices=TransportEventKind)
    channel_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    payload = models.JSONField(default=dict)
    received_at = models.DateTimeField(default=timezone.now)
    processed_at = models.DateTimeField(null=True, blank=True, db_index=True)
    attempts = models.IntegerField(default=0)
    error = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        ordering = ["received_at", "id"]


class PlaybookRun(models.Model):
    """A scheduled playbook's run for one period (a day or an ISO week): it runs once per period."""

    program_id = models.IntegerField()
    playbook_key = models.CharField(max_length=64)
    period = models.CharField(max_length=20)
    started_at = models.DateTimeField(default=timezone.now)
    finished_at = models.DateTimeField(null=True, blank=True)
    started = models.IntegerField(default=0)
    blocked = models.IntegerField(default=0)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["program_id", "playbook_key", "period"], name="uniq_playbook_run_period"),
        ]


class EventCursor(models.Model):
    """How far Messaging has read the workers' timeline for state-event triggers (`router.scan_worker_events`)."""

    name = models.CharField(max_length=40, unique=True)
    last_id = models.BigIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)
