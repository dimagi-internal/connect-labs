from django.conf import settings
from django.db import models


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
