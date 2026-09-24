"""A link issued to one organisation, and what was done through it.

**Only a keyed hash of the token is stored.** The raw token is shown once, to
the person who issued it, and never again: a database dump, a log line or an
admin page that shows this table cannot be used to act as the supplier. The
hash is an HMAC under the deployment's secret rather than a bare SHA-256, so a
leaked table cannot even be used to test guesses offline.

**Scope is a list of rows, not a filter.** A link names the contracts and the
supply points it covers, one by one. "Everything this supplier supplies" would
be a rule evaluated at request time, and a rule is exactly what widens silently
when a new contract is created against the same supplier. A link covers what
the person issuing it could see when they issued it, and nothing that appears
later.

Imported by `supply_chain.models` so Django registers them with the app.
"""

from django.conf import settings
from django.db import models
from django.utils import timezone

from connect_labs.supply_chain.models import Contract, SupplyPoint, TimestampedModel


class UpdateLink(TimestampedModel):
    program_id = models.IntegerField(db_index=True)
    org = models.ForeignKey("labs.LabsOrg", on_delete=models.PROTECT, related_name="supply_update_links")
    label = models.CharField(max_length=255, blank=True, default="")

    contracts = models.ManyToManyField(Contract, blank=True, related_name="update_links")
    supply_points = models.ManyToManyField(SupplyPoint, blank=True, related_name="update_links")

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

    class Meta:
        ordering = ["-submitted_at", "-id"]
