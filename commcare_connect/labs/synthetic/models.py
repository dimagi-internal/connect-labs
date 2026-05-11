from django.conf import settings
from django.db import models
from django.utils import timezone


class SyntheticOpportunity(models.Model):
    """Registry entry marking a Connect opportunity as backed by GDrive fixtures.

    Read-only interception: when an opp_id appears here, export reads are served
    from the fixture store instead of hitting Connect. Writes are unaffected.
    """

    opportunity_id = models.IntegerField(unique=True, db_index=True)
    label = models.CharField(max_length=200, blank=True)
    gdrive_folder_id = models.CharField(
        max_length=200,
        help_text="Google Drive folder ID containing the opp's fixture JSON files.",
    )
    enabled = models.BooleanField(default=True)
    notes = models.TextField(blank=True)
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
