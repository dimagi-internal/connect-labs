"""
Data Access Layer for funder_dashboard.

Uses LabsRecordAPIClient to interact with production LabsRecord API.
Funds use experiment=funder_slug and are scoped by program_id for ACL.

Type constant: type="fund"
"""
import logging

from django.http import HttpRequest

from commcare_connect.funder_dashboard.models import FundRecord
from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

logger = logging.getLogger(__name__)

FUND_TYPE = "fund"


class FunderDashboardDataAccess:
    """
    Data access layer for funder_dashboard.

    Funds use experiment=funder_slug for grouping and program_id for ACL.
    The program_id is extracted from labs_context (the currently selected program).
    Funds are created as public=True so they can also be listed without a scope param.
    """

    def __init__(
        self,
        program_id: str | None = None,
        access_token: str | None = None,
        request: HttpRequest | None = None,
    ):
        self.program_id = program_id

        if request and hasattr(request, "labs_context"):
            labs_context = request.labs_context
            if not program_id and "program_id" in labs_context:
                self.program_id = str(labs_context["program_id"])

        if not access_token and request:
            from django.utils import timezone

            labs_oauth = request.session.get("labs_oauth", {})
            expires_at = labs_oauth.get("expires_at", 0)
            if timezone.now().timestamp() < expires_at:
                access_token = labs_oauth.get("access_token")

        if not access_token:
            raise ValueError("OAuth access token required for funder_dashboard data access")

        self.access_token = access_token
        self.labs_api = LabsRecordAPIClient(
            access_token,
            program_id=int(self.program_id) if self.program_id else None,
        )

    def get_funds(self, status: str | None = None) -> list[FundRecord]:
        kwargs = {}
        if status:
            kwargs["status"] = status
        return self.labs_api.get_records(
            type=FUND_TYPE,
            public=True,
            model_class=FundRecord,
            **kwargs,
        )

    def get_fund_by_id(self, fund_id: int) -> FundRecord | None:
        records = self.labs_api.get_records(
            type=FUND_TYPE,
            public=True,
            model_class=FundRecord,
        )
        for record in records:
            if record.id == fund_id:
                return record
        return None

    def create_fund(self, data: dict) -> FundRecord:
        funder_slug = data.get("funder_slug") or data.get("name", "").lower().replace(" ", "-")
        data["funder_slug"] = funder_slug
        record = self.labs_api.create_record(
            experiment=funder_slug,
            type=FUND_TYPE,
            data=data,
            program_id=int(self.program_id) if self.program_id else None,
            public=True,
        )
        return FundRecord(record.to_api_dict())

    def update_fund(self, fund_id: int, data: dict) -> FundRecord:
        funder_slug = data.get("funder_slug", "")
        record = self.labs_api.update_record(
            record_id=fund_id,
            experiment=funder_slug or None,
            type=FUND_TYPE,
            data=data,
        )
        return FundRecord(record.to_api_dict())

    def add_allocation(self, fund_id: int, allocation: dict) -> FundRecord:
        """Append an allocation entry to a fund's allocations array."""
        fund = self.get_fund_by_id(fund_id)
        if not fund:
            raise ValueError(f"Fund {fund_id} not found")
        data = dict(fund.data)
        allocations = list(data.get("allocations", []))
        allocations.append(allocation)
        data["allocations"] = allocations
        return self.update_fund(fund_id, data)

    def remove_allocation(self, fund_id: int, index: int) -> FundRecord:
        """Remove an allocation entry by index."""
        fund = self.get_fund_by_id(fund_id)
        if not fund:
            raise ValueError(f"Fund {fund_id} not found")
        data = dict(fund.data)
        allocations = list(data.get("allocations", []))
        if 0 <= index < len(allocations):
            allocations.pop(index)
        data["allocations"] = allocations
        return self.update_fund(fund_id, data)

    def _fetch_paginated(self, endpoint: str) -> list[dict]:
        """Fetch a paginated v2 export endpoint and return all rows.

        Note: this materializes all pages into memory. For very large opportunities,
        consider iterating pages directly to avoid peak memory spikes.
        """
        from django.conf import settings

        from commcare_connect.labs.integrations.connect.export_client import ExportAPIClient, ExportAPIError

        try:
            with ExportAPIClient(
                base_url=settings.CONNECT_PRODUCTION_URL,
                access_token=self.access_token,
                timeout=120.0,
            ) as client:
                return client.fetch_all(endpoint)
        except ExportAPIError as e:
            logger.error(f"[FunderDashboard] Export API error for {endpoint}: {e}")
            return []

    def fetch_completed_works(self, opportunity_id: int) -> list[dict]:
        """Fetch completed_works from Connect API."""
        return self._fetch_paginated(f"/export/opportunity/{opportunity_id}/completed_works/")

    def fetch_user_visits(self, opportunity_id: int) -> list[dict]:
        """Fetch user_visits from Connect API."""
        return self._fetch_paginated(f"/export/opportunity/{opportunity_id}/user_visits/")
