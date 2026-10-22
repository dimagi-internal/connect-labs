"""The record forms: donor suppliers, pickers with this programme's own first, durable items.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on chlorine_world.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import (
    op,
)

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


class TestDonorSuppliers:
    def test_a_supplier_can_be_a_donor_and_the_pages_say_so(self, client_in_programme, da):
        donor = op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
        assert donor["type"] == "donor"
        body = client_in_programme.get(reverse("supply_chain:supplier_detail", args=[donor["id"]])).content.decode()
        assert "Donor" in body and "supplies in kind" in body
        listing = client_in_programme.get(reverse("supply_chain:suppliers")).content.decode()
        assert "Donor — supplies in kind" in listing

    def test_the_supplier_form_offers_donor(self, client_in_programme):
        body = client_in_programme.get(reverse("supply_chain:supplier_create")).content.decode()
        assert 'value="donor"' in body

    def test_a_picker_names_a_donor_as_one(self, client_in_programme, da, world):
        op(da, "supplier_create", data={"name": "Water For All", "type": "donor"})
        body = client_in_programme.get(reverse("supply_chain:contract_create")).content.decode()
        assert "Water For All (donor)" in body


class TestPickers:
    def test_currency_is_a_select_with_the_programmes_own_first(self, client_in_programme, da, world):
        op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "NG-1",
                "quantity": "1",
                "quantity_unit": "jerry_can",
                "unit_price": "6000",
                "unit_price_unit": "per_pack",
                "currency": "NGN",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:contract_create")).content.decode()
        select = re.search(r'<select[^>]*name="currency".*?</select>', body, re.S).group(0)
        assert '<optgroup label="Used in this programme">' in select
        used = select.split('<optgroup label="Used in this programme">', 1)[1].split("</optgroup>", 1)[0]
        assert 'value="NGN"' in used
        assert 'value="EUR"' in select

    def test_the_paid_to_picker_puts_this_programmes_payees_first(self, client_in_programme, da, world):
        contract = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-3",
                "quantity": "1",
                "quantity_unit": "jerry_can",
                "consideration": "in_kind",
                "source": "we_recorded",
            },
        )
        shipment = op(
            da,
            "shipment_record",
            data={"contract_id": contract["id"], "status": "at_customs", "source": "we_recorded"},
        )
        agent = op(da, "org_upsert", data={"slug": "agent", "name": "Zenith Clearing"})
        op(
            da,
            "charge_record",
            data={
                "shipment_id": shipment["id"],
                "kind": "clearing",
                "payee_org_id": agent["id"],
                "amount": "100",
                "currency": "USD",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:charge_record", args=[shipment["id"]])).content.decode()
        select = re.search(r'<select[^>]*name="payee_org".*?</select>', body, re.S).group(0)
        first = select.split('<optgroup label="Paid before in this programme">', 1)[1].split("</optgroup>", 1)[0]
        assert "Zenith Clearing" in first
        assert "The regulator" in select.split('<optgroup label="Every organisation">', 1)[1]
        assert "data-tomselect" in select


def _durable(da):
    op(da, "commodity_upsert", data={"slug": "dispenser", "name": "Chlorine dispenser", "category": "equipment"})
    return op(
        da,
        "item_upsert",
        data={"sku": "disp", "name": "Wall dispenser", "commodity_slug": "dispenser", "stock_class": "durable"},
    )


def _commodity_pk():
    from connect_labs.supply_chain.models import Commodity

    return Commodity.objects.get(slug="dispenser").pk


class TestDurableMovementsAskForNoBatch:
    def test_the_item_picker_marks_durable_items_and_the_form_hides_batch_and_expiry(self, client_in_programme, da):
        dispenser = _durable(da)
        body = client_in_programme.get(reverse("supply_chain:movement_record")).content.decode()
        assert 'data-durable-hides="batch expiry"' in body
        # Inside the item picker: another select (commodity, supply point) can
        # hold an option with the same pk, and it comes first on the page.
        picker = re.search(r'<select name="item".*?</select>', body, re.S).group(0)
        option = re.search(rf'<option value="{dispenser["id"]}"[^>]*>', picker).group(0)
        assert 'data-durable="1"' in option
        assert 'id="div_id_batch"' in body and 'id="div_id_expiry"' in body

    def test_a_batch_typed_for_a_durable_item_is_not_recorded(self, client_in_programme, da):
        from connect_labs.supply_chain.models import Movement

        dispenser = _durable(da)
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "wh", "name": "Warehouse", "kind": "central_store", "source": "we_recorded"},
        )
        response = client_in_programme.post(
            reverse("supply_chain:movement_record"),
            {
                "kind": "receipt",
                "occurred_on": "2026-09-01",
                "to_supply_point": store["id"],
                "commodity": _commodity_pk(),
                "item": dispenser["id"],
                "batch": "B-1",
                "expiry": "2027-01-01",
                "quantity": "4",
                "quantity_unit": "dispenser",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 302, response.content.decode()[-2000:]
        movement = Movement.objects.get(item_id=dispenser["id"])
        assert movement.batch == "" and movement.expiry is None
