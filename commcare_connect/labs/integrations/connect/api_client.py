"""
CommCare Connect LabsRecord API Client.

Pure API client for production LabsRecord endpoints. No local storage.
All operations are performed via HTTP calls to production API.
"""

import logging

import httpx
from django.conf import settings

from commcare_connect.labs.models import LocalLabsRecord

logger = logging.getLogger(__name__)


class LabsAPIError(Exception):
    """Exception raised for Labs API errors."""

    pass


class LabsRecordAPIClient:
    """API client for production LabsRecord endpoints.

    This client makes HTTP calls to production's data_export API endpoints
    and returns LocalLabsRecord instances. No local database storage.
    """

    def __init__(
        self,
        access_token: str,
        opportunity_id: int | None = None,
        organization_id: int | None = None,
        program_id: int | None = None,
    ):
        """Initialize API client.

        Args:
            access_token: OAuth Bearer token for production API
            opportunity_id: Optional opportunity ID for scoped API requests
            organization_id: Optional organization ID for scoped API requests
            program_id: Optional program ID for scoped API requests

        Note: At least one of opportunity_id, organization_id, or program_id should be provided.
        """
        self.access_token = access_token
        self.opportunity_id = opportunity_id
        self.organization_id = organization_id
        self.program_id = program_id
        self.base_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
        self.http_client = httpx.Client(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=30.0,
        )

    def close(self):
        """Close HTTP client."""
        if self.http_client:
            self.http_client.close()

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close client."""
        self.close()

    def get_records(
        self,
        experiment: str | None = None,
        type: str | None = None,
        username: str | None = None,
        organization_id: str | None = None,
        program_id: int | None = None,
        labs_record_id: int | None = None,
        model_class: type[LocalLabsRecord] | None = None,
        public: bool | None = None,
        **data_filters,
    ) -> list[LocalLabsRecord]:
        """Fetch records from production API.

        Args:
            experiment: Optional experiment name filter (e.g., 'audit', 'tasks', 'solicitations')
            type: Optional record type filter (e.g., 'AuditSession', 'Task')
            username: Filter by username
            organization_id: Filter by organization slug/ID
            program_id: Filter by program ID
            labs_record_id: Filter by parent record ID
            model_class: Optional proxy model class to instantiate (e.g., AuditSessionRecord)
            public: Filter by public flag (True = public records queryable without scope)
            **data_filters: Additional filters for JSON data fields

        Returns:
            List of LocalLabsRecord instances (or proxy model instances if model_class provided)

        Raises:
            LabsAPIError: If API request fails
        """
        try:
            # Build query parameters
            params = {}

            # Add optional filters
            if experiment:
                params["experiment"] = experiment
            if type:
                params["type"] = type

            # Add username filter if provided
            if username:
                params["username"] = username

            # Handle public filter:
            # When public=True, we DON'T add scope params and DON'T send public param
            # The server automatically filters for public records when no scope is provided
            # When public=False or None, we use scope params as normal
            skip_scope = public is True

            # Add scope filters from client initialization or method parameters
            # NOTE: organization_id must be an integer ID, not a slug
            # labs_context now provides integer IDs extracted from OAuth data
            # Skip scope params when requesting public records
            if not skip_scope:
                if organization_id and isinstance(organization_id, int):
                    params["organization_id"] = organization_id
                elif self.organization_id and isinstance(self.organization_id, int):
                    params["organization_id"] = self.organization_id
                if program_id:
                    params["program_id"] = program_id
                elif self.program_id:
                    params["program_id"] = self.program_id
                if self.opportunity_id:
                    params["opportunity_id"] = self.opportunity_id
            if labs_record_id:
                params["labs_record_id"] = labs_record_id

            # Add data filters (for JSON field queries)
            for key, value in data_filters.items():
                params[f"data__{key}"] = value

            # Make API request to new endpoint (no opportunity_id in URL)
            url = f"{self.base_url}/export/labs_record/"
            logger.debug(f"GET {url} with params: {params}")

            response = self.http_client.get(url, params=params)
            response.raise_for_status()

            # Deserialize to LocalLabsRecord instances (or proxy model if specified)
            records_data = response.json()
            record_class = model_class if model_class else LocalLabsRecord
            return [record_class(item) for item in records_data]

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch records: {e}", exc_info=True)
            raise LabsAPIError(f"Failed to fetch records from production API: {e}") from e

    def get_record_by_id(
        self,
        record_id: int,
        experiment: str | None = None,
        type: str | None = None,
        model_class: type[LocalLabsRecord] | None = None,
    ) -> LocalLabsRecord | None:
        """Get a single record by ID.

        Uses server-side id filtering for O(1) lookup instead of fetching
        all records and scanning.

        Args:
            record_id: Record ID
            experiment: Optional experiment name filter (optimization hint)
            type: Optional record type filter (optimization hint)
            model_class: Optional proxy model class to instantiate

        Returns:
            LocalLabsRecord instance (or proxy model) or None if not found
        """
        try:
            url = f"{self.base_url}/export/labs_record/"
            params = {"id": record_id}
            if experiment:
                params["experiment"] = experiment
            if type:
                params["type"] = type

            response = self.http_client.get(url, params=params)
            response.raise_for_status()

            records_data = response.json()
            if records_data:
                record_class = model_class if model_class else LocalLabsRecord
                return record_class(records_data[0])
            return None

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch record {record_id}: {e}", exc_info=True)
            raise LabsAPIError(f"Failed to fetch record {record_id}: {e}") from e

    def create_record(
        self,
        experiment: str,
        type: str,
        data: dict,
        username: str | None = None,
        program_id: int | None = None,
        labs_record_id: int | None = None,
        public: bool = False,
    ) -> LocalLabsRecord:
        """Create a new record in production.

        Args:
            experiment: Experiment name
            type: Record type
            data: JSON data to store
            username: Username to associate record with
            program_id: Program ID
            labs_record_id: Parent record ID
            public: Whether record is publicly queryable without scope

        Returns:
            Created LocalLabsRecord instance

        Raises:
            LabsAPIError: If API request fails
        """
        payload = {
            "experiment": experiment,
            "type": type,
            "data": data,
            "public": public,
        }

        if username:
            payload["username"] = username
        if program_id:
            payload["program_id"] = program_id
        elif self.program_id:
            payload["program_id"] = self.program_id
        # Only include organization_id if it's an integer ID, not a slug
        if self.organization_id and isinstance(self.organization_id, int):
            payload["organization_id"] = self.organization_id
        if self.opportunity_id:
            payload["opportunity_id"] = self.opportunity_id
        if labs_record_id:
            payload["labs_record_id"] = labs_record_id

        try:
            url = f"{self.base_url}/export/labs_record/"
            logger.debug(f"POST {url} payload: {payload}")

            response = self.http_client.post(url, json=[payload])
            if response.status_code >= 400:
                logger.error(f"API error response ({response.status_code}): {response.text[:1000]}")
            response.raise_for_status()

            result = response.json()
            if not result:
                raise LabsAPIError("API returned empty response after create")

            return LocalLabsRecord(result[0])

        except httpx.HTTPError as e:
            logger.error(f"Failed to create record: {e}", exc_info=True)
            raise LabsAPIError(f"Failed to create record in production API: {e}") from e

    def update_record(
        self,
        record_id: int,
        experiment: str,
        type: str,
        data: dict,
        username: str | None = None,
        program_id: int | None = None,
        labs_record_id: int | None = None,
        public: bool | None = None,
        current_record: LocalLabsRecord | None = None,
    ) -> LocalLabsRecord:
        """Update an existing record in production (upsert).

        Args:
            record_id: ID of record to update
            experiment: Experiment name (required to fetch current record)
            type: Record type (required to fetch current record)
            data: New JSON data
            username: Updated username
            program_id: Updated program ID
            labs_record_id: Updated parent record ID
            public: Whether record is publicly queryable without scope (for sharing)
            current_record: Optional pre-fetched record (avoids redundant API call)

        Returns:
            Updated LocalLabsRecord instance

        Raises:
            LabsAPIError: If API request fails
        """
        # Use provided record or fetch current to read metadata
        if current_record is not None and current_record.id != record_id:
            logger.warning(
                f"current_record.id ({current_record.id}) != record_id ({record_id}); "
                f"ignoring current_record and fetching fresh"
            )
            current_record = None
        current = current_record or self.get_record_by_id(record_id, experiment=experiment, type=type)
        if not current:
            raise LabsAPIError(f"Record {record_id} not found")

        payload = {
            "id": record_id,
            "experiment": current.experiment,
            "type": current.type,
            "data": data,
        }

        if username is not None:
            payload["username"] = username
        elif current.username:
            payload["username"] = current.username

        # Add scope identifiers from current record or client initialization
        if program_id is not None:
            payload["program_id"] = program_id
        elif current.program_id:
            payload["program_id"] = current.program_id
        elif self.program_id:
            payload["program_id"] = self.program_id

        # Only include organization_id if it's an integer ID, not a slug
        if current.organization_id and isinstance(current.organization_id, int):
            payload["organization_id"] = current.organization_id
        elif self.organization_id and isinstance(self.organization_id, int):
            payload["organization_id"] = self.organization_id

        if current.opportunity_id:
            payload["opportunity_id"] = current.opportunity_id
        elif self.opportunity_id:
            payload["opportunity_id"] = self.opportunity_id

        if labs_record_id is not None:
            payload["labs_record_id"] = labs_record_id
        elif current.labs_record_id:
            payload["labs_record_id"] = current.labs_record_id

        # Set public flag for sharing/unsharing (ACL control)
        if public is not None:
            payload["public"] = public

        try:
            url = f"{self.base_url}/export/labs_record/"
            logger.info(f"POST {url} (update)")

            response = self.http_client.post(url, json=[payload])
            response.raise_for_status()

            result = response.json()
            if not result:
                raise LabsAPIError("API returned empty response after update")

            return LocalLabsRecord(result[0])

        except httpx.HTTPError as e:
            logger.error(f"Failed to update record: {e}", exc_info=True)
            raise LabsAPIError(f"Failed to update record in production API: {e}") from e

    def delete_record(self, record_id: int) -> None:
        """Delete a single record.

        Args:
            record_id: ID of record to delete

        Raises:
            LabsAPIError: If API request fails
        """
        self.delete_records([record_id])

    def delete_records(self, record_ids: list[int]) -> None:
        """Delete multiple records.

        Args:
            record_ids: List of record IDs to delete

        Raises:
            LabsAPIError: If API request fails
        """
        if not record_ids:
            return

        try:
            payload = [{"id": record_id} for record_id in record_ids]

            url = f"{self.base_url}/export/labs_record/"
            logger.info(f"DELETE {url} with {len(record_ids)} record(s)")

            response = self.http_client.request("DELETE", url, json=payload)
            response.raise_for_status()

        except httpx.HTTPError as e:
            logger.error(f"Failed to delete records: {e}", exc_info=True)
            raise LabsAPIError(f"Failed to delete records in production API: {e}") from e
