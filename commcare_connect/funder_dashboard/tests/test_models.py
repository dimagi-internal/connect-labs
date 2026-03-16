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
        return FundRecord({"id": 1, "experiment": "org_42", "type": "fund", "data": data, "opportunity_id": 0})

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


class TestFundRecordAllocations:
    _BASE = {"id": 1, "experiment": "org_1", "type": "fund", "opportunity_id": 0}

    def _make_fund(self, allocations=None, total_budget=500000):
        data = {"total_budget": total_budget, "allocations": allocations or []}
        return FundRecord({**self._BASE, "data": data})

    def test_allocations_empty_default(self):
        fund = FundRecord({**self._BASE, "data": {}})
        assert fund.allocations == []

    def test_allocations_returns_list(self):
        allocs = [{"program_id": 1, "amount": 100000, "type": "retroactive"}]
        fund = self._make_fund(allocations=allocs)
        assert fund.allocations == allocs

    def test_committed_amount_sums_allocations(self):
        allocs = [
            {"program_id": 1, "amount": 200000, "type": "retroactive"},
            {"program_id": 2, "amount": 50000, "type": "award"},
        ]
        fund = self._make_fund(allocations=allocs)
        assert fund.committed_amount == 250000

    def test_committed_amount_zero_when_empty(self):
        fund = self._make_fund()
        assert fund.committed_amount == 0

    def test_remaining_amount(self):
        allocs = [{"program_id": 1, "amount": 200000, "type": "retroactive"}]
        fund = self._make_fund(allocations=allocs, total_budget=500000)
        assert fund.remaining_amount == 300000

    def test_remaining_amount_no_budget(self):
        fund = FundRecord({**self._BASE, "data": {"allocations": [{"amount": 100}]}})
        assert fund.remaining_amount == 0
