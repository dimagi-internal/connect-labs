"""What labs writes to organisations and people outside it, and what it sent.

A **Communication** is one message, written once and addressed to one or more
**Recipients**. It is a draft until a person sends it from labs: anyone may
prepare one -- a person on a page, or an agent over MCP -- but only a person,
in a browser, sends (`service.send`). Sending writes one **SentMessage** per
recipient: the record of what went to whom, from whom, with what reply-to.

What a message is ABOUT -- a round, a tender, an award, a workflow run -- is a
generic link (`about`), so any labs record can carry its correspondence. Its
**kind** (`kinds.py`) is the feature that wrote it: who may prepare and send
it, what its placeholders fill in with, and what sending changes (an
invitation issued, an outreach marked sent).

A SentMessage is also written for mail labs sends on its own (a submission
receipt, an award notice) with no Communication behind it, so a record's
correspondence is complete.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models

from connect_labs.labs.models import LabsOrg

DRAFT = "draft"
SENT = "sent"
DISCARDED = "discarded"
STATUSES = [(DRAFT, "Draft"), (SENT, "Sent"), (DISCARDED, "Discarded")]

PREPARED_WEB = "web"
PREPARED_MCP = "mcp"
PREPARED_VIA = [(PREPARED_WEB, "In labs"), (PREPARED_MCP, "By an agent")]


class Communication(models.Model):
    kind = models.CharField(max_length=64, db_index=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=DRAFT, db_index=True)

    # What it is about: any labs record, or nothing.
    about_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    about_id = models.CharField(max_length=64, blank=True, default="")
    about = GenericForeignKey("about_type", "about_id")
    # Who writes: the organisation the message is sent for (a general message's
    # authority), and the programme when the subject belongs to one.
    owner_org = models.ForeignKey(LabsOrg, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    program_id = models.IntegerField(null=True, blank=True, db_index=True)

    title = models.CharField(max_length=200, blank=True, default="")
    # Templates every recipient's copy is filled in from (`{first_name}` ...).
    # A recipient may carry its own subject/body instead (a drafted reminder).
    subject = models.CharField(max_length=300, blank=True, default="")
    body = models.TextField(blank=True, default="")
    reply_to = models.CharField(max_length=320, blank=True, default="")
    # What the kind needs to rebuild or act on it ({"tender_id": 3} ...).
    context = models.JSONField(default=dict, blank=True)

    prepared_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    prepared_via = models.CharField(max_length=8, choices=PREPARED_VIA, default=PREPARED_WEB)
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    sent_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-pk"]
        indexes = [models.Index(fields=["about_type", "about_id"])]

    def __str__(self):
        return self.title or self.subject or f"Communication {self.pk}"

    @property
    def is_draft(self) -> bool:
        return self.status == DRAFT


class Recipient(models.Model):
    """One person a communication is addressed to."""

    communication = models.ForeignKey(Communication, on_delete=models.CASCADE, related_name="recipients")
    org = models.ForeignKey(LabsOrg, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    name = models.CharField(max_length=200, blank=True, default="")
    email = models.CharField(max_length=320, blank=True, default="")
    cc = models.JSONField(default=list, blank=True)
    # A drafted message of its own (a reminder to this supplier); blank uses the communication's.
    subject = models.CharField(max_length=300, blank=True, default="")
    body = models.TextField(blank=True, default="")
    label = models.CharField(max_length=200, blank=True, default="")
    # What the kind acts on once this one is sent ({"outreach_id": 5} ...).
    ref = models.JSONField(default=dict, blank=True)
    included = models.BooleanField(default=True)
    position = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["position", "pk"]

    @property
    def first_name(self) -> str:
        return self.name.split()[0] if self.name.strip() else ""


QUEUED = "queued"  # handed to the mail queue
DELIVERED = "delivered"  # the mail service took it
FAILED = "failed"  # the mail service refused it, after retries
NOT_SENT = "not_sent"  # never handed over (mail is off here)
UNKNOWN = "unknown"  # handed over, and no word back in time: a person says what became of it
DELIVERY = [
    (QUEUED, "Sending"),
    (DELIVERED, "Sent"),
    (FAILED, "Failed"),
    (NOT_SENT, "Not sent"),
    (UNKNOWN, "Outcome unknown"),
]

# What sending changes besides the email (`kind.after_sent`): run once the mail
# service takes the message, retried apart from it, never by resending it.
FOLLOWUP_NONE = ""  # nothing to do (an automatic notice, or not delivered)
FOLLOWUP_PENDING = "pending"
FOLLOWUP_DONE = "done"
FOLLOWUP_FAILED = "failed"
FOLLOWUP = [
    (FOLLOWUP_NONE, "None"),
    (FOLLOWUP_PENDING, "Pending"),
    (FOLLOWUP_DONE, "Done"),
    (FOLLOWUP_FAILED, "Failed"),
]


class SentMessage(models.Model):
    """What went to one person: the record a sent communication leaves.

    The body is as sent, except that a credential in it (an invitation link)
    is replaced by a description of it: labs keeps only a token's hash.
    """

    communication = models.ForeignKey(
        Communication, null=True, blank=True, on_delete=models.SET_NULL, related_name="sent_messages"
    )
    recipient = models.ForeignKey(Recipient, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    kind = models.CharField(max_length=64, db_index=True)
    about_type = models.ForeignKey(ContentType, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    about_id = models.CharField(max_length=64, blank=True, default="")
    about = GenericForeignKey("about_type", "about_id")
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    org = models.ForeignKey(LabsOrg, null=True, blank=True, on_delete=models.SET_NULL, related_name="+")

    to_email = models.CharField(max_length=320)
    to_name = models.CharField(max_length=200, blank=True, default="")
    cc = models.JSONField(default=list, blank=True)
    reply_to = models.CharField(max_length=320, blank=True, default="")
    subject = models.CharField(max_length=300)
    body = models.TextField()
    delivery = models.CharField(max_length=12, choices=DELIVERY, default=QUEUED)
    # What sending did beyond the email ({"invite_id": 7}, {"outreach_sent_on": ...}).
    effects = models.JSONField(default=dict, blank=True)
    followup = models.CharField(max_length=8, choices=FOLLOWUP, default=FOLLOWUP_NONE, blank=True, db_index=True)
    followup_attempts = models.PositiveSmallIntegerField(default=0)
    followup_error = models.TextField(blank=True, default="")
    followup_at = models.DateTimeField(null=True, blank=True)
    # Sent by a person from labs, or by labs itself (null: an automatic notice).
    sent_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    sent_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at", "-pk"]
        indexes = [models.Index(fields=["about_type", "about_id"])]
