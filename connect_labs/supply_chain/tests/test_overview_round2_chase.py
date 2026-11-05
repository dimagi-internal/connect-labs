"""Round 2, Overview: the chase list routes each item to whoever can act, in plain words, one row per contact.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import datetime
import re
from datetime import date

from django.template.loader import render_to_string

from connect_labs.supply_chain.naming import partner_of_point, store_name
from connect_labs.supply_chain.overview import next_step
from connect_labs.supply_chain.portfolio.chase import _num, chase_items, grouped
from connect_labs.supply_chain.templatetags.supply_chain_extras import _in_base_unit, deliver_stages

PLACES = {
    1: {"name": "Warehouse", "kind": "central_store", "managed_by": "North Star"},
    2: {"name": "flw_001", "kind": "user_held", "managed_by": "North Star"},
    3: {"name": "flw_002", "kind": "user_held", "managed_by": "North Star"},
    4: {"name": "flw_003", "kind": "user_held", "managed_by": ""},
}


def _check(kind, *, audience="internal", label="x", facts=None, subject_type="supply_point", subject_id=1, days=3):
    return {
        "kind": kind,
        "audience": audience,
        "subject": {"type": subject_type, "id": subject_id, "label": label},
        "facts": facts or {},
        "days_open": days,
        "href": f"/h/{subject_id}/",
    }


def _items(checks, places=PLACES):
    return chase_items(
        program_id=1,
        program_name="P",
        checks=checks,
        consignments=[],
        orders=[],
        suppliers={},
        places=places,
        check_labels={},
        today=date(2026, 11, 5),
    )


def _gap(subject_id, name, variance):
    return _check(
        "stock_variance",
        label=name,
        subject_id=subject_id,
        facts={"reconcilable": True, "variance": variance, "unit": "sachet"},
    )


class TestCountGapsGoToThePartnersSupervisor:
    def test_a_count_gap_is_the_partners_not_ours(self):
        (item,) = _items([_gap(2, "flw_001", "-8")])
        assert item["audience"] == "partner" and item["who"] == "North Star"
        assert item["what"] == "flw_001's count is 8 sachets below the records"

    def test_gaps_are_one_row_per_partner_with_the_size_of_the_gap(self):
        checks = [_gap(2, "flw_001", "-7"), _gap(3, "flw_002", "-9")]
        groups = grouped(_items(checks))
        assert [g["audience"] for g in groups] == ["partner"]
        (row,) = groups[0]["items"]
        assert row["what"] == "North Star: 2 FLWs 7–9 sachets below records"
        assert row["about"] == "flw_001, flw_002" and "supervisor" in row["ask"]
        assert [p["text"] for p in row["parts"]] == [
            "flw_001's count is 7 sachets below the records",
            "flw_002's count is 9 sachets below the records",
        ]

    def test_a_worker_with_no_partner_stays_ours(self):
        (item,) = _items([_gap(4, "flw_003", "-3")])
        assert item["audience"] == "internal"


class TestSupplierQuestionsAreOneRowPerSupplier:
    def _cost(self, n):
        return _check(
            "contract_cost_unconfirmed",
            label=f"DABS — RUTF {n}",
            subject_type="contract",
            subject_id=n,
            facts={"reasons": ["import duty cannot be resolved", "VAT is excluded but no amount was given"]},
        )

    def test_missing_duty_and_vat_is_a_question_for_the_supplier(self):
        (item,) = _items([self._cost(1)])
        assert item["audience"] == "supplier" and item["who"] == "DABS"
        assert "duty and VAT" in item["ask"] and item["ask"].startswith("Ask DABS")

    def test_three_identical_rows_become_one_naming_how_many(self):
        groups = grouped(_items([self._cost(1), self._cost(2), self._cost(3)]))
        (row,) = groups[0]["items"]
        assert row["ask"].endswith("(3 orders)")

    def test_a_suppliers_different_asks_share_one_message(self):
        shipment = _check(
            "shipment_without_certificate",
            audience="supplier",
            label="DEMO-SHP-NSCH-001 — RUTF — DABS",
            subject_type="shipment",
            facts={"supplier": {"name": "DABS"}, "tender": "Round 1"},
        )
        payment = _check(
            "payment_unconfirmed",
            audience="supplier",
            label="DABS — PAY-1",
            subject_type="payment",
            facts={"supplier": {"name": "DABS"}, "amount": "12000", "currency": "USD"},
            days=18,
        )
        groups = grouped(_items([shipment, payment]))
        (row,) = groups[0]["items"]
        assert row["days"] == 18 and len(row["parts"]) == 2
        texts = " | ".join(p["text"] for p in row["parts"])
        assert "batch certificate for the Round 1 delivery" in texts and "DEMO-SHP" not in texts
        assert "confirm they received payment" in texts

    def test_a_certificate_ask_names_the_company_never_a_shipment_code(self):
        (item,) = _items(
            [
                _check(
                    "shipment_without_certificate",
                    audience="supplier",
                    label="DEMO-SHP-NSCH-001 — RUTF — DABS",
                    subject_type="shipment",
                )
            ]
        )
        assert item["who"] == "DABS"
        assert item["ask"] == "Ask DABS for the batch certificate for this delivery"
        assert "DEMO-SHP" not in item["about"]


class TestPlainWords:
    def test_an_unconfirmed_payment_reads_as_a_person_would_say_it(self):
        (item,) = _items(
            [
                _check(
                    "payment_unconfirmed",
                    audience="supplier",
                    label="DABS — PAY-1",
                    subject_type="payment",
                    facts={"supplier": {"name": "DABS"}, "amount": "12000", "currency": "USD"},
                )
            ]
        )
        assert item["what"] == "DABS hasn't confirmed they received payment"
        assert item["amount"] == "USD 12,000"

    def test_expiring_stock_says_how_much_where_when_and_what_to_do(self):
        facts = {
            "quantity": "40 carton",
            "expiry": "2027-01-15",
            "expired": False,
            "batch": "DEMO-B1",
            "days_left": 71,
        }
        (item,) = _items([_check("stock_expiring", label="Warehouse — batch DEMO-B1", facts=facts)])
        assert item["ask"] == "40 cartons at North Star · Warehouse expire 15 Jan 2027: issue these first"
        assert item["audience"] == "partner" and item["amount"] == "40 cartons"

    def test_an_expired_batch_says_expired_and_what_to_do(self):
        facts = {"quantity": "40 carton", "expiry": "2026-10-01", "expired": True, "batch": "B", "days_left": -35}
        (item,) = _items([_check("stock_expiring", label="Warehouse — batch B", facts=facts)])
        assert "expired 1 Oct 2026: take them off the shelf" in item["ask"]

    def test_a_figure_in_scientific_notation_reads_as_a_number(self):
        assert _num("4E+1") == "40" and _num("1288.4000") == "1,288.4" and _num("8940") == "8,940"


class TestEveryRowHasAnAgeAnAmountAndAnAction:
    def test_the_list_prints_them_and_explains_batch_certificate_on_hover(self):
        shipment = _check(
            "shipment_without_certificate",
            audience="supplier",
            label="DEMO-SHP-NSCH-001 — RUTF — DABS",
            subject_type="shipment",
            facts={"supplier": {"name": "DABS"}, "tender": "Round 1"},
            days=18,
        )
        html = render_to_string("supply_chain/_chase_list.html", {"chase": grouped(_items([shipment]))})
        assert "18 days open" in html and "Ask DABS for the" in html
        assert '<abbr title="The manufacturer' in html and ">batch certificate</abbr>" in html

    def test_a_row_with_no_date_says_so(self):
        from connect_labs.supply_chain.portfolio.chase import chase_items as items

        rows = items(
            program_id=1,
            program_name="P",
            checks=[],
            consignments=[],
            orders=[{"contract_id": 9, "supplier_id": 5, "reference": "PO-9", "expected_on": None, "url": "/o/"}],
            suppliers={5: {"name": "Sahel Foods"}},
            places={},
            check_labels={},
        )
        html = render_to_string("supply_chain/_chase_list.html", {"chase": grouped(rows)})
        assert "no date recorded" in html


class TestStoresAreNamedByPartner:
    def test_a_store_leads_with_its_partner(self):
        assert store_name("Warehouse", "central_store", "North Star") == "North Star · Warehouse"

    def test_a_name_that_already_says_it_is_left_alone(self):
        assert store_name("North Star warehouse", "central_store", "North Star") == "North Star warehouse"

    def test_workers_and_supplier_sites_are_not_prefixed(self):
        assert store_name("flw_001", "user_held", "North Star") == "flw_001"
        assert store_name("DABS depot", "supplier_site", "North Star") == "DABS depot"
        assert store_name("Warehouse", "central_store", "") == "Warehouse"

    def test_a_workers_partner_is_found_up_the_tree(self):
        points = {
            1: {"id": 1, "managed_by_org_id": 7, "parent_supply_point_id": None},
            2: {"id": 2, "managed_by_org_id": None, "parent_supply_point_id": 1},
        }
        assert partner_of_point(points[2], points, {7: "North Star"}) == "North Star"
        assert partner_of_point(points[2], points, {}) == ""


class TestTheTopLineNamesEachTender:
    def test_each_tender_names_its_own_orders(self):
        tenders = [
            {"id": 1, "status": "awarded", "label": "Round 1"},
            {
                "id": 2,
                "status": "awarded",
                "label": "Demo split tender",
                "partner_splits": [
                    {"org_id": 11, "org_name": "North Star", "quantity": "900", "quantity_unit": "carton"},
                    {"org_id": 12, "org_name": "Riverbend", "quantity": "600", "quantity_unit": "carton"},
                    {"org_id": 13, "org_name": "Savanna", "quantity": "500", "quantity_unit": "carton"},
                ],
                "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
            },
        ]
        contracts = [
            {"id": 1, "status": "received", "tender_id": 1, "quantity": "100", "partner_org_id": 10},
            {"id": 2, "status": "placed", "tender_id": 2, "quantity": "900", "partner_org_id": 11},
            {"id": 3, "status": "received", "tender_id": 2, "quantity": "600", "partner_org_id": 12},
        ]
        line = next_step(tenders=tenders, contracts=contracts, points=[])["line"]
        assert "Round 1: 1 order placed · 1 received" in line
        assert "Demo split tender: 2 of 3 partners have ordered" in line and "1 received" in line
        assert "Round 1: 2 of 3" not in line


class TestOneUnit:
    PACK = {"pack_unit": "carton", "base_unit": "sachet", "per_pack": 150}

    def test_stock_is_said_in_sachets_with_whole_cartons_in_the_note(self):
        cell, note = _in_base_unit({"amount": "1288.4", "unit": "carton"}, self.PACK, "excludes goods in transit")
        assert cell == {"amount": cell["amount"], "unit": "sachet"} and int(cell["amount"]) == 193260
        assert note.startswith("1,288 cartons and 60 sachets")

    def test_without_a_pack_the_figure_is_left_as_it_was(self):
        cell = {"amount": "3", "unit": "carton"}
        assert _in_base_unit(cell, None, "n") == (cell, "n")

    def test_the_chain_row_prints_one_unit(self):
        cells = deliver_stages(
            {
                "network": {"supply_points": 3, "user_held": 2, "never_reported": 0},
                "on_hand": {"amount": "1288.4", "unit": "carton"},
                "in_transit": None,
                "distributions": {"runs": 1},
                "consumed": {"amount": "8940", "unit": "sachet"},
                "cover": {},
                "pack": self.PACK,
            }
        )
        by_label = {c["label"]: c["value"] for c in cells}
        assert "sachet" in by_label["On hand"] and "carton" not in by_label["On hand"]
        assert "sachet" in by_label["Given to children"]


# ---- the whole page, loaded once, with the shapes live data has -------------------------------------------

import pytest  # noqa: E402
from django.urls import reverse  # noqa: E402

from connect_labs.supply_chain.tests import test_standing as ts  # noqa: E402

da = ts.da
base = ts.base
sophie = ts.sophie
ace = ts.ace


client_in_program = ts.client_in_program


@pytest.mark.django_db
class TestTheOverviewLoads:
    def _world(self, da, base):
        order = ts._order(da, base, "PO-1")
        shipment = ts._ship(da, order, datetime.date(2026, 9, 5))
        ts._receive(da, base, order, shipment)
        ts._pay(da, ts._invoice(da, order))
        return order

    def test_the_page_loads_with_the_checks_of_a_received_and_paid_order(self, client_in_program, da, base):
        self._world(da, base)
        response = client_in_program.get(reverse("supply_chain:overview"))
        assert response.status_code == 200
        body = response.content.decode()
        assert 'data-testid="chase-list"' in body
        # No second copy of the checks, the tenders and the orders: one link each.
        assert 'data-testid="fold-checks"' not in body and 'data-testid="fold-tenders"' not in body
        assert 'data-testid="fold-contracts"' not in body
        assert 'data-testid="all-tenders-link"' in body and 'data-testid="all-orders-link"' in body
        # The key sits with the table it explains.
        assert body.index('data-testid="standing-table"') < body.index('data-testid="source-legend"')
        assert "Counts at each stage" in body and "The chain" not in body

    def test_partner_and_issue_rows_render_through_the_page(self, client_in_program, da, base, monkeypatch):
        from connect_labs.supply_chain.portfolio import map_data

        gaps = [_gap(2, "flw_001", "-7"), _gap(3, "flw_002", "-9")]
        expiring = _check(
            "stock_expiring",
            label="Warehouse \u2014 batch DEMO-B1",
            facts={"quantity": "40 carton", "expiry": "2027-01-15", "expired": False, "batch": "DEMO-B1"},
        )
        rows = _items([*gaps, expiring])
        monkeypatch.setattr(map_data, "program_chase", lambda access, program_id, name: rows)
        body = client_in_program.get(reverse("supply_chain:overview")).content.decode()
        assert 'data-audience="partner"' in body and "Partners to chase" in body
        assert "North Star: 2 FLWs 7\u20139 sachets below records" in body
        assert "40 cartons at North Star \u00b7 Warehouse expire 15 Jan 2027: issue these first" in body
        # The header counts the rows the list shows, not the checks behind them.
        assert re.search(r"To chase <span[^>]*>\s*2\s*</span>", body)


# ---- stores are named by partner on the pages that show them ----------------------------------------------


@pytest.mark.django_db
class TestStoresNamedByPartnerOnThePages:
    def _world(self, da, base):
        partner = ts.op(da, "org_upsert", ts.AUG_3, data={"slug": "ns", "name": "North Star"})
        store = ts.op(
            da,
            "supply_point_upsert",
            ts.AUG_3,
            data={
                "slug": "ns-wh",
                "name": "Warehouse",
                "kind": "central_store",
                "managed_by_org_id": partner["id"],
                "source": "we_recorded",
            },
        )
        return partner, store

    def test_the_tag_prefixes_the_partner_and_leaves_workers_alone(self, da, base):
        from django.template import Context, Template
        from django.test import RequestFactory

        partner, store = self._world(da, base)
        request = RequestFactory().get("/supply/")
        request.labs_context = {"program_id": ts.PROGRAM}
        out = Template(
            "{% load supply_chain_extras %}{% store_label sid 'Warehouse' %}|{% store_label 0 'Elsewhere' %}"
        ).render(Context({"request": request, "sid": store["id"]}))
        assert out == "North Star · Warehouse|Elsewhere"

    def test_the_tag_without_a_request_just_prints_the_name(self):
        from django.template import Context, Template

        assert (
            Template("{% load supply_chain_extras %}{% store_label 1 'Warehouse' %}").render(Context()) == "Warehouse"
        )

    def test_the_issues_panel_says_whose_warehouse_it_is(self, da, base):
        from connect_labs.supply_chain.issues import open_issues

        partner, store = self._world(da, base)
        ts.op(
            da,
            "movement_record",
            ts.AUG_3,
            data={
                "kind": "adjustment",
                "to_supply_point_id": store["id"],
                "occurred_on": "2026-08-03",
                "commodity_slug": "rutf",
                "quantity": "100",
                "quantity_unit": "carton",
                "batch": "B1",
                "source": "we_recorded",
            },
        )
        ts.op(
            da,
            "stock_issue_report",
            ts.AUG_20,
            data={
                "supply_point_id": store["id"],
                "kind": "missing",
                "occurred_on": "2026-08-20",
                "commodity_slug": "rutf",
                "quantity": "8",
                "quantity_unit": "carton",
                "batch": "B1",
                "reported_by": "A supervisor",
                "source": "we_recorded",
            },
        )
        (row,) = open_issues(ts.PROGRAM)
        assert "at North Star · Warehouse" in row["title"]

    def test_the_issues_and_plan_and_flow_pages_load(self, client_in_program, da, base):
        self._world(da, base)
        for name in ("issues", "plan", "plan_stores", "flow"):
            response = client_in_program.get(reverse(f"supply_chain:{name}"))
            assert response.status_code == 200, name

    def test_where_it_went_names_a_store_by_its_partner(self, da, base):
        from connect_labs.supply_chain.models import SupplyPoint
        from connect_labs.supply_chain.stock.services.flow import _shown_names

        partner, store = self._world(da, base)
        points = list(SupplyPoint.objects.filter(pk=store["id"]))
        assert _shown_names(ts.PROGRAM, points) == {store["id"]: "North Star · Warehouse"}
