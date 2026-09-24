"""What somebody asked to be told, and what they were told.

Three tables, each answering one question:

  AlertSubscription  who wants to hear about what, and how often.
  AlertCheckState    which checks this subscription has already reported and
                     are still true -- the memory that stops a check which
                     persists for a month from being emailed every five
                     minutes, while letting one that clears and comes back be
                     news again.
  AlertNotice        every fact that was due to go out, and whether it did.
                     The log a person reads to answer "was the donor told?",
                     and the queue a daily digest drains.

Imported by `supply_chain.models` so Django registers them with the app.
"""

from django.conf import settings
from django.db import models
from django.db.models import Q

from connect_labs.supply_chain.models import Commodity, SupplyPoint, TimestampedModel

CADENCES = ("immediate", "daily_digest")

# How a notice left, stated rather than implied by a timestamp. "queued" is as
# far as labs can see: SES delivery is the Celery worker's business, and a
# bounce lands on the SNS topic (docs/OUTBOUND_EMAIL.md). The other two are
# the honest answers when nothing was sent at all, so the log never claims a
# delivery that did not happen -- the failure mode the console backend had.
DELIVERY_STATUSES = ("pending", "queued", "email_disabled", "no_address")

NOTICE_KINDS = ("check", "movement")


def _choices(values):
    return [(v, v.replace("_", " ")) for v in values]


class AlertSubscription(TimestampedModel):
    """One recipient's standing request for news about one programme.

    Filters narrow; they never widen. A subscription with a supply point
    hears only about checks and movements that touch that point, and one with
    a commodity only about that commodity. Neither set means the whole
    programme.

    The recipient is a labs user OR an email address, never both and never
    neither -- a database constraint, because a subscription nobody can
    receive is a row that looks like it is working. The email address is how
    an outside party (a donor, a partner) is told, and it needs no labs account.
    """

    program_id = models.IntegerField(db_index=True)
    label = models.CharField(max_length=255, blank=True, default="")
    supply_point = models.ForeignKey(
        SupplyPoint, null=True, blank=True, on_delete=models.CASCADE, related_name="alert_subscriptions"
    )
    commodity = models.ForeignKey(
        Commodity, null=True, blank=True, on_delete=models.CASCADE, related_name="alert_subscriptions"
    )
    # Open lists rather than an enum column. Check kinds come from
    # `checks.KIND_CATEGORIES`, which grows by deploy; validating against the
    # live dict at write time (operations.py) keeps this table from needing a
    # migration every time a check is added.
    check_kinds = models.JSONField(default=list, blank=True)
    movement_kinds = models.JSONField(default=list, blank=True)

    recipient_user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="supply_alert_subscriptions",
    )
    recipient_email = models.EmailField(blank=True, default="")
    cadence = models.CharField(max_length=16, default="immediate", choices=_choices(CADENCES))
    active = models.BooleanField(default=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )

    # The newest movement id this subscription has already considered. Set to
    # the ledger's head when the subscription is made, so subscribing does not
    # mail the programme's whole history. Ids rather than timestamps: two
    # movements can share a `created_at`, and they cannot share an id.
    movements_seen_through = models.BigIntegerField(default=0)
    last_digest_sent_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.CheckConstraint(
                condition=(Q(recipient_user__isnull=False) & Q(recipient_email=""))
                | (Q(recipient_user__isnull=True) & ~Q(recipient_email="")),
                name="alert_subscription_has_one_recipient",
            )
        ]

    def __str__(self):
        return self.label or f"alert subscription {self.pk}"

    @property
    def recipient_address(self) -> str:
        """Where a notice goes today, read at send time rather than stored.

        A labs user's address can change; copying it in at subscription time
        would keep mailing the old one.
        """
        if self.recipient_user_id:
            return (self.recipient_user.email or "").strip()
        return self.recipient_email.strip()

    @property
    def recipient_label(self) -> str:
        if self.recipient_user_id:
            # Not get_full_name(): the labs user model sets first_name and
            # last_name to None, so AbstractUser's version reads "None None".
            user = self.recipient_user
            return getattr(user, "name", "") or user.email or user.username
        return self.recipient_email


class AlertCheckState(models.Model):
    """One check this subscription has already reported, and whether it still holds.

    `cleared_at` is the whole mechanism. A check that is still true keeps its
    open row and is not reported again. One that stops being true has its row
    closed; if it comes back, there is no open row, so it is new -- which is
    what it is.
    """

    subscription = models.ForeignKey(AlertSubscription, on_delete=models.CASCADE, related_name="check_states")
    check_key = models.CharField(max_length=255)
    first_reported_at = models.DateTimeField()
    last_seen_at = models.DateTimeField()
    cleared_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # At most one OPEN row per check: the uniqueness that makes a
            # concurrent second run unable to report the same thing twice.
            models.UniqueConstraint(
                fields=["subscription", "check_key"],
                condition=Q(cleared_at__isnull=True),
                name="alert_one_open_state_per_check",
            )
        ]
        indexes = [models.Index(fields=["subscription", "cleared_at"])]


class AlertNotice(models.Model):
    """One derived fact that was due to go to one recipient.

    Carries a copy of the check or movement as it stood when it was found,
    not a pointer to it: the log has to say what the recipient was told, and
    the check it came from may have cleared by the time anyone looks.
    """

    subscription = models.ForeignKey(AlertSubscription, on_delete=models.CASCADE, related_name="notices")
    program_id = models.IntegerField(db_index=True)
    kind = models.CharField(max_length=16, choices=_choices(NOTICE_KINDS))
    # The check kind (`stock_below_minimum`) or the movement kind (`transfer`).
    subject_kind = models.CharField(max_length=64)
    subject = models.JSONField(default=dict, blank=True)
    facts = models.JSONField(default=dict, blank=True)
    since = models.DateField(null=True, blank=True)
    record_url = models.CharField(max_length=512, blank=True, default="")
    detected_at = models.DateTimeField(db_index=True)

    delivery = models.CharField(max_length=16, default="pending", choices=_choices(DELIVERY_STATUSES))
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_to = models.CharField(max_length=254, blank=True, default="")

    class Meta:
        ordering = ["-detected_at", "-id"]
        indexes = [models.Index(fields=["subscription", "delivery"])]
