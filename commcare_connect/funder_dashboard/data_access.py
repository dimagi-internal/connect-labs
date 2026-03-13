"""
Data Access Layer for funder_dashboard.

Uses LabsRecordAPIClient to interact with production LabsRecord API.
FundRecords are scoped by org_id (used as experiment).

Type constant: type="fund"
"""
from django.http import HttpRequest

from commcare_connect.funder_dashboard.models import FundRecord
from commcare_connect.labs.integrations.connect.api_client import LabsRecordAPIClient

FUND_TYPE = "fund"


class FunderDashboardDataAccess:
    """
    Data access layer for funder_dashboard.

    Funds are scoped by org_id (used as experiment).
    """

    def __init__(
        self,
        org_id: str | None = None,
        access_token: str | None = None,
        request: HttpRequest | None = None,
    ):
        self.org_id = org_id

        if request and hasattr(request, "labs_context"):
            labs_context = request.labs_context
            if not org_id and "organization_id" in labs_context:
                self.org_id = str(labs_context["organization_id"])

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
            organization_id=int(self.org_id) if self.org_id else None,
        )

    def get_funds(self, status: str | None = None) -> list[FundRecord]:
        kwargs = {}
        if status:
            kwargs["status"] = status
        return self.labs_api.get_records(
            experiment=self.org_id,
            type=FUND_TYPE,
            model_class=FundRecord,
            **kwargs,
        )

    def get_fund_by_id(self, fund_id: int) -> FundRecord | None:
        return self.labs_api.get_record_by_id(
            record_id=fund_id,
            experiment=self.org_id,
            type=FUND_TYPE,
            model_class=FundRecord,
        )

    def create_fund(self, data: dict) -> FundRecord:
        record = self.labs_api.create_record(
            experiment=self.org_id,
            type=FUND_TYPE,
            data=data,
            organization_id=int(self.org_id) if self.org_id else None,
        )
        return FundRecord(record.to_api_dict())

    def update_fund(self, fund_id: int, data: dict) -> FundRecord:
        record = self.labs_api.update_record(
            record_id=fund_id,
            experiment=self.org_id,
            type=FUND_TYPE,
            data=data,
        )
        return FundRecord(record.to_api_dict())
