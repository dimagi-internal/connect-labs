"""Which rung of the unit ladder a kit's components describe.

THIS REPOSITORY IS PUBLIC. Every product and figure here is invented.

A co-pack's components describe ONE co-pack -- its base unit: two ORS sachets
and ten zinc tablets. A chlorine test kit's describe ONE kit -- its pack: fifty
reagent tablets and a comparator, for fifty tests. Nothing said which, so the
test kit's item page read "one test holds 50 tablets". `Item.components_per`
says it, and the item page, the kit comparison and the spec check read it.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Item
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10616


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def catalogue(da):
    op(da, "commodity_upsert", data={"slug": "dpd1", "name": "DPD No. 1 tablet", "base_unit": "tablet"})
    op(da, "commodity_upsert", data={"slug": "comparator", "name": "Colour comparator", "base_unit": "unit"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "test-kit",
            "name": "Free chlorine test kit",
            "category": "diagnostic",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
        },
    )


def _kit(da, sku, *, per=None, tablets="50", comparators="1"):
    data = {
        "sku": sku,
        "name": f"Kit {sku}",
        "commodity_slug": "test-kit",
        "base_unit": "test",
        "pack_unit": "kit",
        "base_per_pack": 50,
        "components": [
            {"commodity_slug": "dpd1", "quantity": tablets, "base_unit": "tablet"},
            {"commodity_slug": "comparator", "quantity": comparators, "base_unit": "unit"},
        ],
    }
    if per is not None:
        data["components_per"] = per
    return op(da, "item_upsert", data=data)


class TestTheItemSaysWhichUnitItsComponentsDescribe:
    def test_it_defaults_to_the_base_unit(self, da, catalogue):
        assert _kit(da, "K1")["components_per"] == "base"

    def test_it_can_say_the_pack(self, da, catalogue):
        kit = _kit(da, "K1", per="pack")
        assert kit["components_per"] == "pack"
        assert Item.objects.get(pk=kit["id"]).components_unit == "kit"

    def test_a_base_level_kit_names_its_base_unit(self, da, catalogue):
        kit = _kit(da, "K1", per="base")
        assert Item.objects.get(pk=kit["id"]).components_unit == "test"

    def test_an_unknown_level_is_refused(self, da, catalogue):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            _kit(da, "K1", per="case")


class TestTheMigrationDefault:
    """The rule the data migration applies to rows that pre-date the field."""

    def test_components_that_are_a_multiple_of_the_pack_describe_the_pack(self):
        from connect_labs.supply_chain.records import infer_components_per

        components = [{"quantity": "50"}, {"quantity": "1"}]
        assert infer_components_per(components, pack_unit="kit", base_per_pack=50) == "pack"

    def test_a_co_pack_describes_its_base_unit(self):
        from connect_labs.supply_chain.records import infer_components_per

        components = [{"quantity": "2"}, {"quantity": "10"}]
        assert infer_components_per(components, pack_unit="carton", base_per_pack=50) == "base"

    def test_without_a_pack_it_is_the_base_unit(self):
        from connect_labs.supply_chain.records import infer_components_per

        assert infer_components_per([{"quantity": "50"}], pack_unit="", base_per_pack=None) == "base"
        assert infer_components_per([{"quantity": "50"}], pack_unit="kit", base_per_pack=1) == "base"


class TestKitsAreComparedAtOneLevel:
    def _compare(self, da, *kits):
        supplier = op(da, "supplier_create", data={"name": "Harmattan Health Supplies"})
        tender = op(
            da,
            "tender_create",
            data={
                "label": "Kits",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "test-kit", "quantity": "20", "quantity_unit": "kit"}],
            },
        )
        for kit in kits:
            op(
                da,
                "quote_record",
                data={
                    "tender_id": tender["id"],
                    "commodity_slug": "test-kit",
                    "supplier_id": supplier["id"],
                    "item_id": kit["id"],
                    "as_quoted_amount": "38.00",
                    "as_quoted_unit": "per_pack",
                    "quantity_basis": "20",
                    "quantity_basis_unit": "kit",
                    "pack_spec_source": "trade_item_confirmed",
                    "freight_basis": "included",
                    "duties_basis": "included",
                },
            )
        return op(da, "tender_compare", tender_id=tender["id"], commodity_slug="test-kit")

    def test_the_same_contents_stated_at_different_levels_are_the_same_kit(self, da, catalogue):
        """50 tablets per kit of 50 tests is one tablet per test: the same kit."""
        per_kit = _kit(da, "PER-KIT", per="pack", tablets="50", comparators="1")
        per_test = _kit(da, "PER-TEST", per="base", tablets="1", comparators="0.02")
        comparison = self._compare(da, per_kit, per_test)
        assert comparison["comparable_count"] == 2

    def test_the_same_numbers_at_different_levels_are_different_kits(self, da, catalogue):
        """50 tablets per kit is not 50 tablets per test."""
        per_kit = _kit(da, "PER-KIT", per="pack")
        per_test = _kit(da, "PER-TEST", per="base")
        comparison = self._compare(da, per_kit, per_test)
        assert comparison["comparable_count"] == 0
        reasons = " ".join(
            reason
            for row in comparison["blocked"]
            for reason in row["figures"]["landed_total_for_tender_quantity"].get("unconfirmed", [])
        )
        assert "per kit" in reasons and "per test" in reasons


class TestTheSpecCheckSaysTheLevel:
    def test_a_failing_kit_check_says_which_unit_its_parts_fill(self, da, catalogue):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": "dpd1",
                "spec_requirements": [{"field": "shelf_life_months", "operator": ">=", "value": 18}],
            },
        )
        kit = op(
            da,
            "item_upsert",
            data={
                "sku": "K1",
                "name": "Kit K1",
                "commodity_slug": "test-kit",
                "base_per_pack": 50,
                "pack_unit": "kit",
                "base_unit": "test",
                "components_per": "pack",
                "components": [
                    {
                        "commodity_slug": "dpd1",
                        "quantity": "50",
                        "base_unit": "tablet",
                        "spec_attributes": {"shelf_life_months": 12},
                    }
                ],
            },
        )
        assert op(da, "item_get", item_id=kit["id"])["components_unit"] == "kit"
        facts = next(
            c["facts"]
            for c in op(da, "checks_list")["checks"]
            if c["kind"] == "item_fails_specification" and c["subject"]["id"] == kit["id"]
        )
        assert facts["components_in_each"] == "kit"


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="per", password="x", email="per@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "reference_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheScreens:
    def test_the_item_page_says_one_kit_holds(self, scoped, da, catalogue):
        kit = _kit(da, "K1", per="pack")
        body = scoped.get(reverse("supply_chain:item_detail", args=[kit["id"]])).content.decode()
        assert "one kit holds" in body
        assert "one test holds" not in body

    def test_a_co_pack_says_one_co_pack_holds(self, scoped, da, catalogue):
        op(
            da,
            "commodity_upsert",
            data={"slug": "ors-zinc", "name": "ORS/zinc co-pack", "base_unit": "co-pack", "pack_unit": "carton"},
        )
        op(da, "commodity_upsert", data={"slug": "ors", "name": "ORS", "base_unit": "sachet"})
        copack = op(
            da,
            "item_upsert",
            data={
                "sku": "CP",
                "name": "Co-pack",
                "commodity_slug": "ors-zinc",
                "base_unit": "co-pack",
                "pack_unit": "carton",
                "base_per_pack": 50,
                "components": [{"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"}],
            },
        )
        body = scoped.get(reverse("supply_chain:item_detail", args=[copack["id"]])).content.decode()
        assert "one co-pack holds" in body

    def test_the_item_form_asks_and_saves_it(self, scoped, da, catalogue):
        from connect_labs.supply_chain.models import Commodity

        kit = _kit(da, "K1")
        commodity = Commodity.objects.get(scope_key=f"prog:{PROGRAM}", slug="test-kit")
        body = scoped.get(reverse("supply_chain:item_edit", args=[kit["id"]])).content.decode()
        assert 'name="components_per"' in body
        response = scoped.post(
            reverse("supply_chain:item_edit", args=[kit["id"]]),
            {
                "sku": "K1",
                "name": "Kit K1",
                "commodity": commodity.pk,
                "status": "active",
                "base_unit": "test",
                "pack_unit": "kit",
                "base_per_pack": "50",
                "components_per": "pack",
            },
        )
        assert response.status_code == 302, response.content.decode()[:1500]
        assert Item.objects.get(pk=kit["id"]).components_per == "pack"
        assert Item.objects.get(pk=kit["id"]).base_per_pack == 50
        assert Decimal(Item.objects.get(pk=kit["id"]).components[0]["quantity"]) == Decimal("50")
