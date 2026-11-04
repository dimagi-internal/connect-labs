"""The Overview's own picture of the programme: what to chase, the next step, distribution.

THIS REPOSITORY IS PUBLIC. Every company here is invented.

Sophie's RUTF feedback, "Buying pages > Overview": one readable chase list that
says what each item needs; a distribution overview (partners low, field workers
running dry, open issues); and the next step before a tender exists and after
orders are placed.
"""

import sys
import types
from datetime import date

import pytest

from connect_labs.supply_chain import overview
from connect_labs.supply_chain.overview import (
    buying_model_label,
    distribution_overview,
    next_step,
    open_issues,
    partners_set_up,
)
from connect_labs.supply_chain.portfolio.chase import ask_for, chase_items, grouped
from connect_labs.supply_chain.tests import test_sophie_batch5 as batch5
from connect_labs.supply_chain.tests.test_sophie_batch3 import _home, _open, _outreach

registered_synthetic = batch5.registered_synthetic
da = batch5.da
sophie = batch5.sophie
ace = batch5.ace
base = batch5.base
client_in_program = batch5.client_in_program
home_client = batch5.home_client

LABELS = {"payment_unconfirmed": "Payment not confirmed by the payee", "stock_stockout": "No stock on hand"}


def _check(kind, audience, label, supplier=None, days=5):
    return {
        "kind": kind,
        "audience": audience,
        "subject": {"type": "payment", "id": 1, "label": label},
        "facts": {"supplier": {"name": supplier}} if supplier else {},
        "days_open": days,
        "href": "/x/",
    }


class TestTheChaseListSaysWhatEachItemNeeds:
    def test_a_payment_the_payee_has_not_confirmed_says_who_to_ask_and_what(self):
        assert ask_for("payment_unconfirmed", "supplier", "DABS", "x") == "Ask DABS to confirm they received payment"

    def test_a_missing_name_falls_back_to_the_audience_not_to_a_gap_in_the_sentence(self):
        assert (
            ask_for("payment_unconfirmed", "supplier", "", "x") == "Ask the supplier to confirm they received payment"
        )
        assert ask_for("contract_reference_unknown", "internal", "", "x") == "Record the purchase order number"

    def test_a_kind_with_no_sentence_shows_its_label(self):
        assert ask_for("stock_stockout", "internal", "", "No stock on hand") == "No stock on hand"

    def test_items_come_from_checks_consignments_and_orders_and_group_oldest_first(self):
        items = chase_items(
            program_id=1,
            program_name="Prog",
            checks=[
                _check("payment_unconfirmed", "supplier", "DABS — INV-1", days=3),
                _check("payment_unconfirmed", "supplier", "Nutriset — INV-2", days=30),
            ],
            consignments=[
                {
                    "from_supply_point_id": 1,
                    "to_supply_point_id": 2,
                    "quantity": "10",
                    "quantity_unit": "carton",
                    "expected_on": None,
                    "dispatched_on": "2026-10-01",
                    "receive_url": "/r/",
                },
                {"from_supply_point_id": 1, "to_supply_point_id": 2, "expected_on": "2026-11-01"},
            ],
            orders=[
                {"contract_id": 9, "supplier_id": 5, "reference": "PO-9", "expected_on": None, "url": "/o/"},
                {"contract_id": 10, "supplier_id": 5, "reference": "PO-10", "expected_on": "2026-12-01"},
            ],
            suppliers={5: {"name": "Sahel Foods"}},
            places={1: {"name": "Kano store", "managed_by": "CBI"}, 2: {"name": "Abuja store", "managed_by": ""}},
            check_labels=LABELS,
            today=date(2026, 11, 4),
        )
        asks = [i["ask"] for i in items]
        assert "Ask DABS to confirm they received payment" in asks
        assert "Ask Nutriset to confirm they received payment" in asks
        # A consignment or order that already has a date is not a missing-date item.
        assert asks.count("Ask CBI when the stock will arrive") == 1
        assert asks.count("Ask Sahel Foods for an arrival date") == 1
        consignment = next(i for i in items if i["what"].startswith("On the road"))
        assert consignment["days"] == 34 and "Kano store → Abuja store" in consignment["about"]

        groups = grouped(items)
        assert [g["audience"] for g in groups] == ["supplier", "internal"]
        supplier_days = [i["days"] for i in groups[0]["items"]]
        assert supplier_days == [30, 3, None]  # oldest first, undated last


class TestTheNextStep:
    def test_before_a_tender_exists(self):
        step = next_step(tenders=[], contracts=[], points=[])
        assert step["line"] == "Tender: not created"
        assert step["next"] == "Create a tender to ask suppliers for quotes"

    def test_partners_and_the_needed_by_date_are_in_the_line(self):
        points = [
            {"id": 1, "managed_by_org_id": 11, "status": "active", "kind": "central_store"},
            {"id": 2, "managed_by_org_id": 12, "status": "active", "kind": "central_store"},
            {"id": 3, "managed_by_org_id": 13, "status": "active", "kind": "supplier_site"},
        ]
        contracts = [{"id": 1, "status": "draft", "buyer_org_id": 14}]
        assert partners_set_up(points, contracts, exclude_org_ids=[12]) == (1, 2)
        step = next_step(
            tenders=[{"id": 4, "status": "draft", "needed_by": "2027-02-15", "start_by": "2026-12-05"}],
            contracts=contracts,
            points=points,
            exclude_org_ids=[12],
            today=date(2026, 11, 4),
        )
        assert (
            step["line"]
            == "Partners: 1 of 2 set up · Tender: drafted, not sent · Needed by Feb 2027, start by early Dec"
        )

    def test_without_a_needed_by_date_the_line_is_just_shorter(self):
        step = next_step(tenders=[{"id": 1, "status": "open"}], contracts=[], points=[])
        assert step["line"] == "Tender: collecting quotes"

    def test_after_orders_are_placed(self):
        contracts = [
            {"id": 1, "status": "received", "buyer_org_id": None},
            {"id": 2, "status": "placed", "buyer_org_id": None},
            {"id": 3, "status": "draft", "buyer_org_id": None},
        ]
        step = next_step(tenders=[{"id": 1, "status": "awarded"}], contracts=contracts, points=[])
        assert step["line"] == "Orders: 2 placed · 1 received"
        assert step["next"] == "Record each delivery as it arrives"


class TestBuyingModelHook:
    def test_nothing_until_the_order_records_one(self):
        assert buying_model_label({"id": 1}) == ""

    def test_a_recorded_model_is_named(self):
        assert buying_model_label({"buying_model": "partner_buys"}) == "The partner buys on our quote and invoices us"
        assert buying_model_label({"buying_model": "something_new"}) == "Something new"


POINTS = [
    {"id": 1, "managed_by_org_id": 11, "parent_supply_point_id": None, "kind": "central_store"},
    {"id": 2, "managed_by_org_id": None, "parent_supply_point_id": 1, "kind": "user_held"},
    {"id": 3, "managed_by_org_id": None, "parent_supply_point_id": 1, "kind": "user_held"},
    {"id": 4, "managed_by_org_id": None, "parent_supply_point_id": None, "kind": "user_held"},
]


def _row(point_id, name, kind, status, days):
    return {"supply_point_id": point_id, "name": name, "kind": kind, "status": status, "days_to_stockout": days}


@pytest.mark.django_db
class TestTheDistributionOverview:
    def _overview(self):
        rows = [
            _row(1, "Kano store", "central_store", "ok", "100.00"),
            _row(2, "Aisha", "user_held", "below_min", "3.00"),
            _row(3, "Bala", "user_held", "ok", "20.00"),
            _row(4, "Chidi", "user_held", "stockout", None),
        ]
        return distribution_overview(
            program_id=1, stock_points=rows, points=POINTS, org_names={11: "CBI"}, worker_href=lambda row: "/w/"
        )

    def test_partners_running_low_are_counted_by_places_not_quantities(self):
        low = self._overview()["low_partners"]
        assert [(p["name"], p["low"], p["places"]) for p in low] == [("CBI", 1, 3), ("No partner assigned", 1, 1)]

    def test_a_worker_inherits_the_partner_of_the_place_above(self):
        dry = {d["name"]: d for d in self._overview()["dry_this_week"]}
        assert dry["Aisha"]["partner"] == "CBI" and dry["Aisha"]["days"] == 3

    def test_workers_out_now_or_within_a_week_run_dry_and_the_rest_do_not(self):
        dry = self._overview()["dry_this_week"]
        assert [d["name"] for d in dry] == ["Chidi", "Aisha"]  # out now first, then soonest
        assert dry[0]["days"] is None

    def test_a_worker_whose_cover_cannot_be_told_is_not_called_dry(self):
        rows = [_row(2, "Aisha", "user_held", "unknown", {"unconfirmed": ["no visits"]})]
        assert (
            distribution_overview(program_id=1, stock_points=rows, points=POINTS, org_names={})["dry_this_week"] == []
        )


class TestOpenIssuesHook:
    @pytest.mark.django_db
    def test_with_the_issues_package_installed_no_issues_reads_as_none_open_not_as_not_recorded(self):
        assert open_issues(1) == {"available": True, "items": []}

    def test_without_an_issues_package_it_says_not_recorded(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "connect_labs.supply_chain.issues", None)
        assert open_issues(1) == {"available": False, "items": []}

    def test_it_lights_up_when_the_issues_package_provides_the_reader(self, monkeypatch):
        module = types.ModuleType("connect_labs.supply_chain.issues")
        module.open_issues = lambda program_id: [{"title": "Carton missing", "partner": "CBI", "href": "/i/"}]
        monkeypatch.setitem(sys.modules, "connect_labs.supply_chain.issues", module)
        found = overview.open_issues(7)
        assert found["available"] is True and found["items"][0]["title"] == "Carton missing"


@pytest.mark.django_db
class TestThePage:
    def test_the_page_is_titled_overview_and_leads_with_the_next_step_and_the_chase_list(self, da, base, home_client):
        _open(da, base["tender"]["id"])
        _outreach(da, base["tender"]["id"], base["supplier"]["id"], "2026-09-01")
        body = _home(home_client)
        assert ">Overview</h1>" in body and ">Procurements</h1>" not in body
        assert 'data-testid="next-step-line"' in body and "Tender: collecting quotes" in body
        assert body.index('data-testid="next-step"') < body.index('data-testid="chase-list"')
        assert body.index('data-testid="chase-list"') < body.index('data-testid="distribution-overview"')
        assert body.index('data-testid="distribution-overview"') < body.index('data-testid="standing-table"')

    def test_open_issues_no_longer_say_they_are_not_recorded(self, da, base, home_client):
        body = _home(home_client)
        assert 'data-testid="issues-not-recorded"' not in body and "No open issues." in body
