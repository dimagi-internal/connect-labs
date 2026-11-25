"""What the canopy bridge keeps: labs' side of each Connect Worker's anchor in canopy.

Labs is the system of record. Canopy holds an anchor (the contact, keyed ``personalid:<username>``),
a pointer (``labs-worker://<username>``) and a small profile cache labs keeps current; these rows
are labs' record of what it last told canopy.
"""

from __future__ import annotations

from django.db import models
from django.utils import timezone

from connect_labs.utils.encrypted_fields import EncryptedTextField
from connect_labs.workers.models import ConnectWorker


class PushStatus(models.TextChoices):
    PENDING = "pending"
    CREATED = "created"
    UPDATED = "updated"
    STALE = "stale"
    REFUSED = "refused"
    ERROR = "error"


class ProfilePush(models.Model):
    """The last profile labs pushed to canopy for one Connect Worker (S2), and whether it is due again.

    ``dirty`` is set when something the profile is made of may have changed (a stage, a channel, the
    worker's language); the sweep recomputes the profile and pushes only if its hash changed.
    """

    worker = models.OneToOneField(ConnectWorker, on_delete=models.CASCADE, related_name="canopy_profile_push")
    profile_hash = models.CharField(max_length=64, blank=True, default="")
    pushed_version = models.BigIntegerField(default=0)
    pushed_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=10, choices=PushStatus, default=PushStatus.PENDING)
    reason = models.CharField(max_length=300, blank=True, default="")
    dirty = models.BooleanField(default=True, db_index=True)
    attempts = models.IntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"profile push for {self.worker.username}: {self.status}"


class CanopySession(models.Model):
    """The canopy contact session an agent answers one thread in: one per (thread, agent), opened
    when the agent first has something to answer. Handing the thread to another agent ends this
    one and starts a new row, seeded with the handoff note; continuity comes from labs' record
    (the Worker Brief), never from resuming a canopy session.

    ``session_id`` is "" until the session is opened on canopy.
    """

    thread = models.ForeignKey("messaging.Thread", on_delete=models.CASCADE, related_name="canopy_sessions")
    session_id = models.CharField(max_length=64, blank=True, default="", db_index=True)
    agent = models.CharField(max_length=100)
    skill = models.CharField(max_length=100, blank=True, default="")
    sub = models.CharField(max_length=200)
    seed_note = models.CharField(max_length=600, blank=True, default="")
    handed_from = models.CharField(max_length=200, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)
    opened_at = models.DateTimeField(null=True, blank=True)
    ended_at = models.DateTimeField(null=True, blank=True)
    end_reason = models.CharField(max_length=200, blank=True, default="")
    #: The highest canopy transcript index labs has read (the polling fallback reads past it).
    last_index = models.IntegerField(default=0)

    class Meta:
        ordering = ["-created_at", "-id"]

    def __str__(self):
        return f"canopy session {self.session_id or '(not opened)'} for thread {self.thread_id} ({self.agent})"

    @property
    def is_open(self) -> bool:
        return self.ended_at is None


class TurnStatus(models.TextChoices):
    PENDING = "pending"  # sent; the agent's answer has not come back
    DELIVERED = "delivered"  # answered (by the webhook or the poll)
    FAILED = "failed"  # canopy says the turn failed: a person took the thread
    TIMED_OUT = "timed_out"  # no answer in time: a person took the thread
    CANCELLED = "cancelled"  # the thread closed before the answer came: it is never sent


class PendingTurn(models.Model):
    """One send to the agent and the answer labs is waiting for. The webhook and the poll both
    deliver through it, so a reply is sent to the worker once whichever arrives first."""

    session = models.ForeignKey(CanopySession, on_delete=models.CASCADE, related_name="turns")
    thread = models.ForeignKey("messaging.Thread", on_delete=models.CASCADE, related_name="canopy_turns")
    turn_id = models.CharField(max_length=64, unique=True)
    brief_hash = models.CharField(max_length=64, blank=True, default="")
    sent_at = models.DateTimeField(default=timezone.now)
    status = models.CharField(max_length=10, choices=TurnStatus, default=TurnStatus.PENDING, db_index=True)
    polls = models.IntegerField(default=0)
    next_poll_at = models.DateTimeField(null=True, blank=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    detail = models.CharField(max_length=300, blank=True, default="")
    #: What the agent asked to happen once its answer is out: [{"action": "close" | "handoff", ...}].
    after = models.JSONField(default=list, blank=True)

    class Meta:
        ordering = ["-sent_at", "-id"]

    def __str__(self):
        return f"turn {self.turn_id} on thread {self.thread_id}: {self.status}"


class ReplyDelivery(models.Model):
    """One piece of an agent's answer, delivered once: canopy's push is exactly-once per
    (turn, seq), and a retry of the same piece is recognised here."""

    turn = models.ForeignKey(PendingTurn, on_delete=models.CASCADE, related_name="deliveries")
    seq = models.IntegerField()
    via = models.CharField(max_length=10)  # webhook | poll
    text_hash = models.CharField(max_length=64, blank=True, default="")
    message = models.ForeignKey(
        "messaging.Message", null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    ended = models.BooleanField(default=False)
    error = models.CharField(max_length=300, blank=True, default="")
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        constraints = [models.UniqueConstraint(fields=["turn", "seq"], name="uniq_reply_delivery_turn_seq")]


class WorkerBrief(models.Model):
    """Exactly what an agent was shown about a Connect Worker with one send: the Worker Brief.

    Kept by its hash (``Message.brief_hash`` on the messages sent with it and on the agent's
    answer), so a member of staff can see what the agent saw.
    """

    hash = models.CharField(max_length=64, unique=True)
    body = EncryptedTextField()
    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="canopy_briefs")
    thread = models.ForeignKey("messaging.Thread", null=True, blank=True, on_delete=models.CASCADE, related_name="+")
    sections = models.JSONField(default=dict, blank=True)  # {section: characters}
    created_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"brief {self.hash[:12]} for {self.worker.username}"


class CommitmentStatus(models.TextChoices):
    OPEN = "open"
    DONE = "done"
    DROPPED = "dropped"


class WorkerCommitment(models.Model):
    """Something promised in a conversation that is still to happen ("I'll check your payment by
    Friday"), recorded when an agent closes a thread, shown in every later brief until done."""

    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="commitments")
    thread = models.ForeignKey("messaging.Thread", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    text = models.CharField(max_length=300)
    by = models.CharField(max_length=200, blank=True, default="")
    status = models.CharField(max_length=10, choices=CommitmentStatus, default=CommitmentStatus.OPEN, db_index=True)
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["created_at", "id"]


class Erasure(models.Model):
    """A Connect Worker erased in labs, and whether canopy has forgotten them too (S6).

    Labs keeps only this row and the worker's username (so the sync never writes their record
    again): what was erased is counted, never kept."""

    username = models.CharField(max_length=150, db_index=True)
    sub = models.CharField(max_length=200)
    requested_by = models.CharField(max_length=200)
    requested_at = models.DateTimeField(default=timezone.now)
    labs_counts = models.JSONField(default=dict, blank=True)
    canopy_status = models.CharField(
        max_length=20, blank=True, default="pending"
    )  # pending|erased|not_found|error|off
    canopy_detail = models.CharField(max_length=300, blank=True, default="")
    canopy_done_at = models.DateTimeField(null=True, blank=True)
    attempts = models.IntegerField(default=0)

    def __str__(self):
        return f"erasure of {self.username} (canopy: {self.canopy_status})"
