"""Connect Workers: labs' system of record for the people Connect calls "Connect Workers".

Connect's UI names its mobile users **Connect Workers** (the "Connect Workers" tab,
"Select a Connect Worker"); its code says `worker`, and the export API says
`username`. That username IS the person's PersonalID username (Connect invites
`update_or_create(username=...)` from ConnectID), so it is the key here. Connect's own
`user_id` UUID is Connect-local and appears only in `user_visits`; it is kept, not keyed on.

In the labs DB, not LabsRecords: what labs learns and exchanges with a worker
(their assembled profile, timeline, facts, staff notes, and -- in `messaging` -- every
message) originates in labs or is assembled from several Connect exports, has no single
Connect counterpart, and is read on every message. Canopy holds an anchor and a cache
that point here (`record_uri`), never the record itself.

Design: docs/superpowers/specs/2026-11-21-connect-worker-messaging-design.md
"""

from django.db import models
from django.utils import timezone

from connect_labs.utils.encrypted_fields import EncryptedTextField

PERSON_SCHEME = "labs-worker"
CANOPY_SUB_PREFIX = "personalid:"


class ConnectWorker(models.Model):
    username = models.CharField(max_length=150, unique=True, help_text="PersonalID username (Connect `username`).")
    connect_user_id = models.UUIDField(
        null=True, blank=True, help_text="Connect-local User.user_id, from user_visits."
    )
    name = models.CharField(max_length=255, blank=True, default="")
    phone = EncryptedTextField(blank=True, default="", help_text="Never shown to an agent.")
    languages = models.JSONField(default=list, blank=True)
    timezone = models.CharField(max_length=64, blank=True, default="")
    canopy_contact_id = models.CharField(max_length=64, blank=True, default="")
    canopy_profile_version = models.BigIntegerField(default=0)
    profile_synced_at = models.DateTimeField(null=True, blank=True)
    erased_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "Connect Worker"

    def __str__(self):
        return f"{self.name or self.username} ({self.username})"

    @property
    def canopy_sub(self) -> str:
        return f"{CANOPY_SUB_PREFIX}{self.username}"

    @property
    def record_uri(self) -> str:
        return f"{PERSON_SCHEME}://{self.username}"

    @classmethod
    def username_from_sub(cls, sub: str) -> str | None:
        if not sub or not sub.startswith(CANOPY_SUB_PREFIX):
            return None
        return sub[len(CANOPY_SUB_PREFIX) :] or None


class WorkerMembership(models.Model):
    """One worker on one opportunity, as Connect's `user_data` export last described it."""

    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="memberships")
    opportunity_id = models.IntegerField(db_index=True)
    opportunity_name = models.CharField(max_length=255, blank=True, default="")
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    organization_slug = models.CharField(max_length=255, blank=True, default="")
    user_invite_status = models.CharField(max_length=40, blank=True, default="")
    invited_date = models.DateTimeField(null=True, blank=True)
    date_learn_started = models.DateTimeField(null=True, blank=True)
    completed_learn_date = models.DateTimeField(null=True, blank=True)
    date_claimed = models.DateField(null=True, blank=True)
    last_active = models.DateTimeField(null=True, blank=True)
    payment_accrued = models.IntegerField(default=0)
    suspended = models.BooleanField(default=False)
    suspension_date = models.DateTimeField(null=True, blank=True)
    suspension_reason = EncryptedTextField(blank=True, default="", help_text="Restricted: staff only.")
    claim_limits = models.JSONField(default=list, blank=True)
    # The last user_data row as received, and its hash, so the sync writes events from diffs.
    snapshot = models.JSONField(default=dict, blank=True)
    snapshot_hash = models.CharField(max_length=64, blank=True, default="")
    first_seen_at = models.DateTimeField(default=timezone.now)
    last_synced_at = models.DateTimeField(null=True, blank=True)
    # False once the sync identity can no longer see this opportunity (coverage, not deletion).
    visible = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["worker", "opportunity_id"], name="uniq_worker_membership"),
        ]

    def __str__(self):
        return f"{self.worker.username} @ opp {self.opportunity_id}"


class Stage(models.TextChoices):
    SUSPENDED = "suspended"
    INVITED = "invited"
    NOT_STARTED = "not_started"
    LEARNING = "learning"
    READY_TO_CLAIM = "ready_to_claim"
    AT_CAP = "at_cap"
    DELIVERING = "delivering"
    INACTIVE = "inactive"


class WorkerState(models.Model):
    membership = models.OneToOneField(WorkerMembership, on_delete=models.CASCADE, related_name="state")
    stage = models.CharField(max_length=20, choices=Stage)
    facets = models.JSONField(default=dict, blank=True)
    computed_at = models.DateTimeField(default=timezone.now)

    def __str__(self):
        return f"{self.membership}: {self.stage}"


class Visibility(models.TextChoices):
    NORMAL = "normal"
    RESTRICTED = "restricted"  # staff only: never in a brief or an agent tool result


class Confidence(models.TextChoices):
    EXACT = "exact"  # the source gave the time it happened
    OBSERVED = "observed"  # labs noticed it between two syncs


class WorkerEvent(models.Model):
    """One line of a worker's timeline, from Connect, labs, OCS or PersonalID.

    Connect keeps no history for suspensions, task status, reviews or payment deletes,
    so many Connect events are written from snapshot diffs (`confidence=observed`).
    `dedupe_key` makes every writer idempotent.
    """

    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="events")
    opportunity_id = models.IntegerField(null=True, blank=True, db_index=True)
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    kind = models.CharField(max_length=40, db_index=True)
    occurred_at = models.DateTimeField(null=True, blank=True)
    observed_at = models.DateTimeField(default=timezone.now)
    source = models.CharField(max_length=20)  # connect | labs | ocs | personalid
    confidence = models.CharField(max_length=10, choices=Confidence, default=Confidence.EXACT)
    visibility = models.CharField(max_length=10, choices=Visibility, default=Visibility.NORMAL)
    ref = models.JSONField(default=dict, blank=True)
    payload = models.JSONField(default=dict, blank=True)
    dedupe_key = models.CharField(max_length=200, unique=True)

    class Meta:
        indexes = [models.Index(fields=["worker", "occurred_at"], name="workerevent_worker_time")]
        ordering = ["-occurred_at", "-id"]

    @property
    def at(self):
        return self.occurred_at or self.observed_at


class FactKind(models.TextChoices):
    PREFERENCE = "preference"
    LANGUAGE = "language"
    CONSTRAINT = "constraint"
    CONTEXT = "context"
    CORRECTION = "correction"
    CAUTION = "caution"


class FactStatus(models.TextChoices):
    ACTIVE = "active"
    SUPERSEDED = "superseded"
    RETRACTED = "retracted"


class WorkerFact(models.Model):
    """A durable thing learned about a worker, with provenance. Append-only: supersede or retract.

    Shaped like canopy's PersonFact on purpose. Never a judgement of performance or character.
    `program_id` null = global (e.g. language, "no AI messages"); otherwise program-scoped.
    """

    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="facts")
    program_id = models.IntegerField(null=True, blank=True)
    kind = models.CharField(max_length=20, choices=FactKind)
    statement = models.CharField(max_length=300)
    status = models.CharField(max_length=12, choices=FactStatus, default=FactStatus.ACTIVE)
    confidence = models.FloatField(default=1.0)
    asserted_by_kind = models.CharField(max_length=10)  # agent | staff | worker | system
    asserted_by = models.CharField(max_length=200, blank=True, default="")
    source_thread_id = models.BigIntegerField(null=True, blank=True)
    source_message_id = models.BigIntegerField(null=True, blank=True)
    supersedes = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    expires_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    retracted_at = models.DateTimeField(null=True, blank=True)
    retracted_by = models.CharField(max_length=200, blank=True, default="")
    retraction_reason = models.CharField(max_length=300, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["worker", "status"], name="workerfact_worker_status")]

    def __str__(self):
        return f"[{self.kind}] {self.statement}"


class StaffNote(models.Model):
    worker = models.ForeignKey(ConnectWorker, on_delete=models.CASCADE, related_name="notes")
    program_id = models.IntegerField(null=True, blank=True)
    body = EncryptedTextField()
    private = models.BooleanField(default=False, help_text="Private notes are never shown to an agent.")
    author = models.CharField(max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
