"""What the IPTSc shortfall render (iteration 1) still showed, screen by screen.

THIS REPOSITORY IS PUBLIC. Every organisation, product, reference and figure
here is invented.

The world mirrors the walkthrough: an order of 700 treatment packets from a
distributor, who dispatches 450 through its own update link; the partner
receives them through ITS link; the partner buys the missing 250 locally out of
its setup fee, and receives them.
"""

import re
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Contract, Shipment, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

PROGRAM = 10641


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="ngozi", password="x", email="ngozi@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


def _issue(da, **data):
    issued = op(da, "update_link_issue", data=data)
    return issued, UpdateLink.objects.get(pk=issued["id"])


@pytest.fixture
def world(da):
    us = op(da, "org_upsert", data={"slug": "programme", "name": "The programme"})
    distributor_org = op(da, "org_upsert", data={"slug": "harmattan", "name": "Harmattan Health Supplies"})
    partner = op(da, "org_upsert", data={"slug": "schi", "name": "Sahel Community Health Initiative"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "iptsc-packet",
            "name": "IPTSc three-day treatment packet",
            "base_unit": "packet",
            "pack_unit": "carton",
            "base_per_pack": 50,
        },
    )
    op(da, "commodity_upsert", data={"slug": "dp-tablet", "name": "DP tablet", "base_unit": "tablet"})
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "iptsc-3day-packet",
            "name": "IPTSc three-day packet (standard)",
            "commodity_slug": "iptsc-packet",
            "base_unit": "packet",
            "pack_unit": "carton",
            "base_per_pack": 50,
            "components": [{"commodity_slug": "dp-tablet", "quantity": "9", "base_unit": "tablet"}],
            "one_course_is": "base_unit",
        },
    )
    harmattan = op(da, "supplier_create", data={"name": "Harmattan Health Supplies", "type": "distributor"})
    tamarind = op(da, "supplier_create", data={"name": "Tamarind Pharmacy Wholesale", "type": "trader"})
    store = op(
        da,
        "supply_point_upsert",
        data={
            "slug": "schi-store",
            "name": "SCHI district store",
            "kind": "regional_store",
            "managed_by_org_id": partner["id"],
            "source": "we_recorded",
        },
    )
    order = op(
        da,
        "contract_create",
        data={
            "supplier_id": harmattan["id"],
            "commodity_slug": "iptsc-packet",
            "item_id": item["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": us["id"],
            "reference": "IPTSC-PO-0715",
            "signed_on": "2026-07-15",
            "status": "confirmed",
            "quantity": "700",
            "quantity_unit": "packet",
            "unit_price": "1.80",
            "unit_price_unit": "per_base_unit",
            "currency": "USD",
            "freight_basis": "included",
            "duties_basis": "included",
            "vat_basis": "included",
            "delivery_supply_point_id": store["id"],
            "promised_lead_time_days": 45,
            "source": "we_recorded",
        },
    )
    distributor_issued, distributor_link = _issue(
        da, org_id=distributor_org["id"], contract_ids=[order["id"]], label="Harmattan"
    )
    partner_issued, partner_link = _issue(
        da, org_id=partner["id"], contract_ids=[order["id"]], supply_point_ids=[store["id"]], label="SCHI"
    )
    return {
        "us": us,
        "partner": partner,
        "item": item,
        "harmattan": harmattan,
        "tamarind": tamarind,
        "store": store,
        "order": order,
        "distributor_token": distributor_issued["token"],
        "partner_token": partner_issued["token"],
        "distributor_link": distributor_link,
        "partner_link": partner_link,
    }


def _dispatch(world):
    return service.submit(
        world["distributor_link"],
        "record_shipment",
        {
            "contract": Contract.objects.get(pk=world["order"]["id"]),
            "reference": "HHS-WB-3310",
            "status": "dispatched",
            "carrier": "Kano Freight",
            "dispatched_on": date(2026, 9, 19),
            "expected_on": date(2026, 9, 22),
            "quantity": "450",
            "unit_basis": "base",
            "batch": "DP-2608-A",
        },
    )


def _receive(world, shipment):
    return service.submit(
        world["partner_link"],
        "record_receipt",
        {
            "contract": Contract.objects.get(pk=world["order"]["id"]),
            "supply_point": SupplyPoint.objects.get(pk=world["store"]["id"]),
            "shipment": Shipment.objects.get(pk=shipment["id"]),
            "received_on": date(2026, 9, 24),
            "reference": "SCHI-GRN-0922",
            "quantity_accepted": "450",
            "unit_basis": "base",
            "batch": "DP-2608-A",
        },
    )


def _cover(da, world, received=True):
    cover = op(
        da,
        "contract_create",
        data={
            "supplier_id": world["tamarind"]["id"],
            "commodity_slug": "iptsc-packet",
            "item_id": world["item"]["id"],
            "buyer_of_record": "partner_org",
            "buyer_org_id": world["partner"]["id"],
            "reference": "SCHI-LP-0921",
            "status": "placed",
            "consideration": "bundled",
            "quantity": "250",
            "quantity_unit": "packet",
            "delivery_supply_point_id": world["store"]["id"],
            "covers_shortfall_of_id": world["order"]["id"],
            "source": "partner_reported",
        },
    )
    if received:
        op(
            da,
            "receipt_record",
            data={
                "contract_id": cover["id"],
                "supply_point_id": world["store"]["id"],
                "reference": "SCHI-GRN-0923",
                "received_on": "2026-09-22",
                "source": "we_recorded",
                "lines": [{"item_id": world["item"]["id"], "quantity_accepted": "250", "quantity_unit": "packet"}],
            },
        )
    return cover


@pytest.fixture
def played(da, world):
    """The whole story: dispatched, received short, covered, cover received."""
    shipment = _dispatch(world)
    _receive(world, shipment)
    world["cover"] = _cover(da, world)
    world["shipment"] = shipment
    return world


def _visible(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _page(client, name, *args, **query):
    url = reverse(f"supply_chain:{name}", args=args)
    if query:
        url += "?" + "&".join(f"{k}={v}" for k, v in query.items())
    response = client.get(url)
    assert response.status_code == 200, response.content.decode()[:3000]
    return response.content.decode()


def _public(client, token, done=""):
    url = reverse("supply_chain:update_link_public", args=[token])
    return client.get(url + (f"?done={done}" if done else "")).content.decode()


# ---- 1. each link's panel lists only what came through that link -----------


class TestEachLinkPanelListsOnlyItsOwnRecords:
    def test_the_dispatch_sits_under_the_distributor_and_the_receipt_under_the_partner(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        panels = {}
        for chunk in body.split('data-link-updates="')[1:]:
            org, rest = chunk.split('"', 1)
            panels[org] = _visible(rest.split("</section>", 1)[0])
        assert set(panels) == {"Harmattan Health Supplies", "Sahel Community Health Initiative"}
        assert "HHS-WB-3310" in panels["Harmattan Health Supplies"]
        assert "SCHI-GRN-0922" not in panels["Harmattan Health Supplies"]
        assert "SCHI-GRN-0922" in panels["Sahel Community Health Initiative"]
        assert "HHS-WB-3310: 450" not in panels["Sahel Community Health Initiative"]


# ---- 2. the match card agrees with the fulfilment table ----------------------


class TestACoveredShortOrderHasNothingOutstanding:
    def test_the_card_shows_the_cover_and_nothing_still_outstanding(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        card = _visible(body.split('id="match"', 1)[1].split("</table>", 1)[0])
        # "on this order": the cover's 250 is received on the other one (iteration 2).
        assert "Received on this order 450 packets" in card
        assert re.search(r"Covered by SCHI-LP-0921 from Tamarind Pharmacy Wholesale 250 packets", card)
        assert "Still outstanding 0 packets" in card
        assert "Still outstanding 250" not in card

    def test_an_uncovered_short_order_still_says_what_is_outstanding(self, scoped, da, world):
        _receive(world, _dispatch(world))
        body = _page(scoped, "order_detail", world["order"]["id"])
        card = _visible(body.split('id="match"', 1)[1].split("</table>", 1)[0])
        assert "Still outstanding 250 packets" in card


# ---- 3. stock across the network ---------------------------------------------


class TestTheStockPage:
    def test_no_consumption_rate_is_not_painted_as_a_warning(self, scoped, played):
        # Said once, across the forecast columns (iteration 2), and never orange.
        body = _page(scoped, "stock")
        assert "No consumption yet" in body
        assert not re.search(r"text-orange-700[^>]*>\s*No consumption yet", body)

    def test_one_supply_point_is_singular(self, scoped, played):
        text = _visible(_page(scoped, "stock"))
        assert "1 supply point · 1 has no count yet" in text

    def test_a_kit_leads_with_the_packets_it_is_counted_in(self, scoped, played):
        body = _page(scoped, "stock")
        cell = body.split('data-ledger="', 1)[1].split("</td>", 1)[0]
        text = _visible(cell)
        assert text.index("700 packets") < text.index("14 cartons")

    def test_the_balance_links_to_the_movements_behind_it(self, scoped, played):
        body = _page(scoped, "stock")
        cell = body.split('data-ledger="', 1)[1].split("</td>", 1)[0]
        href = re.search(r'href="([^"]+)"', cell).group(1).replace("&amp;", "&")
        assert href.startswith(reverse("supply_chain:movements"))
        assert f"supply_point_id={played['store']['id']}" in href
        assert f"item_id={played['item']['id']}" in href
        history = _visible(scoped.get(href).content.decode())
        assert "SCHI district store" in history
        assert "450 packets" in history and "250 packets" in history
        assert "SCHI-GRN-0922" in history and "SCHI-GRN-0923" in history


# ---- 4. the trade item page names the item, not its code ----------------------


class TestTheTradeItemPage:
    def test_the_breadcrumb_names_the_item_and_the_code_is_not_a_chip_under_the_title(self, scoped, played):
        body = _page(scoped, "item_detail", played["item"]["id"])
        crumb = _visible(body.split('<nav class="text-sm', 1)[1].split("</nav>", 1)[0])
        assert crumb.endswith("IPTSc three-day packet (standard)")
        assert "iptsc-3day-packet" not in crumb
        header = body.split("<h1", 1)[1].split("How it is packed", 1)[0]
        assert "iptsc-3day-packet" not in header
        # Still findable, among the identifiers.
        assert "iptsc-3day-packet" in body


# ---- 5. the update link page ---------------------------------------------------


class TestTheUpdateLinkPage:
    def test_the_receiving_partner_is_not_offered_a_dispatch(self, client, world):
        _dispatch(world)
        body = _public(client, world["partner_token"])
        assert "record a dispatch" not in body.lower()
        assert "move a dispatch along" not in body.lower()
        titles = re.findall(r'<span class="text-base font-semibold text-gray-900">([^<]+)</span>', body)
        assert titles[0] == "Record goods received"

    def test_the_distributor_is_still_offered_a_dispatch(self, client, world):
        body = _public(client, world["distributor_token"])
        titles = re.findall(r'<span class="text-base font-semibold text-gray-900">([^<]+)</span>', body)
        assert "Record a dispatch" in titles

    def test_a_dispatch_line_says_when_it_left_and_how(self, client, world):
        _dispatch(world)
        text = _visible(_public(client, world["distributor_token"]))
        assert "HHS-WB-3310 — 450 packets, dispatched, left 19 Sep via Kano Freight, expected 22 Sep" in text

    def test_a_delivered_dispatch_says_when_it_arrived_not_when_it_was_expected(self, client, world):
        _receive(world, _dispatch(world))
        text = _visible(_public(client, world["partner_token"]))
        assert "HHS-WB-3310 — 450 packets, left 19 Sep via Kano Freight, delivered 24 Sep" in text
        assert "expected 22 Sep" not in text

    def test_the_banner_is_a_sentence_about_the_dispatch(self, client, world):
        _dispatch(world)
        text = _visible(_public(client, world["distributor_token"], done="record_shipment"))
        assert (
            "Recorded: dispatch HHS-WB-3310, 450 packets, batch DP-2608-A. The programme team can see it now." in text
        )
        assert "record a dispatch —" not in text

    def test_the_banner_is_a_sentence_about_the_receipt(self, client, world):
        _receive(world, _dispatch(world))
        text = _visible(_public(client, world["partner_token"], done="record_receipt"))
        assert "Recorded: receipt SCHI-GRN-0922, 450 packets accepted, batch DP-2608-A at SCHI district store." in text
        assert "record goods received —" not in text


# ---- 6. a bundled order has nothing to bill ------------------------------------


class TestABundledOrderPage:
    def test_it_offers_no_invoice(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        assert "Record an invoice" not in body
        assert reverse("supply_chain:invoice_record", args=[played["cover"]["id"]]) not in body

    def test_the_evidence_note_names_no_duty_relief(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        evidence = _visible(body.split(">Evidence<", 1)[1])
        assert "duty relief" not in evidence

    def test_the_cost_card_is_not_about_importing(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        card = _visible(body.split('id="cost"', 1)[1].split("</div>", 2)[1])
        assert "Cost" in _visible(body.split('id="cost"', 1)[1].split("</span>", 1)[0])
        assert "assuming" not in _visible(body.split('id="cost"', 1)[1].split("<table", 1)[0])
        assert "Landed cost" not in card

    def test_a_bought_order_still_offers_an_invoice_and_names_the_duty_relief(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        assert "Record an invoice" in body
        assert "Landed cost" in body


# ---- 7. the sticky top bar does not hide what an anchor scrolls to --------------


def test_the_order_page_leaves_room_under_the_top_bar(scoped, played):
    body = _page(scoped, "order_detail", played["order"]["id"])
    assert "scroll-margin-top" in body


# ---- 8. the order header -------------------------------------------------------


class TestTheOrderHeader:
    def test_it_names_the_trade_item_the_delivery_point_and_who_told_us(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        header = _visible(body.split("Orders</a> ›", 1)[1].split("Edit order", 1)[0])
        assert "IPTSc three-day packet (standard)" in header
        assert "SCHI district store" in header
        assert "a partner told us" in header


# ---- 9. the orders list --------------------------------------------------------


class TestTheOrdersList:
    def test_each_order_says_when_it_is_due_and_how_much_arrived(self, scoped, played):
        text = _visible(_page(scoped, "orders"))
        assert "due 29 Aug 2026" in text
        assert "450 of 700 packets received" in text
        assert "250 of 250 packets received" in text
