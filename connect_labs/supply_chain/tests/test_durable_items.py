"""Durable equipment moves through the ledger but is never consumed, so it is not forecast.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on field_gaps_world.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import field_gaps_world
from connect_labs.supply_chain.tests.field_gaps_world import TODAY, op

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = field_gaps_world.da
world = field_gaps_world.world
dispensers = field_gaps_world.dispensers
scoped = field_gaps_world.scoped


class TestDurableEquipmentIsNotForecast:
    def test_an_item_is_consumable_unless_it_says_otherwise(self, da, world):
        op(da, "commodity_upsert", data={"slug": "muac", "name": "MUAC tape"})
        assert op(da, "item_upsert", data={"sku": "M", "commodity_slug": "muac"})["stock_class"] == "consumable"

    def test_it_still_has_a_balance(self, da, world, dispensers):
        item, site = dispensers
        on_hand = op(da, "stock_on_hand", supply_point_id=site["id"], item_id=item["id"])
        assert Decimal(on_hand["ledger"]["amount"]) == Decimal("4")

    def test_it_has_no_consumption_rate_months_of_stock_or_resupply(self, da, world, dispensers):
        item, site = dispensers
        plan = op(da, "resupply_plan", supply_point_id=site["id"], item_id=item["id"])
        assert plan["status"] == "durable"
        for key in ("amc", "months_of_stock", "days_to_stockout", "resupply_quantity"):
            assert "not_forecast" in plan[key], key
            assert "durable" in plan[key]["not_forecast"]
        assert Decimal(plan["on_hand"]["amount"]) == Decimal("4")

    def test_the_network_view_says_how_many_each_site_holds(self, da, world, dispensers):
        # "Which site has which dispenser" is the whole reason a durable item
        # stays in the ledger. The network view picked its display unit from
        # the ITEM's own pack unit, which is blank when the item inherits its
        # units from the product -- and then refused to convert the balance
        # into a blank unit, so every site read Unconfirmed.
        item, site = dispensers
        row = next(r for r in op(da, "network_stock")["points"] if r["supply_point_id"] == site["id"])
        assert "unconfirmed" not in row["on_hand"], row["on_hand"]
        assert Decimal(row["on_hand"]["amount"]) == Decimal("4")

    def test_a_pack_unit_inherited_from_the_product_is_the_display_unit(self, da, world):
        op(
            da,
            "commodity_upsert",
            data={"slug": "tap", "name": "Tap", "category": "equipment", "base_unit": "unit", "pack_unit": "unit"},
        )
        item = op(da, "item_upsert", data={"sku": "TAP-1", "commodity_slug": "tap", "stock_class": "durable"})
        op(
            da,
            "movement_record",
            data={
                "kind": "receipt",
                "occurred_on": TODAY.isoformat(),
                "to_supply_point_id": world["store"]["id"],
                "item_id": item["id"],
                "commodity_slug": "tap",
                "quantity": "7",
                "quantity_unit": "unit",
                "source": "we_recorded",
            },
        )
        row = next(r for r in op(da, "network_stock")["points"] if r["supply_point_id"] == world["store"]["id"])
        assert Decimal(row["on_hand"]["amount"]) == Decimal("7")
        assert row["on_hand"]["unit"] == "unit"

    def test_it_raises_no_stockout_or_below_minimum(self, da, world, dispensers):
        kinds = {c["kind"] for c in op(da, "checks_list")["checks"]}
        assert not {"stock_stockout", "stock_below_minimum"} & kinds


def test_the_item_page_and_form_carry_the_stock_class(scoped, da, world, dispensers):
    item, _site = dispensers
    page = scoped.get(reverse("supply_chain:item_detail", args=[item["id"]])).content.decode()
    assert "Durable" in page
    form = scoped.get(reverse("supply_chain:item_edit", args=[item["id"]])).content.decode()
    assert 'name="stock_class"' in form


def test_the_stock_page_says_durable_not_forecast(scoped, da, world, dispensers):
    body = scoped.get(reverse("supply_chain:stock")).content.decode()
    assert "durable — not forecast" in body
