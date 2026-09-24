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
    operation = models.CharField(max_length=64)
    result_type = models.CharField(max_length=32, blank=True, default="")
    result_id = models.IntegerField(null=True, blank=True)
    # What this submission put on the record, written when it was made. Read
    # back from the row at submit time and kept, because the row moves on: a
    # dispatch recorded as "dispatched" and later moved to customs must still
    # read "dispatched" against the submission that recorded it.
    summary = models.TextField(blank=True, default="")
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
