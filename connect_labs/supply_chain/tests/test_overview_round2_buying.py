"""Round 2, Overview buying table: a split tender is one row with its partners' orders beneath it.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.standing import add_open_check_moves, standing_rows
from connect_labs.supply_chain.tests import test_standing as ts

da = ts.da
base = ts.base
sophie = ts.sophie
ace = ts.ace
client_in_program = ts.client_in_program
PROGRAM = ts.PROGRAM


@pytest.fixture
def split(da, base):
    """A split tender across three partners: two have ordered (one delivered), one has not."""
    partners = [
        ts.op(da, "org_upsert", ts.AUG_3, data={"slug": f"p{n}", "name": name})
        for n, name in enumerate(("North Star", "Riverbend", "Savanna"))
    ]
    tender = ts.op(
        da,
        "tender_create",
        ts.AUG_3,
        data={
            "label": "Demo split tender",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
        },
    )
    ts.op(
        da,
        "tender_update",
        ts.AUG_3,
        tender_id=tender["id"],
        data={
            "status": "awarded",
            "partner_splits": [
                {"org_id": p["id"], "quantity": q, "quantity_unit": "carton"}
                for p, q in zip(partners, ("900", "600", "500"))
            ],
        },
    )
    orders = []
    for partner, quantity, ref in ((partners[0], "900", "PO-NS"), (partners[1], "600", "PO-RB")):
        orders.append(
            ts.op(
                da,
                "contract_create",
                ts.AUG_20,
                data={
                    "supplier_id": base["suppliers"][0]["id"],
                    "commodity_slug": "rutf",
                    "buyer_of_record": "programme_org",
                    "buyer_org_id": base["us"]["id"],
                    "partner_org_id": partner["id"],
                    "reference": ref,
                    "quantity": quantity,
                    "quantity_unit": "carton",
                    "status": "placed",
                    "source": "we_recorded",
                    "tender_id": tender["id"],
                },
            )
        )
    shipment = ts._ship(da, orders[1], datetime.date(2026, 11, 20))
    return {"tender": tender, "partners": partners, "orders": orders, "shipment": shipment}


@pytest.mark.django_db
class TestASplitTenderIsOneRow:
    def test_the_tender_row_lists_every_partner_including_the_one_who_has_not_ordered(self, da, split):
        rows = standing_rows(PROGRAM, ts.TODAY)
        (tender_row,) = (r for r in rows if r.kind == "tender")
        assert [(p["name"], p["state"]) for p in tender_row.partners] == [
            ("North Star", "ordered"),
            ("Riverbend", "ordered"),
            ("Savanna", "waiting"),
        ]
        assert tender_row.partners[2]["status"] == "Not ordered yet"
        assert "Ordered 2 of 3" in tender_row.sub and "Savanna to order" in tender_row.sub
        assert tender_row.stage == "Ordered · 2 of 3 partners"
        # The partners' orders are drawn beneath it, not as rows of their own.
        assert {r.under_tender for r in rows if r.kind == "order"} == {split["tender"]["id"]}

    def test_an_order_for_a_partner_leads_with_the_partner(self, da, split):
        orders = [r for r in standing_rows(PROGRAM, ts.TODAY) if r.kind == "order"]
        assert "North Star · 900 cartons from Northwind Foods" in {r.title for r in orders}

    def test_the_page_draws_one_row_with_three_partners_beneath(self, client_in_program, da, split):
        response = client_in_program.get(reverse("supply_chain:overview"))
        assert response.status_code == 200
        body = response.content.decode()
        assert body.count('data-testid="overview-row"') == 1
        assert body.count('data-testid="row-partner"') == 3
        assert re.search(r'data-state="waiting"[^>]*>.*?Savanna.*?Not ordered yet', body, re.S)
        assert "North Star" in body and "Riverbend" in body

    def test_orders_list_has_a_partner_column_and_the_same_status_words(self, client_in_program, da, split):
        body = client_in_program.get(reverse("supply_chain:orders")).content.decode()
        assert ">For partner</th>" in body
        assert body.count('data-testid="order-partner"') == 2
        status = re.findall(r'data-testid="order-status">(.*?)</span>', body, re.S)
        assert any("In transit" in s for s in status) and not any("· in transit" in s for s in status)


@pytest.mark.django_db
class TestNextMoveAgreesWithTheChaseList:
    def test_a_received_order_still_owes_payment_confirmation_and_a_certificate(self, da, base):
        order = ts._order(da, base, "PO-1")
        shipment = ts._ship(da, order, datetime.date(2026, 9, 5))
        ts._receive(da, base, order, shipment)
        rows = standing_rows(PROGRAM, ts.TODAY)
        row = next(r for r in rows if r.kind == "order")
        assert row.next_move is None
        checks = [
            {
                "kind": "payment_unconfirmed",
                "subject": {"type": "payment", "id": 1, "label": "Northwind Foods — PAY-1"},
                "facts": {"contract_id": order["id"], "supplier": {"name": "Northwind Foods"}},
            },
            {
                "kind": "shipment_without_certificate",
                "subject": {"type": "shipment", "id": shipment["id"], "label": "SH-1 — RUTF — Northwind Foods"},
                "facts": {"contract_id": order["id"], "supplier": {"name": "Northwind Foods"}},
            },
            {"kind": "stock_variance", "subject": {}, "facts": {}},
        ]
        add_open_check_moves(rows, checks)
        assert [m.text for m in row.theirs] == [
            "Ask Northwind Foods to confirm they received payment",
            "Ask Northwind Foods for the batch certificate",
        ]
        assert row.whose == "suppliers"

    def test_the_page_shows_them_as_the_orders_next_move(self, client_in_program, da, base):
        order = ts._order(da, base, "PO-1")
        ts._ship(da, order, datetime.date(2026, 9, 5))
        body = client_in_program.get(reverse("supply_chain:overview")).content.decode()
        assert "for the batch certificate" in body.split('id="supply-standing"')[1].split("</table>")[0]
