"""
Labs Models

LocalLabsRecord and SQL cache models for the labs environment.
"""

from typing import Any


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
