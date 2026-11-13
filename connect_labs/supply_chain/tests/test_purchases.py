"""Round 3, "organise Supply around workflows": Purchases, and one page per purchase from sizing to payment.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain import purchases
from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests.chlorine_world import op

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme


def _invoice(da, world, key, status="received", direction="from_supplier"):
    contract = world["contracts"][key]
    return op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "reference": f"INV-{key}",
            "issued_on": "2026-10-25",
            "amount": "1000",
            "currency": "USD",
            "source": "supplier_reported",
            **({"direction": direction} if direction != "from_supplier" else {}),
        },
    )


# ---- the pure arranging -------------------------------------------------------------------------------------


def test_money_state_says_none_owed_or_paid():
    contract = {"id": 1, "consideration": "priced"}
    assert purchases.money_state(contract, [])["state"] == "none"
    owed = purchases.money_state(contract, [{"contract_id": 1, "status": "received"}])
    assert owed["state"] == "owed" and owed["words"] == "1 invoice to pay"
    assert purchases.money_state(contract, [{"contract_id": 1, "status": "paid"}])["state"] == "paid"
    # a rejected invoice is not money owed
    assert purchases.money_state(contract, [{"contract_id": 1, "status": "rejected"}])["state"] == "none"


def test_a_partners_invoice_counts_as_owed_and_says_to_whom():
    contract = {"id": 1, "consideration": "priced"}
    owed = purchases.money_state(contract, [{"contract_id": 1, "status": "received", "direction": "from_partner"}])
    assert owed["state"] == "owed" and owed["owed_to"] == "partner"


def test_a_donated_order_is_not_billed():
    assert purchases.money_state({"id": 1, "consideration": "in_kind"}, [])["state"] == "not_billed"


# ---- the pages -----------------------------------------------------------------------------------------------


def test_the_menu_has_purchases_and_not_sourcing_or_orders(client_in_programme, da, monkeypatch):
    walk.build(da, monkeypatch, ordered=())
    body = client_in_programme.get(reverse("supply_chain:purchases")).content.decode()
    labels = [t.split("<")[0].strip() for t in body.split('data-testid="supply-tab-group"')[1:]]
    assert labels  # the menu is drawn
    assert ">Purchases<" in body
    assert ">Sourcing<" not in body and ">Orders<" not in body


def test_the_old_sourcing_address_goes_to_purchases_and_keeps_the_program(client_in_programme):
    response = client_in_programme.get(reverse("supply_chain:procurement_tender_board") + "?program_id=10172")
    assert response.status_code == 302
    assert response["Location"] == reverse("supply_chain:purchases") + "?program_id=10172"


def test_the_list_links_each_tender_to_its_purchase_page(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    walk.in_transit(da, world, "river")
    response = client_in_programme.get(reverse("supply_chain:purchases") + "?show=all")
    assert response.status_code == 200
    body = response.content.decode()
    assert reverse("supply_chain:purchase", args=[world["tender"]["id"]]) in body
    assert "Ordered 2 of 3 · Delivered 1 of 3" in body
    assert "Record a purchase outside a tender" in body
    assert reverse("supply_chain:orders") in body  # "All orders (records)"


def test_the_filters_stand_in_for_the_orders_list(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    walk.in_transit(da, world, "river")
    _invoice(da, world, "north")
    base = reverse("supply_chain:purchases")
    transit = client_in_programme.get(base + "?show=transit").content.decode()
    assert "Demo split tender" in transit
    unpaid = client_in_programme.get(base + "?show=unpaid").content.decode()
    assert "Demo split tender" in unpaid
    # a tender nothing has been bought on is neither in transit nor unpaid
    op(
        da,
        "tender_create",
        data={
            "label": "Feb re-quote",
            "lines": [{"commodity_slug": "rutf", "quantity": "800", "quantity_unit": "carton"}],
        },
    )

    def table(show):
        # the guide above the table names every tender; only the table is narrowed
        return (
            client_in_programme.get(base + f"?show={show}").content.decode().split('data-testid="purchase-table"')[1]
        )

    assert "Feb re-quote" not in table("transit")
    assert "Feb re-quote" not in table("unpaid")
    assert "Feb re-quote" in table("active")


def test_a_purchase_outside_a_tender_is_listed(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=())
    op(
        da,
        "contract_create",
        data={
            "supplier_id": world["supplier"]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": world["orgs"]["north"]["id"],
            "quantity": "40",
            "quantity_unit": "carton",
            "reference": "DIRECT-1",
            "source": "we_recorded",
        },
    )
    body = client_in_programme.get(reverse("supply_chain:purchases") + "?show=all").content.decode()
    assert 'data-testid="direct-row"' in body and "DIRECT-1" in body


def test_the_purchase_page_lays_out_the_seven_steps_and_each_llo(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    walk.deliver(da, world, "north")
    walk.in_transit(da, world, "river", expected="2026-11-20")
    _invoice(da, world, "north")
    response = client_in_programme.get(reverse("supply_chain:purchase", args=[world["tender"]["id"]]))
    assert response.status_code == 200
    body = response.content.decode()
    for title in ("Size it", "Ask suppliers", "Compare", "Award", "Orders", "Delivered", "Paid"):
        assert title in body
    assert body.count('data-testid="order-row"') == 2
    # the LLO that has not ordered is on the page, saying so
    assert 'data-testid="order-waiting"' in body and "Savanna" in body and "Not ordered yet" in body
    # one place to say what happened, not a page per record type
    assert 'data-testid="what-happened"' in body and "It arrived" in body
    # the money, and the way on
    assert "1 invoice to pay" in body
    assert "expected 20 Nov 2026" in body
    assert reverse("supply_chain:award_detail", args=[world["award"]["id"]]) in body


def test_the_next_step_is_the_first_one_not_done(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    body = client_in_programme.get(reverse("supply_chain:purchase", args=[world["tender"]["id"]])).content.decode()
    # an award exists but Savanna has not ordered: orders is the step under way
    assert "Next: Orders." in body
    assert "Draft an order for each LLO" in body


def test_a_purchase_that_does_not_exist_is_a_404(client_in_programme, da, monkeypatch):
    walk.build(da, monkeypatch, ordered=())
    assert client_in_programme.get(reverse("supply_chain:purchase", args=[999999])).status_code == 404


def test_the_order_and_tender_pages_still_sit_under_the_purchases_tab(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north",))
    contract = world["contracts"]["north"]
    for url in (
        reverse("supply_chain:order_detail", args=[contract["id"]]),
        reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]]),
    ):
        body = client_in_programme.get(url).content.decode()
        assert 'aria-current="page"' in body or "is-active" in body
