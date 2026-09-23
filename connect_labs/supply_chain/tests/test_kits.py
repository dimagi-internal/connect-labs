"""Kits: one SKU that bundles several products.

THIS REPOSITORY IS PUBLIC. Every supplier, product and figure here is invented.

An ORS/zinc co-pack holds two ORS sachets and ten zinc tablets, and is priced
per co-pack. The item still belongs to one primary commodity and is counted in
its own SKUs; what the component list adds is the three questions a single
commodity cannot answer (field use cases, G1):

  (a) does the zinc inside meet the zinc specification;
  (b) do two suppliers' co-packs hold the same contents, so that their prices
      can be ranked against each other;
  (c) is one of these a whole treatment course.
"""

from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Item
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10611


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def catalogue(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "ors-zinc",
            "name": "ORS/zinc co-pack",
            "category": "oral_rehydration",
            "base_unit": "co_pack",
            "pack_unit": "carton",
            "base_per_pack": 50,
        },
    )
    op(da, "commodity_upsert", data={"slug": "ors", "name": "ORS", "base_unit": "sachet"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "zinc",
            "name": "Zinc 20 mg",
            "base_unit": "tablet",
            "spec_requirements": [{"field": "zinc_mg", "operator": "==", "value": 20, "unit": "mg"}],
        },
    )


def _kit(da, sku, zinc_tablets, zinc_mg=20, **extra):
    return op(
        da,
        "item_upsert",
        data={
            "sku": sku,
            "name": f"Co-pack {sku}",
            "commodity_slug": "ors-zinc",
            "base_unit": "co_pack",
            "pack_unit": "carton",
            "base_per_pack": 50,
            "components": [
                {"commodity_slug": "ors", "quantity": 2, "base_unit": "sachet"},
                {
                    "commodity_slug": "zinc",
                    "quantity": zinc_tablets,
                    "base_unit": "tablet",
                    "spec_attributes": {"zinc_mg": zinc_mg},
                },
            ],
            **extra,
        },
    )


class TestAKitSaysWhatIsInIt:
    def test_the_components_round_trip(self, da, catalogue):
        kit = _kit(da, "EHA-CP-10", 10)
        assert kit["is_kit"] is True
        assert [c["commodity_slug"] for c in kit["components"]] == ["ors", "zinc"]
        assert op(da, "item_get", item_id=kit["id"])["components"][1]["quantity"] == "10"

    def test_a_component_must_name_a_product_in_this_catalogue(self, da, catalogue):
        """A component pointing at nothing could never be checked against a
        specification, and would read as if it had passed."""
        with pytest.raises(ValueError, match="amoxicillin"):
            op(
                da,
                "item_upsert",
                data={
                    "sku": "BAD",
                    "name": "Bad kit",
                    "commodity_slug": "ors-zinc",
                    "components": [{"commodity_slug": "amoxicillin", "quantity": 1, "base_unit": "tablet"}],
                },
            )

    def test_a_component_needs_a_quantity_and_a_unit(self, da, catalogue):
        import jsonschema

        with pytest.raises(jsonschema.ValidationError):
            op(
                da,
                "item_upsert",
                data={"sku": "BAD", "commodity_slug": "ors-zinc", "components": [{"commodity_slug": "ors"}]},
            )

    def test_an_ordinary_item_is_not_a_kit(self, da, catalogue):
        plain = op(da, "item_upsert", data={"sku": "ORS-1", "name": "ORS", "commodity_slug": "ors"})
        assert plain["is_kit"] is False
        assert plain["components"] == []


class TestTheSpecificationCoversEachComponent:
    def test_zinc_inside_a_co_pack_is_checked_against_the_zinc_specification(self, da, catalogue):
        _kit(da, "EHA-CP-10", 10, zinc_mg=10)
        found = [c for c in op(da, "checks_list")["checks"] if c["kind"] == "item_fails_specification"]
        assert len(found) == 1
        failing = [part for part in found[0]["facts"]["components"] if "fail" in part["verdict"]]
        assert [part["commodity_slug"] for part in failing] == ["zinc"]

    def test_a_kit_whose_components_all_pass_is_not_flagged(self, da, catalogue):
        _kit(da, "EHA-CP-10", 10, zinc_mg=20)
        kinds = {c["kind"] for c in op(da, "checks_list")["checks"]}
        assert "item_fails_specification" not in kinds


def _round_with_quotes(da, *kits):
    supplier = op(da, "supplier_create", data={"name": "EHA Clinics"})
    round_ = op(
        da,
        "round_create",
        data={
            "label": "Co-packs",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "ors-zinc", "quantity": "100", "quantity_unit": "carton"}],
        },
    )
    for kit, price in kits:
        op(
            da,
            "quote_record",
            data={
                "round_id": round_["id"],
                "commodity_slug": "ors-zinc",
                "supplier_id": supplier["id"],
                "item_id": kit["id"],
                "as_quoted_amount": price,
                "as_quoted_unit": "per_pack",
                "quantity_basis": "100",
                "quantity_basis_unit": "carton",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
    return op(da, "round_compare", round_id=round_["id"], commodity_slug="ors-zinc")


class TestKitsAreComparedOnlyAgainstTheSameContents:
    def test_two_kits_with_the_same_contents_are_ranked_together(self, da, catalogue):
        first = _kit(da, "A", 10)
        second = _kit(da, "B", 10)
        comparison = _round_with_quotes(da, (first, "40.00"), (second, "38.00"))
        assert comparison["comparable_count"] == 2

    def test_kits_with_different_contents_are_not_ranked_against_each_other(self, da, catalogue):
        """Ten zinc tablets against twelve is not the same product at a
        different price, and ranking them would say it was."""
        ten = _kit(da, "A", 10)
        twelve = _kit(da, "B", 12)
        comparison = _round_with_quotes(da, (ten, "40.00"), (twelve, "38.00"))

        assert comparison["comparable_count"] == 0
        for row in comparison["blocked"]:
            cell = row["figures"]["landed_total_for_round_quantity"]
            assert any("kit composition differs" in reason for reason in cell["unconfirmed"])
            question = next(q for q in row["questions"] if q["key"] == "kit_composition")
            # Deciding which contents to buy is ours, not the supplier's.
            assert question["audience"] == "internal"
            assert "zinc" in question["question"]

    def test_the_rows_carry_their_composition_so_a_screen_can_set_them_side_by_side(self, da, catalogue):
        ten = _kit(da, "A", 10)
        comparison = _round_with_quotes(da, (ten, "40.00"))
        row = comparison["comparable"][0]
        assert row["composition"] == [
            {"commodity_slug": "ors", "quantity": "2", "base_unit": "sachet"},
            {"commodity_slug": "zinc", "quantity": "10", "base_unit": "tablet"},
        ]


class TestAKitCanBeOneCourse:
    def test_a_packet_that_is_one_course_costs_per_course_with_no_ration_table(self, da, catalogue):
        """The co-pack commodity has no course definition. A packet the
        manufacturer made AS the course needs none."""
        kit = _kit(da, "A", 10, one_course_is="base_unit")
        comparison = _round_with_quotes(da, (kit, "40.00"))
        figures = comparison["comparable"][0]["figures"]
        # 40.00 per carton of 50 co-packs, one co-pack per course.
        assert Decimal(figures["usd_per_course"]["amount"]) == Decimal("0.8")

    def test_without_it_the_course_figure_stays_unconfirmed(self, da, catalogue):
        kit = _kit(da, "A", 10)
        comparison = _round_with_quotes(da, (kit, "40.00"))
        assert "unconfirmed" in comparison["comparable"][0]["figures"]["usd_per_course"]


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="kit", password="x", email="kit@dimagi.com")
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


def _item_post(**overrides):
    payload = {
        "sku": "EHA-CP-10",
        "name": "EHA co-pack",
        "manufacturer": "",
        "status": "active",
        "base_unit": "co_pack",
        "pack_unit": "carton",
        "base_per_pack": "50",
        "pack_per_case": "",
        "base_unit_grams": "",
        "shelf_life_months": "",
        "gtin_base": "",
        "gtin_pack": "",
        "gtin_case": "",
        "gpc_brick": "",
        "one_course_is": "base_unit",
        "components-TOTAL_FORMS": "2",
        "components-INITIAL_FORMS": "0",
        "components-MIN_NUM_FORMS": "0",
        "components-MAX_NUM_FORMS": "1000",
        "components-0-commodity_slug": "ors",
        "components-0-quantity": "2",
        "components-0-base_unit": "sachet",
        "components-1-commodity_slug": "zinc",
        "components-1-quantity": "10",
        "components-1-base_unit": "tablet",
    }
    payload.update(overrides)
    return payload


class TestTheKitScreens:
    def test_the_item_form_records_a_kit(self, scoped, da, catalogue):
        from connect_labs.supply_chain.models import Commodity

        copack = Commodity.objects.get(scope_key=f"prog:{PROGRAM}", slug="ors-zinc")
        response = scoped.post(reverse("supply_chain:item_create"), _item_post(commodity=copack.pk))
        assert response.status_code == 302, response.content.decode()[:2000]

        item = Item.objects.get(sku="EHA-CP-10")
        assert [c["commodity_slug"] for c in item.components] == ["ors", "zinc"]
        assert item.one_course_is == "base_unit"

    def test_the_edit_form_opens_on_the_kit_as_it_stands(self, scoped, da, catalogue):
        kit = _kit(da, "EHA-CP-10", 10)
        body = scoped.get(reverse("supply_chain:item_edit", args=[kit["id"]])).content.decode()
        assert 'name="components-TOTAL_FORMS" value="2"' in body
        assert 'value="10"' in body

    def test_the_item_page_shows_what_is_inside(self, scoped, da, catalogue):
        kit = _kit(da, "EHA-CP-10", 10, zinc_mg=10, one_course_is="base_unit")
        body = scoped.get(reverse("supply_chain:item_detail", args=[kit["id"]])).content.decode()
        assert "What is inside" in body
        assert "Zinc 20 mg" in body
        assert "10 tablet" in body
        # The component's own verdict, not only the kit's.
        assert "fail" in body
        assert "One co_pack is one full course" in body or "one full course" in body


def _twin_zinc_kit(da):
    """A kit holding two zinc formulations, one to spec and one not."""
    return op(
        da,
        "item_upsert",
        data={
            "sku": "TWIN-ZN",
            "name": "Twin zinc co-pack",
            "commodity_slug": "ors-zinc",
            "base_unit": "co_pack",
            "components": [
                {"commodity_slug": "zinc", "quantity": 5, "base_unit": "tablet", "spec_attributes": {"zinc_mg": 20}},
                {"commodity_slug": "zinc", "quantity": 5, "base_unit": "tablet", "spec_attributes": {"zinc_mg": 10}},
            ],
        },
    )


class TestTwoComponentsOfOneProduct:
    def test_each_row_on_the_item_page_carries_its_own_verdict(self, scoped, da, catalogue):
        kit = _twin_zinc_kit(da)
        response = scoped.get(reverse("supply_chain:item_detail", args=[kit["id"]]))
        verdicts = [row["verdict"] for row in response.context["item"]["component_rows"]]
        assert verdicts == ["Meets all 1", "1 of 1 fail"]

    def test_editing_keeps_each_components_own_specification(self, scoped, da, catalogue):
        from connect_labs.supply_chain.models import Commodity

        kit = _twin_zinc_kit(da)
        copack = Commodity.objects.get(scope_key=f"prog:{PROGRAM}", slug="ors-zinc")
        body = scoped.get(reverse("supply_chain:item_edit", args=[kit["id"]])).content.decode()
        assert 'name="components-1-source_index"' in body
        response = scoped.post(
            reverse("supply_chain:item_edit", args=[kit["id"]]),
            _item_post(
                sku="TWIN-ZN",
                commodity=copack.pk,
                **{
                    "components-0-commodity_slug": "zinc",
                    "components-0-quantity": "5",
                    "components-0-base_unit": "tablet",
                    "components-0-source_index": "0",
                    "components-1-quantity": "6",
                    "components-1-source_index": "1",
                },
            ),
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        item = Item.objects.get(pk=kit["id"])
        assert [c.get("spec_attributes") for c in item.components] == [{"zinc_mg": 20}, {"zinc_mg": 10}]

    def test_a_zero_quantity_is_refused_on_the_field(self, scoped, da, catalogue):
        from connect_labs.supply_chain.models import Commodity

        copack = Commodity.objects.get(scope_key=f"prog:{PROGRAM}", slug="ors-zinc")
        response = scoped.post(
            reverse("supply_chain:item_create"), _item_post(commodity=copack.pk, **{"components-1-quantity": "0"})
        )
        assert response.status_code == 200
        assert response.context["components"].forms[1].errors["quantity"]
        assert not Item.objects.filter(sku="EHA-CP-10").exists()
