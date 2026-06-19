from django.conf import settings
from django.contrib.postgres.fields import ArrayField
from django.db import models
from django.db.models import Max
from django.utils import timezone

LABS_ONLY_OPP_ID_FLOOR = 10_000

# Dimagi-internal email domains treated as a single trust boundary for labs-only
# synthetic-opp visibility: the AI team uses @dimagi-ai.com, most demo opps are
# registered for @dimagi.com, and both should see each other's synthetic opps.
DIMAGI_INTERNAL_DOMAINS = ("@dimagi.com", "@dimagi-ai.com")


class SyntheticOpportunity(models.Model):
    """Registry entry marking an opportunity as backed by GDrive fixtures.

    Two modes:

    * ``labs_only=False`` — wraps a real Connect opp. Read-only interception:
      export reads return fixture data instead of hitting Connect. Writes
      unaffected. opportunity_id matches the real Connect opp PK.
    * ``labs_only=True`` — no real Connect opp behind it. opportunity_id is
      auto-allocated from the LABS_ONLY_OPP_ID_FLOOR range. Surfaced into
      labs_context for users whose ``view_synthetic_opps`` is on and whose
      email domain matches ``allowed_domains``. org_name + program_name carry
      the display shell so templates have something to render alongside it.
    """

    opportunity_id = models.IntegerField(unique=True, db_index=True)
    label = models.CharField(max_length=200, blank=True)
    gdrive_folder_id = models.CharField(
        max_length=200,
        help_text="Google Drive folder ID containing the opp's fixture JSON files.",
    )
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
    labs_only = models.BooleanField(
        default=False,
        help_text="If true, this opp has no real Connect opp behind it and is surfaced "
        "only into labs_context for opted-in users whose email domain matches allowed_domains.",
    )
    org_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Display-only org name for labs-only opps. Ignored when labs_only=False.",
    )
    program_name = models.CharField(
        max_length=200,
        blank=True,
        help_text="Display-only program name for labs-only opps. Ignored when labs_only=False.",
    )
    program_id = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="Labs-only program this opp belongs to (reserved >= 10_000 range). When unset, "
        "the opp is its own program (program_id = opportunity_id). Set it to file several opps "
        "(e.g. a study + its service-delivery opp) under one program. Ignored when labs_only=False.",
    )
    cloned_from_opportunity_id = models.IntegerField(
        null=True,
        blank=True,
        db_index=True,
        help_text="The real Connect opportunity_id this labs-only opp was cloned from "
        "(provenance + clone idempotency). Null for opps not produced by the clone pipeline.",
    )
    allowed_domains = ArrayField(
        models.CharField(max_length=100),
        default=list,
        blank=True,
        help_text="Email-domain allowlist for labs-only opps (e.g. ['@dimagi.com']). "
        "Empty means no domain restriction beyond view_synthetic_opps being on.",
    )
    visit_count = models.IntegerField(
        null=True,
        blank=True,
        help_text="Cached count of this opp's synthetic user_visits, shown in the labs-context "
        "opportunity picker. Null = not yet computed (picker falls back to 0). Set at generation "
        "and refreshable via the refresh_synthetic_visit_counts management command.",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="synthetic_opportunities",
    )

    class Meta:
        db_table = "labs_synthetic_opportunity"
        ordering = ["-updated_at"]
        verbose_name = "Synthetic opportunity"
        verbose_name_plural = "Synthetic opportunities"

    def __str__(self):
        return f"{self.opportunity_id} — {self.label or '(unlabeled)'}"

    @classmethod
    def next_labs_only_opp_id(cls) -> int:
        """Allocate the next integer opp_id in the labs-only reserved range."""
        current_max = cls.objects.filter(labs_only=True).aggregate(m=Max("opportunity_id"))["m"]
        if current_max is None or current_max < LABS_ONLY_OPP_ID_FLOOR:
            return LABS_ONLY_OPP_ID_FLOOR
        return current_max + 1

    def is_visible_to(self, user) -> bool:
        """Return True if ``user`` should see this labs-only opp in their labs_context.

        Always False for non-labs-only opps (those gate via real Connect membership).
        For labs-only opps: requires ``view_synthetic_opps`` on AND, when
        ``allowed_domains`` is non-empty, the user's email to end with one of them
        — with Dimagi-internal domains treated as equivalent (see below).
        """
        if not self.labs_only or not self.enabled:
            return False
        if not getattr(user, "view_synthetic_opps", False):
            return False
        if not self.allowed_domains:
            return True
        email = (getattr(user, "email", "") or "").lower()
        if any(email.endswith(d.lower()) for d in self.allowed_domains):
            return True
        # Dimagi-internal equivalence: the AI team uses @dimagi-ai.com while most
        # demo opps are registered for @dimagi.com (the create form's default).
        # Treat the Dimagi-internal domains as one trust boundary so an
        # @dimagi-ai.com user sees opps allow-listed for @dimagi.com and vice
        # versa — the same reason the MCP labs-only access gate grants opted-in
        # Dimagi callers regardless of allowed_domains.
        user_is_dimagi = any(email.endswith(d) for d in DIMAGI_INTERNAL_DOMAINS)
        allowlist_is_dimagi = any(d.strip().lower() in DIMAGI_INTERNAL_DOMAINS for d in self.allowed_domains)
        return user_is_dimagi and allowlist_is_dimagi


class LabsLocalRecord(models.Model):
    """LabsRecord stored in the labs DB instead of production Connect.

    Used for opportunities that have no real Connect opp behind them — i.e.
    labs-only synthetic opps. Mirrors the production LabsRecord schema so that
    LabsRecordAPIClient can dispatch transparently: real opp_ids hit prod via
    HTTP, labs-only opp_ids hit this table via the ORM. Same wire shape on
    both sides (LocalLabsRecord wraps the resulting dict).
    """

    experiment = models.CharField(max_length=200, db_index=True)
    type = models.CharField(max_length=100, db_index=True)
    data = models.JSONField(default=dict)
    public = models.BooleanField(default=False)
    opportunity_id = models.IntegerField(db_index=True)
    organization_id = models.IntegerField(null=True, blank=True)
    program_id = models.IntegerField(null=True, blank=True, db_index=True)
    labs_record_id = models.IntegerField(null=True, blank=True, db_index=True)
    username = models.CharField(max_length=150, blank=True, default="")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "labs_local_labs_record"
        ordering = ["-id"]
        verbose_name = "Labs-local LabsRecord"
        verbose_name_plural = "Labs-local LabsRecords"

    def to_api_dict(self) -> dict:
        """Return the dict shape LocalLabsRecord(...) expects."""
        return {
            "id": self.id,
            "experiment": self.experiment,
            "type": self.type,
            "data": self.data,
            "public": self.public,
            "opportunity_id": self.opportunity_id,
            "organization_id": self.organization_id,
            "program_id": self.program_id,
            "labs_record_id": self.labs_record_id,
            "username": self.username or None,
        }

    def __str__(self):
        return f"local:{self.experiment}:{self.type}:{self.id}"


class UserSyntheticDataset(models.Model):
    """Per-user synthetic fixture data stored in the database.

    Generated on demand from the opportunity's CommCare app structure.
    Expires after 24 hours. The factory serves this instead of hitting
    real Connect when it exists for the requesting user + opportunity.
    """

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="synthetic_datasets",
    )
    opportunity_id = models.IntegerField(db_index=True)
    visit_count = models.IntegerField()
    fixtures = models.JSONField()
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()

    class Meta:
        db_table = "labs_user_synthetic_dataset"
        unique_together = [("user", "opportunity_id")]
        verbose_name = "User synthetic dataset"
        verbose_name_plural = "User synthetic datasets"

    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @classmethod
    def for_user_and_opp(cls, user, opportunity_id: int) -> "UserSyntheticDataset | None":
        try:
            dataset = cls.objects.get(user=user, opportunity_id=opportunity_id)
            if dataset.is_expired():
                dataset.delete()
                return None
            return dataset
        except cls.DoesNotExist:
            return None

    def __str__(self):
        return f"user={self.user_id} opp={self.opportunity_id} visits={self.visit_count}"
