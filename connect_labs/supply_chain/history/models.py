"""Append-only history: what write ran, and what it changed.

Provenance belongs to the **call**, and history belongs to the **record**. A
single call can change several records -- an award, for instance, writes the
award and updates the tender -- so there are two tables: `OperationCall` (one
row per write operation that ran) and `Revision` (one row per change to one
supply record).

Both tables are append-only: their `save()` refuses an update and their
`delete()` always raises. They are the records that must never lose history.
See docs/superpowers/specs/2026-09-26-supply-sophie-history-design.md §3.1.
"""

from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models
from django.db.models import Q
from django.utils import timezone

# "supplier": a partner acting for itself -- a bid on the market, or a report
# through an update link. Set only by trusted in-process callers, never by an
# MCP or HTTP caller (see `history.calls.run_recorded`).
CHANNELS = ("web", "mcp", "api", "command", "supplier")
ACTIONS = ("create", "update", "delete")


def _choices(values):
    return [(v, v) for v in values]


class AppendOnlyError(Exception):
    pass


class _AppendOnly(models.Model):
    class Meta:
        abstract = True

    def save(self, *args, **kwargs):
        if self.pk is not None and not kwargs.get("force_insert"):
            raise AppendOnlyError(f"{type(self).__name__} is append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise AppendOnlyError(f"{type(self).__name__} is append-only")


class OperationCall(_AppendOnly):
    """One write operation that ran: who, through what, on what evidence."""

    program_id = models.IntegerField(null=True, db_index=True)
    operation = models.CharField(max_length=64)
    # PROTECT on purpose: a user who has supply history cannot be hard-deleted,
    # because the history would then lose who did it. Deactivate such a user
    # (is_active=False) instead.
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.PROTECT, related_name="+")
    actor_is_agent = models.BooleanField(default=False)
    channel = models.CharField(max_length=16, choices=_choices(CHANNELS))
    # For channel "supplier": the organisation the partner acted for (a
    # labs.LabsOrg id). A plain integer, like program_id, so history never
    # holds an organisation's row in place or cascades with it.
    acting_org_id = models.IntegerField(null=True, blank=True)
    # The caller's reference for its evidence -- an email Message-ID, a
    # document hash. Blank (never null) so the uniqueness constraint below
    # can exclude blanks with a plain `~Q(source_ref="")`.
    source_ref = models.CharField(max_length=512, blank=True, default="")
    # The quoted text that justified the write. Capped at the application
    # layer (2,000 chars per the design doc); the column itself is unbounded.
    source_excerpt = models.TextField(blank=True, default="")
    # sha256 of the canonical JSON of the validated payload, `source` left
    # out. Part of the idempotency key: the same evidence producing the same
    # write is recorded once, but one email quoting two products is two writes.
    payload_digest = models.CharField(max_length=64, blank=True, default="")
    # The operation's return value, returned again on replay of a duplicate
    # source_ref. A result over RESULT_LIMIT bytes is stored as
    # {"truncated": true, "summary": ...} instead (history/calls.py).
    result = models.JSONField(null=True, encoder=DjangoJSONEncoder)
    # Recorded time only -- deliberately NOT auto_now_add, so the demo seeder
    # (Task 2) can backfill a plausible past recorded_at.
    recorded_at = models.DateTimeField(default=timezone.now, db_index=True)
    # The same evidence arriving again and being answered from this row rather
    # than written twice (history/calls.py `_replay`). A count and a day, not a
    # row per replay: nothing was written, so there is nothing else to keep,
    # and "received again 2 Sep, recorded once" is the whole story. The one
    # thing about a call that changes after it ran, alongside `result`.
    replay_count = models.PositiveIntegerField(default=0, db_default=0)
    last_replayed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["program_id", "operation", "source_ref", "payload_digest"],
                condition=~Q(source_ref=""),
                name="supply_opcall_idempotent_source",
            )
        ]

    def __str__(self):
        return f"{self.operation} ({self.channel})"


class Revision(_AppendOnly):
    """One change to one supply record."""

    call = models.ForeignKey(OperationCall, null=True, on_delete=models.PROTECT, related_name="revisions")
    program_id = models.IntegerField(null=True, db_index=True)
    content_type = models.ForeignKey(ContentType, on_delete=models.PROTECT, related_name="+")
    object_id = models.CharField(max_length=64)
    action = models.CharField(max_length=8, choices=_choices(ACTIONS))
    # {field: [old, new]}. For "create", old is null. For "delete", the full
    # last state.
    changes = models.JSONField(default=dict, encoder=DjangoJSONEncoder)
    # Automatic. Never supplied by an API caller.
    recorded_at = models.DateTimeField(default=timezone.now)

    class Meta:
        indexes = [
            models.Index(fields=["content_type", "object_id", "recorded_at"]),
            models.Index(fields=["program_id", "recorded_at"]),
        ]

    def __str__(self):
        return f"{self.action} on {self.content_type}:{self.object_id}"
