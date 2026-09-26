"""Specifications edited in a browser, not only through the API.

THIS REPOSITORY IS PUBLIC. Every product and figure here is invented.

A technical partner's requirements (50 tests to a kit, a range that reads the
2 mg/L dose) go on the PRODUCT; what a manufacturer states for its kit, and for
the reagent inside it, goes on the TRADE ITEM. Both used to be API-only: the
product form said so in a footnote and the item form kept whatever was there.
Both go through the same upsert operations as everything else.
"""

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10617
SCOPE = f"prog:{PROGRAM}"


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access

    account = django_user_model.objects.create_user(username="spec", password="x", email="spec@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "reference.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


@pytest.fixture
def catalogue(da):
    op(da, "commodity_upsert", data={"slug": "dpd1", "name": "DPD No. 1 tablet", "base_unit": "tablet"})
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


def _formset(prefix, rows, initial=0):
    data = {
        f"{prefix}-TOTAL_FORMS": str(len(rows)),
        f"{prefix}-INITIAL_FORMS": str(initial),
        f"{prefix}-MIN_NUM_FORMS": "0",
        f"{prefix}-MAX_NUM_FORMS": "1000",
    }
    for index, row in enumerate(rows):
        for key, value in row.items():
            data[f"{prefix}-{index}-{key}"] = value
    return data


def _product_post(**extra):
    return {
        "slug": "test-kit",
        "name": "Free chlorine test kit",
        "category": "diagnostic",
        "base_unit": "test",
        "pack_unit": "kit",
        "base_per_pack": "50",
        "spec_reference": "AQ-TK-1",
        **extra,
    }


REQUIREMENTS = [
    {"field": "tests_per_kit", "operator": ">=", "value": "50", "unit": "tests", "rationale": "fifty tests a kit"},
    {
        "field": "range_max_mg_per_l",
        "operator": ">=",
        "value": "2.0",
        "unit": "mg/L",
        "rationale": "must read the 2 mg/L dose",
    },
]


class TestTheProductForm:
    def test_it_shows_the_requirements_already_on_the_product(self, scoped, da, catalogue):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": "test-kit",
                "spec_requirements": [
                    {"field": "tests_per_kit", "operator": ">=", "value": 50, "unit": "tests"},
                ],
            },
        )
        body = scoped.get(reverse("supply_chain:product_edit", args=["test-kit"])).content.decode()
        assert 'name="requirements-TOTAL_FORMS" value="1"' in body
        assert 'value="tests_per_kit"' in body

    def test_it_saves_the_requirements_through_the_upsert(self, scoped, da, catalogue):
        response = scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            {**_product_post(), **_formset("requirements", REQUIREMENTS)},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        saved = Commodity.objects.get(scope_key=SCOPE, slug="test-kit").spec_requirements
        assert [r["field"] for r in saved] == ["tests_per_kit", "range_max_mg_per_l"]
        assert saved[1] == {
            "field": "range_max_mg_per_l",
            "operator": ">=",
            "value": 2.0,
            "unit": "mg/L",
            "rationale": "must read the 2 mg/L dose",
        }
        assert saved[0]["value"] == 50

    def test_removing_the_last_requirement_clears_them(self, scoped, da, catalogue):
        scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            {**_product_post(), **_formset("requirements", REQUIREMENTS)},
        )
        rows = [{**REQUIREMENTS[0], "DELETE": "on"}, {**REQUIREMENTS[1], "DELETE": "on"}]
        scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            {**_product_post(), **_formset("requirements", rows, initial=2)},
        )
        assert Commodity.objects.get(scope_key=SCOPE, slug="test-kit").spec_requirements == []

    def test_a_post_without_the_editor_leaves_the_requirements_alone(self, scoped, da, catalogue):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": "test-kit",
                "spec_requirements": [
                    {"field": "tests_per_kit", "operator": ">=", "value": 50},
                ],
            },
        )
        scoped.post(reverse("supply_chain:product_edit", args=["test-kit"]), _product_post())
        assert len(Commodity.objects.get(scope_key=SCOPE, slug="test-kit").spec_requirements) == 1

    def test_an_unknown_operator_is_refused_on_the_row(self, scoped, da, catalogue):
        rows = [{**REQUIREMENTS[0], "operator": "~="}]
        response = scoped.post(
            reverse("supply_chain:product_edit", args=["test-kit"]),
            {**_product_post(), **_formset("requirements", rows)},
        )
        assert response.status_code == 200
        assert Commodity.objects.get(scope_key=SCOPE, slug="test-kit").spec_requirements == []


def _kit(da):
    return op(
        da,
        "item_upsert",
        data={
            "sku": "lumen-fc50",
            "name": "Lumen FC-50",
            "commodity_slug": "test-kit",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "components_per": "pack",
            "spec_attributes": {"tests_per_kit": 50},
            "components": [
                {
                    "commodity_slug": "dpd1",
                    "quantity": "50",
                    "base_unit": "tablet",
                    "spec_attributes": {"shelf_life_months": 24},
                }
            ],
        },
    )


def _item_post(**extra):
    commodity = Commodity.objects.get(scope_key=SCOPE, slug="test-kit")
    return {
        "sku": "lumen-fc50",
        "name": "Lumen FC-50",
        "commodity": commodity.pk,
        "status": "active",
        "base_unit": "test",
        "pack_unit": "kit",
        "base_per_pack": "50",
        "components_per": "pack",
        **_formset(
            "components",
            [{"commodity_slug": "dpd1", "quantity": "50", "base_unit": "tablet", "source_index": "0"}],
            initial=1,
        ),
        **extra,
    }


class TestTheItemForm:
    def test_it_shows_what_the_item_and_each_component_state(self, scoped, da, catalogue):
        kit = _kit(da)
        body = scoped.get(reverse("supply_chain:item_edit", args=[kit["id"]])).content.decode()
        assert 'name="stated-TOTAL_FORMS" value="2"' in body
        assert 'value="tests_per_kit"' in body
        assert 'value="shelf_life_months"' in body

    def test_it_saves_the_items_own_figures_and_each_components(self, scoped, da, catalogue):
        kit = _kit(da)
        rows = [
            {"applies_to": "item", "field": "tests_per_kit", "value": "50"},
            {"applies_to": "item", "field": "range_max_mg_per_l", "value": "3.5"},
            {"applies_to": "dpd1", "field": "shelf_life_months", "value": "18"},
        ]
        response = scoped.post(
            reverse("supply_chain:item_edit", args=[kit["id"]]),
            {**_item_post(), **_formset("stated", rows, initial=2)},
        )
        assert response.status_code == 302, response.content.decode()[:2000]
        item = Item.objects.get(pk=kit["id"])
        assert item.spec_attributes == {"tests_per_kit": 50, "range_max_mg_per_l": 3.5}
        assert item.components[0]["spec_attributes"] == {"shelf_life_months": 18}

    def test_a_figure_for_a_product_not_inside_the_kit_is_refused(self, scoped, da, catalogue):
        kit = _kit(da)
        rows = [{"applies_to": "comparator", "field": "graduations", "value": "10"}]
        response = scoped.post(
            reverse("supply_chain:item_edit", args=[kit["id"]]),
            {**_item_post(), **_formset("stated", rows)},
        )
        assert response.status_code == 200
        assert Item.objects.get(pk=kit["id"]).spec_attributes == {"tests_per_kit": 50}

    def test_the_spec_check_reads_what_the_form_saved(self, scoped, da, catalogue):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": "test-kit",
                "spec_requirements": [
                    {"field": "range_max_mg_per_l", "operator": ">=", "value": 2.0, "unit": "mg/L"},
                ],
            },
        )
        kit = _kit(da)
        rows = [{"applies_to": "item", "field": "range_max_mg_per_l", "value": "1.5"}]
        scoped.post(
            reverse("supply_chain:item_edit", args=[kit["id"]]),
            {**_item_post(), **_formset("stated", rows)},
        )
        failing = [
            c
            for c in op(da, "checks_list")["checks"]
            if c["kind"] == "item_fails_specification" and c["subject"]["id"] == kit["id"]
        ]
        assert failing and failing[0]["facts"]["verdict"] == "1 of 1 fail"
