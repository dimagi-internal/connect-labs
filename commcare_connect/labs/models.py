"""
Labs Models

LocalLabsRecord and SQL cache models for the labs environment.
"""

from datetime import timedelta
from typing import Any

from django.conf import settings
from django.db import models
from django.utils import timezone


class LocalLabsRecord:
    """Transient object for Labs API responses. Never saved to database.

    This class mimics production LabsRecord but is not a Django model.
    It's instantiated from production API responses and provides typed access
    to record data.
    """

    def __init__(self, api_data: dict[str, Any]) -> None:
        """Initialize from production API response.

        Args:
            api_data: Response data from /export/labs_record/ API
        """
        self.id: int = api_data["id"]
        self.experiment: str = api_data["experiment"]
        self.type: str = api_data["type"]
        self.data: dict = api_data["data"]
        self.username: str | None = api_data.get("username")  # Primary user identifier (not user_id)
        self.opportunity_id: int = api_data["opportunity_id"]
        self.organization_id: str | None = api_data.get("organization_id")
        self.program_id: int | None = api_data.get("program_id")
        self.labs_record_id: int | None = api_data.get("labs_record_id")  # Parent reference
        self.public: bool = api_data.get("public", False)  # Public records can be queried without scope

    @property
    def pk(self) -> int:
        """Alias for id to mimic Django model interface.

        This allows LocalLabsRecord instances to be used in contexts that expect
        Django models, such as django-tables2 and URL reverse lookups.
        """
        return self.id

    def __str__(self) -> str:
        return f"{self.experiment}:{self.type}:{self.id}"

    def __repr__(self) -> str:
        return f"<LocalLabsRecord: {self}>"

    def to_api_dict(self) -> dict[str, Any]:
        """Serialize for API POST/PUT requests.

        Returns:
            Dict suitable for posting to production API
        """
        return {
            "id": self.id,
            "experiment": self.experiment,
            "type": self.type,
            "data": self.data,
            "username": self.username,
            "program_id": self.program_id,
            "labs_record_id": self.labs_record_id,
            "opportunity_id": self.opportunity_id,
            "organization_id": self.organization_id,
            "public": self.public,
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Prevent saving to database."""
        raise NotImplementedError("LocalLabsRecord cannot be saved. Use LabsRecordAPIClient instead.")

    def delete(self, *args: Any, **kwargs: Any) -> None:
        """Prevent deletion from database."""
        raise NotImplementedError("LocalLabsRecord cannot be deleted. Use LabsRecordAPIClient instead.")


# Import SQL cache models so Django can discover them for migrations
from commcare_connect.labs.analysis.backends.sql.models import (  # noqa: E402, F401
    ComputedFLWCache,
    ComputedVisitCache,
    RawVisitCache,
)


class UserConnectToken(models.Model):
    """Persistent store of a user's production-Connect OAuth token.

    Populated when the user logs into labs via the Connect OAuth flow. Looked up
    by the MCP server (and eventually by celery tasks) to act on a user's behalf
    without a browser session.

    The refresh_token is used to extend the access_token when it expires.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="connect_token",
    )
    access_token = models.TextField()
    refresh_token = models.TextField(blank=True)
    expires_at = models.DateTimeField()
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "labs_user_connect_token"

    def __str__(self) -> str:
        return f"ConnectToken({self.user.username})"

    @property
    def is_expired(self) -> bool:
        # Treat tokens within 60 seconds of expiry as expired to avoid races.
        return timezone.now() >= (self.expires_at - timedelta(seconds=60))


class DeletedWorkflowBackup(models.Model):
    """Safety copy of a workflow definition, written just before it is deleted.

    Deleting a workflow hard-deletes its definition and render code from the
    Connect LabsRecord store with no way to recover them (a surviving run only
    keeps the ``definition_id``). This table captures the restorable pair — the
    definition JSON plus its render-code JSX — so a deleted workflow can be
    reconstructed by hand from the admin. Runs, audit sessions, and chat
    history are intentionally not backed up.

    Written by ``WorkflowDataAccess.delete_definition`` before the delete
    executes; that write is fail-closed (a failure aborts the delete).
    """

    definition_id = models.IntegerField(db_index=True)
    opportunity_id = models.IntegerField(db_index=True)
    name = models.CharField(max_length=255, blank=True, default="")
    template_type = models.CharField(max_length=100, blank=True, default="")
    definition_data = models.JSONField(default=dict)
    render_code = models.TextField(blank=True, default="")
    deleted_by = models.CharField(max_length=150, blank=True, default="")
    deleted_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "labs_deleted_workflow_backup"
        ordering = ["-id"]
        verbose_name = "Deleted workflow backup"
        verbose_name_plural = "Deleted workflow backups"

    def __str__(self) -> str:
        return f"backup:def={self.definition_id}:opp={self.opportunity_id}:{self.name}"
