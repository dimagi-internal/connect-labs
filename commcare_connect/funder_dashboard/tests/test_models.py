"""Tests for FundRecord proxy model."""
from commcare_connect.funder_dashboard.models import FundRecord


class TestFundRecord:
    def _make_fund(self, **data_overrides):
        data = {
            "name": "Bloomberg Neonatal Fund",
            "description": "Emergency care for newborns",
            "total_budget": 3000000,
            "currency": "USD",
            "org_id": "org_42",
            "program_ids": [1, 2, 3],
            "delivery_types": ["kmc", "transport"],
            "status": "active",
        }
        data.update(data_overrides)
        return FundRecord(
            {"id": 1, "experiment": "org_42", "type": "fund", "data": data, "opportunity_id": 0}
        )

    def test_name(self):
        assert self._make_fund().name == "Bloomberg Neonatal Fund"

    def test_description(self):
        assert self._make_fund().description == "Emergency care for newborns"

    def test_total_budget(self):
        assert self._make_fund().total_budget == 3000000

    def test_currency(self):
        assert self._make_fund().currency == "USD"

    def test_currency_default(self):
        fund = FundRecord({"id": 1, "experiment": "x", "type": "fund", "data": {}, "opportunity_id": 0})
        assert fund.currency == "USD"

    def test_org_id(self):
        assert self._make_fund().org_id == "org_42"

    def test_program_ids(self):
        assert self._make_fund().program_ids == [1, 2, 3]

    def test_program_ids_default(self):
        fund = FundRecord({"id": 1, "experiment": "x", "type": "fund", "data": {}, "opportunity_id": 0})
        assert fund.program_ids == []

    def test_delivery_types(self):
        assert self._make_fund().delivery_types == ["kmc", "transport"]

    def test_status(self):
        assert self._make_fund().status == "active"

    def test_status_default(self):
        fund = FundRecord({"id": 1, "experiment": "x", "type": "fund", "data": {}, "opportunity_id": 0})
        assert fund.status == "active"
