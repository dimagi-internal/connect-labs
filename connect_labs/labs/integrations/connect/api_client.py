"""
Connect LabsRecord API Client.

Hybrid client. Real Connect opportunities flow over HTTP to production's
``/export/labs_record/`` endpoint. Labs-only synthetic opportunities (opp_ids
>= 10_000 registered in the SyntheticOpportunity table with ``labs_only=True``)
flow into the local ``LabsLocalRecord`` table via the
``labs.synthetic.local_records_backend`` module. The dispatch is invisible to
callers: every method returns the same ``LocalLabsRecord`` wrapper either way.
"""

import functools
import inspect
import logging

import httpx
from django.conf import settings

from connect_labs.audit_trail import service as _audit_service
from connect_labs.audit_trail.models import Action as _AuditAction
from connect_labs.audit_trail.models import Outcome as _AuditOutcome
from connect_labs.labs.models import LocalLabsRecord
from connect_labs.labs.synthetic import local_records_backend as _local_backend

logger = logging.getLogger(__name__)


_BODY_TRUNCATION = 2000


def _audited(action):
    """Record an audit event for a client method call (HIPAA access logging).

    Wraps both the production-HTTP and local-synthetic dispatch paths in one
    seam. Events are best-effort — a broken audit pipeline never blocks the
    data operation itself.
    """

    def decorator(fn):
        sig = inspect.signature(fn)

        @functools.wraps(fn)
        def wrapper(self, *args, **kwargs):
            def emit(result=None, outcome=_AuditOutcome.SUCCESS, error: str | None = None):
                try:
                    try:
                        bound = sig.bind(self, *args, **kwargs)
                        params = bound.arguments
                    except TypeError:
                        params = {}
                    record_ids = params.get("record_ids")
                    if isinstance(result, list):
                        count = len(result)
                    elif record_ids:
                        count = len(record_ids)
                    elif fn.__name__ == "get_record_by_id":
                        count = 1 if result is not None else 0
                    else:
                        count = None
                    resource_id = params.get("record_id")
                    if resource_id is None and result is not None and not isinstance(result, list):
                        resource_id = getattr(result, "id", None)
                    metadata = {"method": fn.__name__}
                    if params.get("experiment"):
                        metadata["experiment"] = str(params["experiment"])
                    if record_ids:
                        metadata["record_ids"] = list(record_ids)[:100]
                    if error:
                        metadata["error"] = error
                    program_id = params.get("program_id") or self.program_id
                    _audit_service.record(
                        action,
                        resource_type=params.get("type") or "labs_record",
                        resource_id=resource_id,
                        record_count=count,
                        opportunity_id=self.opportunity_id,
                        program_id=program_id,
                        organization_id=self.organization_id if isinstance(self.organization_id, int) else None,
                        labs_only=self._is_labs_only(),
                        outcome=outcome,
                        metadata=metadata,
                    )
                except Exception:  # pragma: no cover - audit must never break data access
                    logger.exception("Audit recording failed for %s (non-fatal)", fn.__name__)

            try:
                result = fn(self, *args, **kwargs)
            except Exception as exc:
                emit(outcome=_AuditOutcome.FAILURE, error=type(exc).__name__)
                raise
            emit(result=result)
            return result

        return wrapper

    return decorator


class LabsAPIError(Exception):
    """Exception raised for Labs API errors.

    For HTTP errors with a response, ``status_code`` and ``body`` carry the
    upstream detail so MCP clients can debug the failure without server log
    access. For pure network errors, both are ``None``.
    """

    def __init__(self, message: str, status_code: int | None = None, body: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body[:_BODY_TRUNCATION] if body is not None else None


def _wrap_http_error(message: str, exc: httpx.HTTPError) -> LabsAPIError:
    response = getattr(exc, "response", None)
    status_code = response.status_code if response is not None else None
    body = response.text if response is not None else None
    return LabsAPIError(message, status_code=status_code, body=body)


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
        # Coerce scope ids to int: callers (MCP args, URL params) may pass them as
        # strings, and downstream int comparisons (e.g. is_labs_only_program_id's
        # `program_id < LABS_ONLY_OPP_ID_FLOOR`) raise TypeError on a str.
        self.opportunity_id = int(opportunity_id) if opportunity_id is not None else None
        self.organization_id = int(organization_id) if organization_id is not None else None
        self.program_id = int(program_id) if program_id is not None else None
        self.base_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
        self.http_client = httpx.Client(
            headers={"Authorization": f"Bearer {self.access_token}"},
            timeout=120.0,
        )

    def close(self):
        """Close HTTP client."""
        if self.http_client:
            self.http_client.close()

    def _effective_opportunity_id(self, opportunity_id: int | None = None) -> int | None:
        """Resolve the opportunity_id used for dispatch — caller arg wins, then client init."""
        return opportunity_id if opportunity_id is not None else self.opportunity_id

    def _is_labs_only(self, opportunity_id: int | None = None) -> bool:
        if _local_backend.is_labs_only_opportunity_id(self._effective_opportunity_id(opportunity_id)):
            return True
        # Program-scoped requests with no opportunity selected (e.g. the Workflows
        # list for a synthetic program) must also dispatch to the local backend —
        # otherwise they fall through to production Connect and 404.
        return _local_backend.is_labs_only_program_id(self.program_id)

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - close client."""
        self.close()

    @_audited(_AuditAction.LIST)
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
        if self._is_labs_only():
            return _local_backend.get_records(
                opportunity_id=self.opportunity_id,
                experiment=experiment,
                type=type,
                username=username,
                program_id=program_id or self.program_id,
                organization_id=organization_id if isinstance(organization_id, int) else self.organization_id,
                labs_record_id=labs_record_id,
                model_class=model_class,
                public=public,
                **data_filters,
            )
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
            raise _wrap_http_error(f"Failed to fetch records from production API: {e}", e) from e

    @_audited(_AuditAction.READ)
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
        if self._is_labs_only():
            return _local_backend.get_record_by_id(
                record_id=record_id,
                opportunity_id=self.opportunity_id,
                experiment=experiment,
                type=type,
                model_class=model_class,
            )
        try:
            url = f"{self.base_url}/export/labs_record/"
            params = {"id": record_id}
            if experiment:
                params["experiment"] = experiment
            if type:
                params["type"] = type

            # Include scope params so the API can authorize access to non-public records
            if self.organization_id and isinstance(self.organization_id, int):
                params["organization_id"] = self.organization_id
            if self.program_id:
                params["program_id"] = self.program_id
            if self.opportunity_id:
                params["opportunity_id"] = self.opportunity_id

            response = self.http_client.get(url, params=params)
            response.raise_for_status()

            records_data = response.json()
            if records_data:
                record_class = model_class if model_class else LocalLabsRecord
                return record_class(records_data[0])
            return None

        except httpx.HTTPError as e:
            logger.error(f"Failed to fetch record {record_id}: {e}", exc_info=True)
            raise _wrap_http_error(f"Failed to fetch record {record_id}: {e}", e) from e

    @_audited(_AuditAction.CREATE)
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
        if self._is_labs_only():
            return _local_backend.create_record(
                opportunity_id=self.opportunity_id,
                experiment=experiment,
                type=type,
                data=data,
                username=username,
                program_id=program_id or self.program_id,
                organization_id=self.organization_id if isinstance(self.organization_id, int) else None,
                labs_record_id=labs_record_id,
                public=public,
            )
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
            raise _wrap_http_error(f"Failed to create record in production API: {e}", e) from e

    @_audited(_AuditAction.UPDATE)
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
        if self._is_labs_only():
            return _local_backend.update_record(
                record_id=record_id,
                opportunity_id=self.opportunity_id,
                experiment=experiment,
                type=type,
                data=data,
                username=username,
                program_id=program_id or self.program_id,
                organization_id=self.organization_id if isinstance(self.organization_id, int) else None,
                labs_record_id=labs_record_id,
                public=public,
            )
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
            raise _wrap_http_error(f"Failed to update record in production API: {e}", e) from e

    def delete_record(self, record_id: int) -> None:
        """Delete a single record.

        Args:
            record_id: ID of record to delete

        Raises:
            LabsAPIError: If API request fails
        """
        self.delete_records([record_id])

    @_audited(_AuditAction.DELETE)
    def delete_records(self, record_ids: list[int]) -> None:
        """Delete multiple records.

        Args:
            record_ids: List of record IDs to delete

        Raises:
            LabsAPIError: If API request fails
        """
        if not record_ids:
            return

        if self._is_labs_only():
            # Scope the delete to this client's own labs-only opp/program so a
            # client scoped to one tenant can't delete another tenant's records
            # by id (the local backend has no membership check behind it).
            _local_backend.delete_records(
                record_ids=record_ids,
                opportunity_id=self.opportunity_id,
                program_id=self.program_id,
            )
            return

        try:
            payload = [{"id": record_id} for record_id in record_ids]

            url = f"{self.base_url}/export/labs_record/"
            logger.info(f"DELETE {url} with {len(record_ids)} record(s)")

            response = self.http_client.request("DELETE", url, json=payload)
            response.raise_for_status()

        except httpx.HTTPError as e:
            logger.error(f"Failed to delete records: {e}", exc_info=True)
            raise _wrap_http_error(f"Failed to delete records in production API: {e}", e) from e
