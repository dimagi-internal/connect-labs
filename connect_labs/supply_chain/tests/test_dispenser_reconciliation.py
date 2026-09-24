"""What the dispenser import's close showed: the order has to reconcile.

THIS REPOSITORY IS PUBLIC. Every organisation, reference and figure here is
invented.

The world mirrors the walkthrough: a donor gives 120 dispensers in kind; the
distributor's warehouse accepts 118 and refuses 2 through its own update link,
then releases 60 and 50 to two project sites. The order is where the import
closes -- how many came, how many were refused, and where the rest are now.
"""

import re
from html import unescape

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

PROGRAM = 10644


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="halima", password="x", email="halima@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def world(da):
    def org(slug, name):
        return op(da, "org_upsert", data={"slug": slug, "name": name})["id"]

    donor = org("donor-ngo", "ClearWater Action")
    distributor = org("distributor", "Harmattan Health Supplies")
    partner = org("partner", "Sahel Community Health Initiative")
    us = org("us", "The programme")
    supplier = op(da, "supplier_create", data={"name": "ClearWater Action", "type": "donor", "org_id": donor})
    op(
        da,
        "commodity_upsert",
        data={"slug": "dispenser", "name": "Chlorine dispenser", "category": "equipment", "base_unit": "unit"},
    )
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "disp-1",
            "name": "Standard dispenser",
            "commodity_slug": "dispenser",
            "base_unit": "unit",
            "pack_unit": "unit",
            "base_per_pack": 1,
            "stock_class": "durable",
        },
    )

    def point(slug, name, kind, managed_by):
        return op(
            da,
            "supply_point_upsert",
            data={"slug": slug, "name": name, "kind": kind, "managed_by_org_id": managed_by, "source": "we_recorded"},
        )["id"]

    warehouse = point("warehouse", "Harmattan warehouse", "central_store", distributor)
    dawaki = point("dawaki", "Dawaki project site", "facility", partner)
    rimi = point("rimi", "Rimi project site", "facility", partner)
    order = op(
        da,
        "contract_create",
        data={
            "supplier_id": supplier["id"],
            "item_id": item["id"],
            "commodity_slug": "dispenser",
            "reference": "DON-DISP-1",
            "status": "confirmed",
            "consideration": "in_kind",
            "quantity": "120",
            "quantity_unit": "unit",
            "currency": "USD",
            "buyer_of_record": "programme_org",
            "buyer_org_id": us,
            "delivery_supply_point_id": warehouse,
            "source": "we_recorded",
        },
    )
    return {
        "order": order,
        "item": item,
        "warehouse": warehouse,
        "dawaki": dawaki,
        "rimi": rimi,
        "distributor": distributor,
    }


def _receive(da, world, accepted="118", rejected="2"):
    line = {"item_id": world["item"]["id"], "quantity_accepted": accepted, "quantity_unit": "unit"}
    if rejected:
        line.update(quantity_rejected=rejected, rejection_reason="Tap housing cracked in transit")
    op(
        da,
        "receipt_record",
        data={
            "contract_id": world["order"]["id"],
            "supply_point_id": world["warehouse"],
            "received_on": "2026-09-20",
            "reference": "GRN-1",
            "source": "partner_reported",
            "lines": [line],
        },
    )


def _release(da, world, to, quantity):
    op(
        da,
        "movement_record",
        data={
            "kind": "transfer",
            "occurred_on": "2026-09-21",
            "from_supply_point_id": world["warehouse"],
            "to_supply_point_id": world[to],
            "item_id": world["item"]["id"],
            "commodity_slug": "dispenser",
            "quantity": quantity,
            "quantity_unit": "unit",
            "source": "partner_reported",
        },
    )


def _visible(html):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", html)))


def _order_page(client, world):
    response = client.get(reverse("supply_chain:order_detail", args=[world["order"]["id"]]))
    assert response.status_code == 200
    return response.content.decode()


def _card(body, anchor):
    return _visible(body.split(f'id="{anchor}"', 1)[1].split("</table>", 1)[0])


class TestRefusedIsNotOutstanding:
    def test_the_match_states_what_was_refused_and_what_is_still_to_arrive(self, da, world):
        _receive(da, world)
        match = op(da, "contract_match", contract_id=world["order"]["id"])
        assert match["refused"] == {"amount": "2", "unit": "unit"}
        assert match["still_to_arrive"] == {"amount": "0", "unit": "unit"}
        # Ordered less accepted, as it always was: other readers depend on it.
        assert match["outstanding"]["amount"] == "2"

    def test_with_nothing_refused_there_is_nothing_to_split(self, da, world):
        _receive(da, world, accepted="100", rejected=None)
        match = op(da, "contract_match", contract_id=world["order"]["id"])
        assert match["refused"]["amount"] == "0"
        assert match["still_to_arrive"] is None

    def test_refused_and_short_are_both_stated(self, da, world):
        _receive(da, world, accepted="100", rejected="5")
        match = op(da, "contract_match", contract_id=world["order"]["id"])
        assert match["refused"]["amount"] == "5"
        assert match["still_to_arrive"]["amount"] == "15"

    def test_the_card_says_refused_on_arrival_and_nothing_still_to_arrive(self, scoped, da, world):
        _receive(da, world)
        card = _card(_order_page(scoped, world), "match")
        assert "Refused on arrival 2 units" in card
        assert "Still outstanding 0 units" in card
        assert "Still outstanding 2" not in card
        assert "all arrived, some refused" in card

    def test_an_order_still_short_says_how_many_are_still_to_arrive(self, scoped, da, world):
        _receive(da, world, accepted="100", rejected="5")
        card = _card(_order_page(scoped, world), "match")
        assert "Refused on arrival 5 units" in card
        assert "Still outstanding 15 units" in card
        assert "all arrived" not in card


class TestWhereTheEquipmentIsNow:
    def test_the_order_page_says_where_each_unit_is_held(self, scoped, da, world):
        _receive(da, world)
        _release(da, world, "dawaki", "60")
        _release(da, world, "rimi", "50")
        panel = _card(_order_page(scoped, world), "where-now")
        assert "Harmattan warehouse" in panel and "8 units" in panel
        assert "Dawaki project site" in panel and "60 units" in panel
        assert "Rimi project site" in panel and "50 units" in panel
        assert "Held across 3 places 118 units" in panel

    def test_it_says_when_the_balances_are_only_this_orders(self, scoped, da, world):
        _receive(da, world)
        body = _visible(_order_page(scoped, world).split('id="where-now"', 1)[1].split("Shipments", 1)[0])
        assert "This is the only order of it" in body

    def test_goods_that_are_used_up_have_no_such_panel(self, scoped, da, world):
        from connect_labs.supply_chain.models import Item

        Item.objects.filter(pk=world["item"]["id"]).update(stock_class="consumable")
        _receive(da, world)
        assert 'id="where-now"' not in _order_page(scoped, world)


class TestTheDistributorsLink:
    def _link(self, da, world):
        issued = op(
            da,
            "update_link_issue",
            data={
                "org_id": world["distributor"],
                "contract_ids": [world["order"]["id"]],
                "supply_point_ids": [world["warehouse"], world["dawaki"], world["rimi"]],
            },
        )
        return UpdateLink.objects.get(pk=issued["id"]), issued

    def _public(self, client, issued):
        token = issued["url"].rstrip("/").rsplit("/", 1)[-1]
        return client.get(reverse("supply_chain:update_link_public", args=[token])).content.decode()

    def test_a_link_over_only_a_donation_does_not_mention_confirming_a_payment(self, client, da, world):
        _, issued = self._link(da, world)
        body = self._public(client, issued)
        assert "confirm a payment" not in body.lower()

    def test_the_order_is_offered_with_its_units_pluralised(self, client, da, world):
        _, issued = self._link(da, world)
        body = self._public(client, issued)
        assert "DON-DISP-1 — Standard dispenser, 120 units" in body
        assert "120 unit<" not in body and "120 unit\n" not in body

    def test_the_order_page_names_whose_link_it_was_without_a_stray_s(self, scoped, client, da, world):
        from connect_labs.supply_chain.models import Contract, SupplyPoint
        from connect_labs.supply_chain.update_links import service

        link, _ = self._link(da, world)
        service.submit(
            link,
            "record_receipt",
            {
                "contract": Contract.objects.get(pk=world["order"]["id"]),
                "supply_point": SupplyPoint.objects.get(pk=world["warehouse"]),
                "received_on": None,
                "quantity_accepted": 118,
                "quantity_rejected": 2,
                "rejection_reason": "Tap housing cracked in transit",
                "unit_basis": "base",
            },
        )
        body = _visible(_order_page(scoped, world))
        assert "update link held by Harmattan Health Supplies" in body
        assert "Supplies's" not in body
