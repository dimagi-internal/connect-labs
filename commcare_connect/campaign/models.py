from django.conf import settings
from django.db import models

from commcare_connect.campaign.services import rbac


class CampaignUser(models.Model):
    """An in-app whitelist entry: who may sign in and with what role."""

    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        INACTIVE = "inactive", "Inactive"
        DEACTIVATED = "deactivated", "Deactivated"

    ROLE_CHOICES = [(r, r.replace("_", " ").title()) for r in rbac.ROLES]

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="campaign_membership",
    )
    commcare_username = models.CharField(max_length=255, unique=True)
    email = models.EmailField()
    name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=32, choices=ROLE_CHOICES, default="reporting_user")
    scope = models.CharField(max_length=64, default="All regions")
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ACTIVE)
    invited_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    created_at = models.DateTimeField(auto_now_add=True)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        db_table = "campaign_user"

    def __str__(self):
        return f"{self.commcare_username} ({self.role})"

    @property
    def is_active_member(self) -> bool:
        return self.status == self.Status.ACTIVE
