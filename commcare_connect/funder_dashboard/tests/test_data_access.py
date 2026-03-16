"""Tests for funder_dashboard data access layer.

All tests mock LabsRecordAPIClient to avoid real API calls.
"""
from unittest.mock import MagicMock, patch

import pytest

from commcare_connect.funder_dashboard.data_access import FUND_TYPE, FunderDashboardDataAccess
from commcare_connect.funder_dashboard.models import FundRecord
from commcare_connect.labs.models import LocalLabsRecord


def _make_fund_record(**overrides):
    data = {
        "name": "Test Fund",
        "description": "A test fund",
        "total_budget": 1000000,
        "currency": "USD",
        "org_id": "org_42",
        "program_ids": [1],
        "delivery_types": ["kmc"],
        "status": "active",
    }
    data.update(overrides.pop("data", {}))
    defaults = {
        "id": 1,
        "experiment": "org_42",
        "type": FUND_TYPE,
        "data": data,
        "opportunity_id": 0,
    }
    defaults.update(overrides)
    return FundRecord(defaults)


@pytest.fixture
def mock_api_client():
    with patch("commcare_connect.funder_dashboard.data_access.LabsRecordAPIClient") as MockClient:
        mock_instance = MagicMock()
        MockClient.return_value = mock_instance
        yield mock_instance


@pytest.fixture
def data_access(mock_api_client):
    da = FunderDashboardDataAccess(org_id="42", access_token="test-token")
    da.labs_api = mock_api_client
    return da


class TestConstructor:
    def test_requires_access_token(self):
        with pytest.raises(ValueError, match="OAuth access token required"):
            FunderDashboardDataAccess(org_id="42")

    @patch("commcare_connect.funder_dashboard.data_access.LabsRecordAPIClient")
    def test_stores_org_id(self, MockClient):
        da = FunderDashboardDataAccess(org_id="42", access_token="tok")
        assert da.org_id == "42"

    @patch("commcare_connect.funder_dashboard.data_access.LabsRecordAPIClient")
    def test_creates_api_client_with_token(self, MockClient):
        FunderDashboardDataAccess(org_id="42", access_token="tok")
        MockClient.assert_called_once_with("tok", organization_id=42)


class TestGetFunds:
    def test_returns_fund_records(self, data_access, mock_api_client):
        records = [_make_fund_record(id=1), _make_fund_record(id=2)]
        mock_api_client.get_records.return_value = records
        result = data_access.get_funds()
        assert result == records
        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=FUND_TYPE,
            model_class=FundRecord,
        )

    def test_filters_by_status(self, data_access, mock_api_client):
        mock_api_client.get_records.return_value = []
        data_access.get_funds(status="active")
        mock_api_client.get_records.assert_called_once_with(
            experiment="42",
            type=FUND_TYPE,
            model_class=FundRecord,
            status="active",
        )


class TestGetFundById:
    def test_returns_record(self, data_access, mock_api_client):
        record = _make_fund_record(id=5)
        mock_api_client.get_record_by_id.return_value = record
        result = data_access.get_fund_by_id(5)
        assert result is record
        mock_api_client.get_record_by_id.assert_called_once_with(
            record_id=5,
            experiment="42",
            type=FUND_TYPE,
            model_class=FundRecord,
        )

    def test_returns_none_when_not_found(self, data_access, mock_api_client):
        mock_api_client.get_record_by_id.return_value = None
        assert data_access.get_fund_by_id(999) is None


class TestCreateFund:
    def test_creates_record(self, data_access, mock_api_client):
        input_data = {"name": "New Fund", "status": "active"}
        api_return = LocalLabsRecord(
            {"id": 100, "experiment": "42", "type": FUND_TYPE, "data": input_data, "opportunity_id": 0}
        )
        mock_api_client.create_record.return_value = api_return
        result = data_access.create_fund(input_data)
        assert isinstance(result, FundRecord)
        assert result.id == 100
        mock_api_client.create_record.assert_called_once_with(
            experiment="42",
            type=FUND_TYPE,
            data=input_data,
            organization_id=42,
        )


class TestUpdateFund:
    def test_updates_record(self, data_access, mock_api_client):
        updated_data = {"name": "Updated Fund", "status": "closed"}
        api_return = LocalLabsRecord(
            {"id": 5, "experiment": "42", "type": FUND_TYPE, "data": updated_data, "opportunity_id": 0}
        )
        mock_api_client.update_record.return_value = api_return
        result = data_access.update_fund(5, updated_data)
        assert isinstance(result, FundRecord)
        assert result.id == 5
        mock_api_client.update_record.assert_called_once_with(
            record_id=5,
            experiment="42",
            type=FUND_TYPE,
            data=updated_data,
        )


class TestAddAllocation:
    def test_adds_allocation_to_fund(self):
        fund_data = {"name": "Test Fund", "total_budget": 500000, "allocations": []}
        mock_fund = FundRecord({"id": 1, "experiment": "1", "type": "fund", "opportunity_id": None, "data": fund_data})
        updated_data = dict(fund_data)
        updated_data["allocations"] = [
            {"program_id": 45, "program_name": "KMC", "amount": 200000, "type": "retroactive"}
        ]
        mock_updated = FundRecord(
            {"id": 1, "experiment": "1", "type": "fund", "opportunity_id": None, "data": updated_data}
        )

        da = FunderDashboardDataAccess(org_id="1", access_token="tok")
        with patch.object(da, "get_fund_by_id", return_value=mock_fund):
            with patch.object(da, "update_fund", return_value=mock_updated) as mock_update:
                da.add_allocation(
                    fund_id=1,
                    allocation={"program_id": 45, "program_name": "KMC", "amount": 200000, "type": "retroactive"},
                )
                call_data = mock_update.call_args[0][1]
                assert len(call_data["allocations"]) == 1
                assert call_data["allocations"][0]["program_id"] == 45


class TestRemoveAllocation:
    def test_removes_allocation_by_index(self):
        allocs = [
            {"program_id": 1, "amount": 100000, "type": "retroactive"},
            {"program_id": 2, "amount": 50000, "type": "award"},
        ]
        fund_data = {"name": "Test Fund", "total_budget": 500000, "allocations": allocs}
        mock_fund = FundRecord({"id": 1, "experiment": "1", "type": "fund", "opportunity_id": None, "data": fund_data})
        expected_data = dict(fund_data)
        expected_data["allocations"] = [allocs[1]]
        mock_updated = FundRecord(
            {"id": 1, "experiment": "1", "type": "fund", "opportunity_id": None, "data": expected_data}
        )

        da = FunderDashboardDataAccess(org_id="1", access_token="tok")
        with patch.object(da, "get_fund_by_id", return_value=mock_fund):
            with patch.object(da, "update_fund", return_value=mock_updated) as mock_update:
                da.remove_allocation(fund_id=1, index=0)
                call_data = mock_update.call_args[0][1]
                assert len(call_data["allocations"]) == 1
                assert call_data["allocations"][0]["program_id"] == 2
