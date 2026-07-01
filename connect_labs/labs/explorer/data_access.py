"""
Data Access Layer for Labs Data Explorer

Wraps LabsRecordAPIClient to provide convenient access to LabsRecord data
with automatic labs context filtering.
"""

import logging
from typing import Any

from django.http import HttpRequest

from connect_labs.labs.integrations.connect.api_client import LabsAPIError, LabsRecordAPIClient
from connect_labs.labs.models import LocalLabsRecord

logger = logging.getLogger(__name__)


class RecordExplorerDataAccess:
    """Data access layer for Labs Data Explorer.

    Provides filtered access to LabsRecord data based on labs context
    (opportunity/program) from the request session.
    """

    def __init__(self, request: HttpRequest):
        """Initialize data access with request context.

        Args:
            request: HttpRequest with labs_oauth and labs_context in session
        """
        self.request = request

        # Get OAuth token from session
        labs_oauth = request.session.get("labs_oauth", {})
        self.access_token = labs_oauth.get("access_token")
        if not self.access_token:
            raise ValueError("No labs OAuth token found in session")

        # Get labs context (opportunity_id, program_id) from request
        self.labs_context = getattr(request, "labs_context", {})
        self.opportunity_id = self.labs_context.get("opportunity_id")
        self.program_id = self.labs_context.get("program_id")
        self.organization_id = self.labs_context.get("organization_id")

        # Initialize API client with context
        self.client = LabsRecordAPIClient(
            access_token=self.access_token,
            opportunity_id=self.opportunity_id,
            program_id=self.program_id,
            organization_id=self.organization_id,
        )

    def close(self):
        """Close the API client."""
        if self.client:
            self.client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

    def get_all_records(
        self,
        experiment: str | None = None,
        type: str | None = None,
        username: str | None = None,
    ) -> list[LocalLabsRecord]:
        """Get all records matching filters.

        The API accepts optional filters - if not provided, returns all records
        for the current labs context (opportunity/program/organization).

        Args:
            experiment: Filter by experiment name
            type: Filter by record type
            username: Filter by username

        Returns:
            List of LocalLabsRecord instances
        """
        try:
            # Make a single API call with optional filters
            # The API will return all records matching the labs context if no filters provided
            return self.client.get_records(
                experiment=experiment,
                type=type,
                username=username,
            )
        except LabsAPIError as e:
            logger.error(f"Failed to get records: {e}")
            return []

    def get_public_records(
        self,
        experiment: str | None = None,
        type: str | None = None,
    ) -> list[LocalLabsRecord]:
        """Get all public records (records not scoped to any opp/org/program).

        Args:
            experiment: Filter by experiment name
            type: Filter by record type

        Returns:
            List of LocalLabsRecord instances
        """
        try:
            # Fetch public records by passing public=True
            # This skips scope params and returns only public records
            return self.client.get_records(
                experiment=experiment,
                type=type,
                public=True,
            )
        except LabsAPIError as e:
            logger.error(f"Failed to get public records: {e}")
            return []

    def get_record_by_id(self, record_id: int) -> LocalLabsRecord | None:
        """Get a single record by ID.

        Args:
            record_id: Record ID

        Returns:
            LocalLabsRecord instance or None if not found
        """
        # We need to search across all records since we don't know
        # the experiment/type for this ID
        all_records = self.get_all_records()
        for record in all_records:
            if record.id == record_id:
                return record
        return None

    def update_record(
        self,
        record_id: int,
        data: dict[str, Any],
    ) -> LocalLabsRecord:
        """Update a record's data field.

        Args:
            record_id: ID of record to update
            data: New data dictionary

        Returns:
            Updated LocalLabsRecord instance

        Raises:
            LabsAPIError: If update fails
        """
        # Get current record to determine experiment/type
        current = self.get_record_by_id(record_id)
        if not current:
            raise LabsAPIError(f"Record {record_id} not found")

        return self.client.update_record(
            record_id=record_id,
            experiment=current.experiment,
            type=current.type,
            data=data,
            username=current.username,
            program_id=current.program_id,
            labs_record_id=current.labs_record_id,
        )

    def bulk_create_records(self, records_data: list[dict[str, Any]]) -> list[LocalLabsRecord]:
        """Create multiple records from JSON data.

        Args:
            records_data: List of record dictionaries (from JSON export)

        Returns:
            List of created LocalLabsRecord instances
        """
        created_records = []

        for record_dict in records_data:
            try:
                # Extract fields from dictionary
                experiment = record_dict.get("experiment")
                type_value = record_dict.get("type")
                data = record_dict.get("data", {})
                username = record_dict.get("username")
                program_id = record_dict.get("program_id")
                labs_record_id = record_dict.get("labs_record_id")

                if not experiment or not type_value:
                    logger.warning(f"Skipping record without experiment/type: {record_dict}")
                    continue

                created = self.client.create_record(
                    experiment=experiment,
                    type=type_value,
                    data=data,
                    username=username,
                    program_id=program_id,
                    labs_record_id=labs_record_id,
                )
                created_records.append(created)
            except LabsAPIError as e:
                logger.error(f"Failed to create record: {e}")
                # Continue with other records
                continue

        return created_records

    def get_distinct_values(self, field: str) -> list[str]:
        """Get distinct values for a field across all records.

        Args:
            field: Field name ('experiment' or 'type')

        Returns:
            List of distinct values
        """
        all_records = self.get_all_records()
        values = set()
        for record in all_records:
            value = getattr(record, field, None)
            if value:
                values.add(value)
        return sorted(list(values))

    def delete_records(self, record_ids: list[int]) -> None:
        """Delete multiple records.

        Args:
            record_ids: List of record IDs to delete

        Raises:
            LabsAPIError: If delete fails
        """
        if not record_ids:
            return
        self.client.delete_records(record_ids)
