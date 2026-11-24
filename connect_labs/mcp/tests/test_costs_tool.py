"""`costs_opportunities`: what each opportunity cost, un-summed, and Connect's payments to its organisation.
All data invented."""

import datetime as dt
from decimal import Decimal

import pytest

from connect_labs.mcp.tool_registry import MCPToolError
from connect_labs.mcp.tools import costs as tool
from connect_labs.pulse import costs, ingest
from connect_labs.pulse.models import PulseCostEntry, PulseOrgPayment
from connect_labs.pulse.tests.test_costs import FakeClient, _inv, _opp, _work

D = Decimal


@pytest.fixture(autouse=True)
def _fresh_costs():
    costs.invalidate()
    yield
    costs.invalidate()


@pytest.mark.django_db
class TestCostsOpportunities:
    def _world(self):
        _opp(1, program_id=7)
        _opp(2, program_id=7)
        _opp(3, program_id=8, is_test=True)
        _work(1, 30, org=10, units=4)
        _inv(1, "75636FC1C8", 57143, 40)  # service delivery: billed pay already counted
        _inv(1, "SETUP1", 2_857_143, 2000, service=False)
        PulseCostEntry.objects.create(
            opportunity_id=1,
            kind=PulseCostEntry.KIND_FIXED,
            usd=D("500"),
            reason="ORS co-packs bought direct",
            source="Intacct bill 123",
        )
        PulseOrgPayment.objects.create(
            opportunity_id=1,
            organization="org-1",
            invoice_id=91,
            amount=D("57143"),
            amount_usd=D("40"),
            date_paid=dt.datetime(2026, 3, 9, tzinfo=dt.timezone.utc),
            confirmed=True,
            payment_method="bank",
        )

    def test_breaks_the_total_down_and_lists_every_invoice_with_its_number(self):
        self._world()
        got = tool.costs_opportunities(None, opportunity_ids=[1])
        row = got["rows"][0]
        assert (row["worker_usd"], row["org_usd"], row["fixed_invoiced_usd"], row["fixed_entered_usd"]) == (
            30.0,
            10.0,
            2000.0,
            500.0,
        )
        # Service-delivery invoices are listed to reconcile, never added.
        assert row["service_invoiced_usd"] == 40.0
        assert row["total_usd"] == 2540.0 == got["total_usd"]
        assert {i["invoice_number"]: i["kind"] for i in row["invoices"]} == {
            "75636FC1C8": "service_delivery",
            "SETUP1": "fixed",
        }
        assert row["cost_entries"][0]["source"] == "Intacct bill 123"
        assert row["org_payments"][0]["invoice_id"] == 91 and row["org_paid_usd"] == 40.0

    def test_a_programme_selects_its_real_opportunities_only(self):
        self._world()
        assert [r["id"] for r in tool.costs_opportunities(None, program_id=7)["rows"]] == [1, 2]
        with pytest.raises(MCPToolError):
            tool.costs_opportunities(None, program_id=8)  # only a test opportunity

    def test_include_drops_the_lists(self):
        self._world()
        row = tool.costs_opportunities(None, opportunity_ids=[1], include=[])["rows"][0]
        assert "invoices" not in row and "org_payments" not in row and "cost_entries" not in row

    def test_a_selection_is_required(self):
        with pytest.raises(MCPToolError):
            tool.costs_opportunities(None)


@pytest.mark.django_db
class TestRefreshOrgPayments:
    def test_keeps_only_organisation_payments_and_replaces_the_opportunitys_rows(self):
        opp = _opp(1)
        PulseOrgPayment.objects.create(opportunity_id=1, organization="old")
        rows = [
            {"username": "worker-a", "organization": None, "amount": "100", "amount_usd": "0.07"},
            {
                "username": None,
                "organization": "org-1",
                "invoice_id": 91,
                "amount": "57143",
                "amount_usd": "40",
                "date_paid": "2026-03-09T10:00:00Z",
                "confirmed": True,
                "payment_method": "bank",
            },
        ]
        assert ingest.refresh_org_payments(FakeClient(rows), opp) == 1
        kept = list(PulseOrgPayment.objects.filter(opportunity_id=1))
        assert [(p.organization, p.invoice_id, p.amount_usd) for p in kept] == [("org-1", 91, D("40"))]
