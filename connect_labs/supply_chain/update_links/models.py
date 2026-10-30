"""A link issued to one organisation, and what was done through it.

**Only a keyed hash of the token is stored.** The raw token is shown once, to
the person who issued it, and never again: a database dump, a log line or an
admin page that shows this table cannot be used to act as the supplier. The
hash is an HMAC under the deployment's secret rather than a bare SHA-256, so a
leaked table cannot even be used to test guesses offline.

**Scope is a list of rows by default, and a rule only when asked for.** A
link normally names the contracts, supply points and approvals it covers, one
by one: it covers what the person issuing it could see when they issued it, and
nothing that appears later.

That was wrong for a partner. An LLO receives goods on a cover order created
AFTER its link was issued, could not record that receipt, and the programme
officer ended up recording it for them. So a link can instead be issued to
follow its organisation (`coverage="organisation"`): everything involving that
organisation in this programme, now and later, resolved LIVE at every request
(`service.scope_for`). It widens as new orders are created -- which is exactly
why it is a separate, explicit choice made on the issue screen and shown on the
links list, never what a list of rows quietly turns into. The rule is narrow
and relational: orders the organisation supplies, buys or receives at a store
it runs; stores it runs; approvals asked of it. Nothing of another
organisation's, and nothing in another programme.

Imported by `supply_chain.models` so Django registers them with the app.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from connect_labs.supply_chain.models import AwardApproval, Contract, SupplyPoint, TimestampedModel

COVERAGE_LISTED = "listed"
COVERAGE_ORGANISATION = "organisation"
COVERAGE = (
    (COVERAGE_LISTED, "Only the orders, supply points and approvals named"),
    (COVERAGE_ORGANISATION, "Everything involving the organisation, including new orders"),
)


class UpdateLink(TimestampedModel):
    program_id = models.IntegerField(db_index=True)
    org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supply_update_links")
    label = models.CharField(max_length=255, blank=True, default="")
    # "listed": the join tables below are the scope. "organisation": they are
    # empty and the scope is worked out afresh at each request.
    coverage = models.CharField(max_length=16, default=COVERAGE_LISTED, choices=COVERAGE)
    # The PersonalID user the link was sent to, when it was sent to one person
    # (a field worker, a store keeper) rather than to an organisation's desk.
    # NOT authentication: whoever holds the link can use it, and this is only
    # who we SAID it was for -- so what is recorded through it is attributed to
    # that person, labelled as "link sent to", never as "verified". See
    # docs/personalid-authorization.md for how this becomes a verified identity.
    connect_username = models.CharField(max_length=150, blank=True, default="", db_default="", db_index=True)

    # When set, the page asks the person to prove they can read the PersonalID
    # chat of `connect_username` (a code sent there) before it shows anything or
    # accepts a write. Needs connect_username. See update_links/verification.py.
    require_verification = models.BooleanField(default=False, db_default=False)
    # The Open Chat Studio chatbot (public id) codes are sent through.
    verify_bot = models.CharField(max_length=64, blank=True, default="", db_default="")

    # Sending the link through PersonalID (update_links/delivery.py), when that
    # was asked for at issue time. The raw link exists only in the issue
    # response, so this is the only moment it can be sent from the system.
    sent_at = models.DateTimeField(null=True, blank=True)
    sent_via_bot = models.CharField(max_length=64, blank=True, default="", db_default="")
    send_error = models.CharField(max_length=255, blank=True, default="", db_default="")

    contracts = models.ManyToManyField(Contract, blank=True, related_name="update_links")
    supply_points = models.ManyToManyField(SupplyPoint, blank=True, related_name="update_links")
    # Approvals asked of this link's organisation, which it may answer itself.
    # A link to an approver, not a supplier: it names these and nothing else.
    approvals = models.ManyToManyField(AwardApproval, blank=True, related_name="update_links")

    token_hash = models.CharField(max_length=64, unique=True)
    # The first characters of the raw token, so the issuer can tell two links
    # apart on the list without the list being able to act as either.
    token_hint = models.CharField(max_length=8, blank=True, default="")

    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)
    issued_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    revoked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.label or f"update link {self.pk}"

    @property
    def follows_org(self) -> bool:
        return self.coverage == COVERAGE_ORGANISATION

    @property
    def is_revoked(self) -> bool:
        return self.revoked_at is not None

    @property
    def is_expired(self) -> bool:
        return self.expires_at <= timezone.now()

    @property
    def is_usable(self) -> bool:
        return not self.is_revoked and not self.is_expired

    @property
    def state(self) -> str:
        if self.is_revoked:
            return "revoked"
        if self.is_expired:
            return "expired"
        return "active"


class UpdateLinkSubmission(models.Model):
    """One write made through a link: which operation, and what it produced.

    The row the write produced already says `source=supplier_reported` and
    names the organisation. This says which LINK it came through, which the
    row cannot -- so revoking a link that was misused leaves a list of exactly
    what it was used for.
    """

    link = models.ForeignKey(UpdateLink, on_delete=models.CASCADE, related_name="submissions")
    action = models.CharField(max_length=32)
    # Whom the link was sent to when this was recorded, copied from the link so
    # the attribution survives the link being revoked or re-issued. Empty for a
    # link sent to an organisation rather than a person.
    connect_username = models.CharField(max_length=150, blank=True, default="", db_default="", db_index=True)
    operation = models.CharField(max_length=64)
    result_type = models.CharField(max_length=32, blank=True, default="")
    result_id = models.IntegerField(null=True, blank=True)
    # What this submission put on the record, written when it was made. Read
    # back from the row at submit time and kept, because the row moves on: a
    # dispatch recorded as "dispatched" and later moved to customs must still
    # read "dispatched" against the submission that recorded it.
    summary = models.TextField(blank=True, default="")
    # Whether this was done from a browser that had proved it could read the
    # recipient's PersonalID chat (a LinkDevice), as opposed to merely holding
    # the link.
    verified = models.BooleanField(default=False, db_default=False)
    # Set by the field page: an id the phone made up for this one entry, so that
    # sending it again (a retry after a dropped connection, a second tab) is the
    # same entry, never a second one. Empty for everything entered on the form page.
    client_id = models.CharField(max_length=64, blank=True, default="", db_default="")
    # The phone's own clock when it was entered. An entry made in a village on
    # Monday and sent on Thursday says so; `submitted_at` is when we got it.
    device_time = models.DateTimeField(null=True, blank=True)
    photo_attached = models.BooleanField(default=False, db_default=False)
    # The order it touched, so the order page can ask the database for its
    # submissions rather than filtering every link's in Python.
    contract = models.ForeignKey(
        Contract, null=True, blank=True, on_delete=models.SET_NULL, related_name="link_submissions"
    )
    submitted_at = models.DateTimeField(default=timezone.now, db_index=True)
    # When it HAPPENED, in the organisation's own words: the day the payment
    # arrived, the goods were received, the stock was released. Not when it
    # was typed. A distributor catching up records a fortnight of events in
    # one sitting, and the order page listed an order confirmed, a payment
    # received and 600 cartons inspected all "24 Sep, 12:08".
    happened_on = models.DateField(null=True, blank=True)

    class Meta:
        ordering = ["-submitted_at", "-id"]
        constraints = [
            models.UniqueConstraint(
                fields=["link", "client_id"],
                condition=~models.Q(client_id=""),
                name="uniq_update_link_submission_client_id",
            )
        ]


class LinkVerification(models.Model):
    """One code sent to the recipient's PersonalID chat, waiting to be typed back.

    Only a keyed hash of the code is kept. A code is good once, for ten minutes,
    for five tries.
    """

    link = models.ForeignKey(UpdateLink, on_delete=models.CASCADE, related_name="verifications")
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    expires_at = models.DateTimeField()
    attempts = models.PositiveSmallIntegerField(default=0)
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at", "-id"]


class LinkDevice(models.Model):
    """A browser that proved it could read the recipient's PersonalID chat.

    The browser holds a random secret in a cookie; only its keyed hash is
    stored. It is good until the link expires or is revoked. This is what
    "verified" means: *a browser whose user read a code sent to X's chat* --
    possession of X's registered device and app at that moment, not a
    continuing identity.
    """

    link = models.ForeignKey(UpdateLink, on_delete=models.CASCADE, related_name="devices")
    secret_hash = models.CharField(max_length=64, unique=True)
    connect_username = models.CharField(max_length=150)
    created_at = models.DateTimeField(default=timezone.now)
    last_used_at = models.DateTimeField(null=True, blank=True)
