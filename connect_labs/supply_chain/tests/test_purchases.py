"""Round 3, "organise Supply around workflows": Purchases, and one page per purchase from sizing to payment.

Sophie Feintuch's walk of the demo (Nov 2026). THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest
from django.urls import reverse

from connect_labs.supply_chain import purchases
from connect_labs.supply_chain.tests import purchase_walk_world as walk
from connect_labs.supply_chain.tests import test_views as views_fixtures
from connect_labs.supply_chain.tests.chlorine_world import op

pytestmark = pytest.mark.django_db

da = walk.da
account = walk.account
client_in_programme = walk.client_in_programme
sophie = views_fixtures.sophie


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
    assert 'data-testid="what-happened"' in body and "Something else" in body
    # each row offers the one thing it is waiting for: an invoice for the delivered order, the arrival for the other
    assert "Record the invoice" in body and "Mark as arrived" in body
    # a delivered order is not offered a dispatch again
    north = body.split('data-testid="order-row"')[1].split("</tr>")[0]
    assert "It was dispatched" not in north
    # the money, and the way on
    assert "1 invoice to pay" in body
    assert "expected 20 Nov" in body
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


# ---- round 3, step 1 follow-ups (Sophie Feintuch's walk, 18 Nov) ----------------------------------------------


def _table(body):
    return body.split('data-testid="purchase-table"')[1]


def test_an_open_invoice_is_the_next_move_when_nothing_else_is_left(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river", "savanna"))
    for key in ("north", "river", "savanna"):
        walk.deliver(da, world, key)
    _invoice(da, world, "north")
    body = client_in_programme.get(reverse("supply_chain:purchases") + "?show=all").content.decode()
    table = _table(body)
    assert "Invoice to pay" in table
    assert (
        "Pay Dabs Nutritional Products Ltd&#x27;s invoice" in table
        or "Pay Dabs Nutritional Products Ltd's invoice" in table
    )
    assert "Nothing to do" not in table


def test_a_purchase_delivered_and_paid_reads_done(client_in_programme, da, monkeypatch):
    from connect_labs.supply_chain.models import Invoice

    world = walk.build(da, monkeypatch, ordered=("north", "river", "savanna"))
    for key in ("north", "river", "savanna"):
        walk.deliver(da, world, key)
        _invoice(da, world, key)
    Invoice.objects.all().update(status="paid")
    table = _table(client_in_programme.get(reverse("supply_chain:purchases") + "?show=all").content.decode())
    assert 'data-testid="purchase-stage">Done' in table.replace("\n", "").replace("  ", "") or "Done" in table
    assert "Ordered" not in table.split('data-testid="purchase-stage"')[1].split("</td>")[0]


def test_many_silent_suppliers_are_counted_not_one_named(client_in_programme, da, monkeypatch):
    walk.build(da, monkeypatch, ordered=())
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Feb re-quote",
            "lines": [{"commodity_slug": "rutf", "quantity": "800", "quantity_unit": "carton"}],
        },
    )
    from connect_labs.supply_chain.models import Tender

    Tender.objects.filter(pk=tender["id"]).update(status="open")
    for name in ("Ariel", "Hilina", "Nutri K"):
        sup = op(da, "supplier_create", data={"name": name})
        op(
            da,
            "outreach_log",
            data={"tender_id": tender["id"], "supplier_id": sup["id"], "channel": "manual", "sent_on": "2026-09-01"},
        )
    body = client_in_programme.get(reverse("supply_chain:purchases") + "?show=all").content.decode()
    assert "suppliers who have not replied" in body


def test_products_are_named_short_and_prices_are_delivered_prices(client_in_programme, da, monkeypatch):
    from connect_labs.supply_chain.models import Commodity
    from connect_labs.supply_chain.procurement.sourcing import short_product

    assert short_product("RUTF (ready-to-use therapeutic food)") == "RUTF"
    assert short_product("RUTF, 92 g sachet") == "RUTF"
    assert short_product("Chlorine tablets") == "Chlorine tablets"
    walk.build(da, monkeypatch, ordered=())
    Commodity.objects.filter(slug="rutf").update(name="RUTF (ready-to-use therapeutic food)")
    body = client_in_programme.get(reverse("supply_chain:purchases") + "?show=all").content.decode()
    table = _table(body)
    assert "ready-to-use" not in table and "landed price" not in table


def test_the_direct_purchase_line_and_short_form(client_in_programme, da, monkeypatch):
    walk.build(da, monkeypatch, ordered=())
    body = client_in_programme.get(reverse("supply_chain:purchases")).content.decode()
    assert 'data-testid="direct-purchase-note"' in body
    assert reverse("supply_chain:purchase_outside_tender") in body
    form = client_in_programme.get(reverse("supply_chain:purchase_outside_tender"))
    assert form.status_code == 200
    page = form.content.decode()
    assert 'data-testid="more-options"' in page and "Bought or donated" in page and "Who from" in page
    # the questions it takes are above the fold, the jargon below it
    assert page.index("Bought or donated") < page.index('data-testid="more-options"') < page.index("Freight")


def test_a_donation_can_be_recorded_from_the_short_form(client_in_programme, da, monkeypatch):
    from connect_labs.supply_chain.models import Contract

    world = walk.build(da, monkeypatch, ordered=())
    from connect_labs.supply_chain.models import Commodity

    commodity = Commodity.objects.get(slug="rutf")
    response = client_in_programme.post(
        reverse("supply_chain:purchase_outside_tender"),
        {
            "supplier": world["supplier"]["id"],
            "commodity": commodity.pk,
            "buyer_of_record": "programme_org",
            "buyer_org": world["orgs"]["north"]["id"],
            "consideration": "in_kind",
            "payment_terms": "on_delivery",
            "quantity": "40",
            "quantity_unit": "carton",
            "status": "placed",
            "freight_basis": "not_specified",
            "duties_basis": "not_specified",
            "vat_basis": "not_specified",
            "source": "we_recorded",
        },
    )
    assert response.status_code == 302, response.content.decode()[:600]
    assert Contract.objects.filter(consideration="in_kind", quantity=40).exists()


def test_a_partner_buys_order_offers_the_llos_invoice_not_the_suppliers_or_an_advance():
    contract = {"id": 9, "buying_model": "partner_buys", "consideration": "priced"}
    acts = purchases.order_actions(contract, [{"id": 1}], [], delivered=True, in_transit=False)
    assert acts["primary"]["label"] == "Record the LLO's invoice" and "from=partner" in acts["primary"]["url"]
    labels = [m["label"] for m in acts["more"]]
    assert "An advance was paid" not in labels and "Record the invoice" not in labels


def test_the_next_event_follows_the_order(client_in_programme):
    contract = {"id": 9, "consideration": "priced"}
    assert (
        purchases.order_actions(contract, [], [], delivered=False, in_transit=False)["primary"]["label"]
        == "Record a dispatch"
    )
    assert (
        purchases.order_actions(contract, [{"id": 1}], [], delivered=False, in_transit=True)["primary"]["label"]
        == "Mark as arrived"
    )
    assert (
        purchases.order_actions(contract, [{"id": 1}], [], delivered=True, in_transit=False)["primary"]["label"]
        == "Record the invoice"
    )
    invoiced = [{"id": 4, "contract_id": 9, "status": "received"}]
    assert (
        purchases.order_actions(contract, [{"id": 1}], invoiced, delivered=True, in_transit=False)["primary"]["label"]
        == "Record payment"
    )
    paid = [{"id": 4, "contract_id": 9, "status": "paid"}]
    assert purchases.order_actions(contract, [{"id": 1}], paid, delivered=True, in_transit=False)["primary"] is None


def test_done_steps_fold_and_the_live_step_stays_open(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north", "river"))
    body = client_in_programme.get(reverse("supply_chain:purchase", args=[world["tender"]["id"]])).content.decode()
    done = [f for f in body.split("<section")[1:] if 'data-step-state="done"' in f.split(">")[0]]
    assert done and all("<details  class" in d or "<details class" in d for d in done)  # closed: no `open`
    assert 'data-step-state="now"' in body
    live = [f for f in body.split("<section")[1:] if 'data-step-state="now"' in f.split(">")[0]][0]
    assert "<details open" in live


def test_the_summaries_do_not_contradict_themselves(client_in_programme, da, monkeypatch):
    world = walk.build(da, monkeypatch, ordered=("north",))
    body = client_in_programme.get(reverse("supply_chain:purchase", args=[world["tender"]["id"]])).content.decode()
    assert "0 asked" not in body
    assert "Chosen: Dabs Nutritional Products Ltd" in body
    assert "Awarded to Dabs Nutritional Products Ltd" in body


def test_without_a_program_the_page_lists_the_users_programs(client, sophie, monkeypatch):
    from connect_labs.labs import context as labs_context

    monkeypatch.setattr(
        labs_context, "get_org_data", lambda request: {"programs": [{"id": 77, "name": "Demo program"}]}
    )
    body = client.get(reverse("supply_chain:purchases")).content.decode()
    assert 'data-testid="program-picker"' in body and "Demo program" in body and "program_id=77" in body


def test_after_the_award_only_the_winner_is_asked_for_papers(client_in_programme, da, monkeypatch):
    """Round 3: "To ask for now" asked the losing suppliers for a certificate "with this delivery"."""
    from connect_labs.supply_chain.models import Commodity

    world = walk.build(da, monkeypatch, ordered=())
    Commodity.objects.filter(slug="rutf").update(category="therapeutic_food")
    loser = op(da, "supplier_create", data={"name": "Ariel Nutrition"})
    op(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": loser["id"],
            "as_quoted_amount": "60.00",
            "as_quoted_unit": "per_pack",
            "quantity_basis": "2000",
            "quantity_basis_unit": "carton",
            "base_per_pack_stated": 150,
            "pack_spec_source": "stated_on_quote",
            "freight_basis": "included",
            "duties_basis": "included",
        },
    )
    body = client_in_programme.get(
        reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
    ).content.decode()
    assert 'data-testid="award-card"' in body
    gaps = (
        body.split('data-testid="qualification-gaps"')[1].split("</div>")[0]
        if 'data-testid="qualification-gaps"' in body
        else ""
    )
    assert "Dabs Nutritional Products Ltd" in gaps  # the winner is still asked (so this checks something)
    assert "Ariel Nutrition" not in gaps
