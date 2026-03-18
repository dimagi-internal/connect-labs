"""
Data Access Layer for solicitations.

This layer uses LabsRecordAPIClient to interact with production LabsRecord API.
It handles casting API responses to typed proxy models
(SolicitationRecord, ResponseRecord, ReviewRecord).

This is a pure API client with no local database storage.

Type constants:
- Solicitations: experiment=program_id, type="solicitation"
- Responses: experiment=llo_entity_id, type="solicitation_response"
- Reviews: experiment=llo_entity_id, type="solicitation_review"
"""

import logging

from django.http import HttpRequest

from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient
from commcare_connect.solicitations.models import ResponseRecord, ReviewRecord, SolicitationRecord

logger = logging.getLogger(__name__)

# Record type constants
SOLICITATION_TYPE = "solicitation"
RESPONSE_TYPE = "solicitation_response"
REVIEW_TYPE = "solicitation_review"


class SolicitationsDataAccess:
    """
    Data access layer for solicitations that uses LabsRecordAPIClient.

    This class provides solicitation-specific methods and handles casting
    API responses to appropriate proxy model types.

    Solicitations are scoped by program_id (used as experiment).
    Responses and Reviews are scoped by llo_entity_id (used as experiment).
    """

    def __init__(
        self,
        program_id: str | None = None,
        organization_id: str | None = None,
        access_token: str | None = None,
        request: HttpRequest | None = None,
    ):
        """Initialize solicitations data access.

        Args:
            program_id: Program ID string (optional scope for solicitations)
            organization_id: Organization ID or slug (fallback scope when no program)
            access_token: OAuth Bearer token for production API
            request: HttpRequest object (for extracting token and context in labs mode)
        """
        self.program_id = program_id
        self.organization_id = organization_id

        # Use labs_context from middleware if available
        if request and hasattr(request, "labs_context"):
            labs_context = request.labs_context
            if not program_id and "program_id" in labs_context:
                self.program_id = str(labs_context["program_id"])
            if not organization_id and "organization_id" in labs_context:
                self.organization_id = str(labs_context["organization_id"])

        # Determine the experiment scope: prefer program_id, fall back to organization_id
        self.experiment = self.program_id or self.organization_id

        # Get OAuth token from labs session
        if not access_token and request:
            from django.utils import timezone

            labs_oauth = request.session.get("labs_oauth", {})
            expires_at = labs_oauth.get("expires_at", 0)
            if timezone.now().timestamp() < expires_at:
                access_token = labs_oauth.get("access_token")

        if not access_token:
            raise ValueError("OAuth access token required for solicitations data access")

        self.access_token = access_token
        self.labs_api = LabsRecordAPIClient(
            access_token,
            program_id=int(self.program_id) if self.program_id else None,
            organization_id=self.organization_id,
        )

    # =========================================================================
    # Solicitation Methods
    # =========================================================================

    def get_solicitations(
        self,
        status: str | None = None,
        solicitation_type: str | None = None,
    ) -> list[SolicitationRecord]:
        """
        Query for solicitation records with optional filters.

        Args:
            status: Filter by status ('active', 'closed', 'draft')
            solicitation_type: Filter by type ('eoi', 'rfp')

        Returns:
            List of SolicitationRecord instances
        """
        kwargs = {}
        if status:
            kwargs["status"] = status
        if solicitation_type:
            kwargs["solicitation_type"] = solicitation_type

        return self.labs_api.get_records(
            experiment=self.experiment,
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
            **kwargs,
        )

    def get_public_solicitations(
        self,
        solicitation_type: str | None = None,
    ) -> list[SolicitationRecord]:
        """
        Get publicly listed solicitations (no scope required).

        Args:
            solicitation_type: Filter by type ('eoi', 'rfp')

        Returns:
            List of SolicitationRecord instances
        """
        kwargs = {}
        if solicitation_type:
            kwargs["solicitation_type"] = solicitation_type

        return self.labs_api.get_records(
            type=SOLICITATION_TYPE,
            public=True,
            model_class=SolicitationRecord,
            **kwargs,
        )

    def get_solicitation_by_id(self, solicitation_id: int) -> SolicitationRecord | None:
        """
        Get a single solicitation record by ID.

        Args:
            solicitation_id: ID of the solicitation

        Returns:
            SolicitationRecord instance or None
        """
        return self.labs_api.get_record_by_id(
            record_id=solicitation_id,
            experiment=self.experiment,
            type=SOLICITATION_TYPE,
            model_class=SolicitationRecord,
        )

    def create_solicitation(self, data: dict) -> SolicitationRecord:
        """
        Create a new solicitation via production API.

        Args:
            data: Dictionary containing solicitation data

        Returns:
            SolicitationRecord instance
        """
        is_public = data.get("is_public", False)

        record = self.labs_api.create_record(
            experiment=self.experiment,
            type=SOLICITATION_TYPE,
            data=data,
            program_id=int(self.program_id) if self.program_id else None,
            public=is_public,
        )
        return SolicitationRecord(record.to_api_dict())

    def update_solicitation(self, solicitation_id: int, data: dict) -> SolicitationRecord:
        """
        Update an existing solicitation via production API.

        Args:
            solicitation_id: ID of the solicitation record to update
            data: Dictionary containing updated solicitation data

        Returns:
            Updated SolicitationRecord instance
        """
        record = self.labs_api.update_record(
            record_id=solicitation_id,
            experiment=self.experiment,
            type=SOLICITATION_TYPE,
            data=data,
        )
        return SolicitationRecord(record.to_api_dict())

    # =========================================================================
    # Response Methods
    # =========================================================================

    def get_responses_for_solicitation(
        self,
        solicitation_id: int,
    ) -> list[ResponseRecord]:
        """
        Get all responses for a solicitation.

        Args:
            solicitation_id: ID of the solicitation to get responses for

        Returns:
            List of ResponseRecord instances
        """
        return self.labs_api.get_records(
            type=RESPONSE_TYPE,
            labs_record_id=solicitation_id,
            model_class=ResponseRecord,
        )

    def get_response_by_id(self, response_id: int) -> ResponseRecord | None:
        """
        Get a single response record by ID.

        Args:
            response_id: ID of the response

        Returns:
            ResponseRecord instance or None
        """
        return self.labs_api.get_record_by_id(
            record_id=response_id,
            type=RESPONSE_TYPE,
            model_class=ResponseRecord,
        )

    def create_response(
        self,
        solicitation_id: int,
        llo_entity_id: str,
        data: dict,
    ) -> ResponseRecord:
        """
        Create a new response via production API.

        Args:
            solicitation_id: ID of the solicitation being responded to
            llo_entity_id: LLO entity ID (used as experiment for scoping)
            data: Dictionary containing response data

        Returns:
            ResponseRecord instance
        """
        record = self.labs_api.create_record(
            experiment=llo_entity_id,
            type=RESPONSE_TYPE,
            data=data,
            labs_record_id=solicitation_id,
        )
        return ResponseRecord(record.to_api_dict())

    def award_response(self, response_id: int, reward_budget: int, org_id: str) -> ResponseRecord:
        """Mark a response as awarded with budget and org_id.

        If the parent solicitation has a fund_id, auto-creates a fund allocation.
        """
        current = self.get_response_by_id(response_id)
        if not current:
            raise ValueError(f"Response {response_id} not found")

        data = dict(current.data)
        data["status"] = "awarded"
        data["reward_budget"] = reward_budget
        data["org_id"] = org_id
        result = self.update_response(response_id, data)

        # Auto-create fund allocation if solicitation has a fund_id
        try:
            solicitation = self.get_solicitation_by_id(current.solicitation_id)
            if solicitation and solicitation.fund_id:
                from commcare_connect.funder_dashboard.data_access import FunderDashboardDataAccess

                fda = FunderDashboardDataAccess(access_token=self.access_token)
                fda.add_allocation(
                    fund_id=solicitation.fund_id,
                    allocation={
                        "program_id": self.program_id,
                        "program_name": "",
                        "amount": reward_budget,
                        "type": "award",
                        "solicitation_id": current.solicitation_id,
                        "response_id": response_id,
                        "org_id": org_id,
                        "org_name": current.llo_entity_name,
                        "notes": f"Award from {solicitation.title}",
                    },
                )
        except Exception:
            logger.exception("Failed to auto-create fund allocation for response %s", response_id)

        return result

    def update_response(self, response_id: int, data: dict) -> ResponseRecord:
        """
        Update an existing response via production API.

        Args:
            response_id: ID of the response record to update
            data: Dictionary containing updated response data

        Returns:
            Updated ResponseRecord instance
        """
        # We need the llo_entity_id from the data to use as experiment.
        # The update_record fetches the current record to get experiment/type.
        llo_entity_id = data.get("llo_entity_id", "")
        record = self.labs_api.update_record(
            record_id=response_id,
            experiment=llo_entity_id,
            type=RESPONSE_TYPE,
            data=data,
        )
        return ResponseRecord(record.to_api_dict())

    # =========================================================================
    # Review Methods
    # =========================================================================

    def get_reviews_for_response(
        self,
        response_id: int,
    ) -> list[ReviewRecord]:
        """
        Get all reviews for a response.

        Args:
            response_id: ID of the response to get reviews for

        Returns:
            List of ReviewRecord instances
        """
        return self.labs_api.get_records(
            type=REVIEW_TYPE,
            labs_record_id=response_id,
            model_class=ReviewRecord,
        )

    def get_review_by_id(self, review_id: int) -> ReviewRecord | None:
        """
        Get a single review record by ID.

        Args:
            review_id: ID of the review

        Returns:
            ReviewRecord instance or None
        """
        return self.labs_api.get_record_by_id(
            record_id=review_id,
            type=REVIEW_TYPE,
            model_class=ReviewRecord,
        )

    def create_review(self, response_id: int, data: dict) -> ReviewRecord:
        """
        Create a new review via production API.

        Args:
            response_id: ID of the response being reviewed
            data: Dictionary containing review data

        Returns:
            ReviewRecord instance
        """
        # Use llo_entity_id from data as experiment for scoping
        llo_entity_id = data.get("llo_entity_id", "")
        record = self.labs_api.create_record(
            experiment=llo_entity_id,
            type=REVIEW_TYPE,
            data=data,
            labs_record_id=response_id,
        )
        return ReviewRecord(record.to_api_dict())

    def update_review(self, review_id: int, data: dict) -> ReviewRecord:
        """
        Update an existing review via production API.

        Args:
            review_id: ID of the review record to update
            data: Dictionary containing updated review data

        Returns:
            Updated ReviewRecord instance
        """
        llo_entity_id = data.get("llo_entity_id", "")
        record = self.labs_api.update_record(
            record_id=review_id,
            experiment=llo_entity_id,
            type=REVIEW_TYPE,
            data=data,
        )
        return ReviewRecord(record.to_api_dict())
