"""The catalogue and the supplier directory, driven through the browser.

THIS REPOSITORY IS PUBLIC. Every supplier and figure here is invented.

Like `test_write_screens.py`, these go through the real operations against a
real database rather than patching `call_operation`. The join is the thing most
likely to be wrong: a ModelForm that validates happily and then hands
`item_upsert` a `commodity_id` where its schema wants a `commodity_slug`.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Commodity, Item, Supplier

pytestmark = pytest.mark.django_db

PROGRAM = 10502
SCOPE = f"prog:{PROGRAM}"


@pytest.fixture
def user(client, django_user_model):
    account = django_user_model.objects.create_user(username="ada", password="x")
    client.force_login(account)
    return account


@pytest.fixture
def scoped(client, user, monkeypatch):
    """A signed-in caller with a programme selected.

    Patches the call sites, never the module the names are defined in, and
    imports every one of them first — see `test_write_screens.scoped` for the
    leak that happens when those two rules are not both followed.
    """
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.reference import views as reference_views  # noqa: F401

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    # Each module gets only the names it actually imported. `reference_views`
    # never imports `has_program_context` -- the guard lives in its base class
    # in `form_views` -- and monkeypatch rightly refuses to invent an
    # attribute, which is the check that caught this being over-patched.
    for module in ("form_views", "views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    for module in ("form_views", "views", "reference.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    return client


@pytest.fixture
def rutf():
    return Commodity.objects.create(
        scope_key=SCOPE, slug="rutf", name="Ready-to-use therapeutic food", base_unit="sachet", pack_unit="carton"
    )


PRODUCT = {
    "slug": "resomal",
    "name": "ReSoMal rehydration solution",
    "category": "oral_rehydration",
    "base_unit": "sachet",
    "pack_unit": "carton",
    "base_per_pack": "100",
    "base_unit_grams": "42",
    "shelf_life_months_minimum": "24",
    "spec_reference": "",
    "unicef_material_number": "",
    "base_units_per_day": "",
    "days_per_course": "",
    "course_source": "",
}


def item_post(**overrides):
    payload = {
        "sku": "NUT-RUTF-92",
        "name": "Plumpy'Nut 92 g",
        "manufacturer": "Nutriset",
        "status": "active",
        "base_unit": "sachet",
        "pack_unit": "carton",
        "base_per_pack": "150",
        "pack_per_case": "",
        "base_unit_grams": "92",
        "shelf_life_months": "24",
        "gtin_base": "",
        "gtin_pack": "",
        "gtin_case": "",
        "gpc_brick": "",
    }
    payload.update(overrides)
    return payload


class TestAddingAProduct:
    def test_the_form_renders(self, scoped):
        response = scoped.get(reverse("supply_chain:product_create"))
        assert response.status_code == 200
        assert "What it must be" not in response.content.decode(), "that heading belongs to the read page"
        assert "Short key" in response.content.decode()

    def test_a_product_is_created_in_this_programmes_catalogue(self, scoped):
        response = scoped.post(reverse("supply_chain:product_create"), PRODUCT)
        assert response.status_code == 302

        made = Commodity.objects.get(slug="resomal")
        assert made.scope_key == SCOPE, "a product belongs to the caller's programme, not to everyone"
        assert made.name == "ReSoMal rehydration solution"
        assert made.base_per_pack == 100

    def test_a_course_definition_is_assembled_and_multiplied_out(self, scoped):
        """Two numbers in, three out — the third is what every per-child figure uses."""
        scoped.post(
            reverse("supply_chain:product_create"),
            {**PRODUCT, "base_units_per_day": "2", "days_per_course": "56", "course_source": "WHO 2013"},
        )
        course = Commodity.objects.get(slug="resomal").course_definition
        assert course["base_units_per_day"] == "2"
        assert course["days_per_course"] == 56
        # An integer, not a string: `base_units_per_course` is a count of
        # sachets, and the schema types it as one. Money is the string case.
        assert course["base_units_per_course"] == 112
        assert course["source"] == "WHO 2013"

    def test_a_course_that_does_not_divide_into_whole_units_is_refused(self, scoped):
        """Refused, not rounded.

        `base_units_per_course` is a count of sachets and the schema types it
        as an integer. Rounding here would be the one thing this domain
        refuses: an invisible assumption inside a figure every per-child cost
        is then computed from.
        """
        response = scoped.post(
            reverse("supply_chain:product_create"),
            {**PRODUCT, "base_units_per_day": "1.5", "days_per_course": "55"},
        )
        assert response.status_code == 200
        assert "82.5" in response.content.decode(), "the message must show the number that did not divide"
        assert not Commodity.objects.filter(slug="resomal").exists()

    def test_a_course_of_nothing_a_day_is_refused_on_the_field(self, scoped):
        response = scoped.post(
            reverse("supply_chain:product_create"),
            {**PRODUCT, "base_units_per_day": "0", "days_per_course": "56"},
        )
        assert response.status_code == 200
        assert not Commodity.objects.filter(slug="resomal").exists()

    def test_a_course_is_left_absent_rather_than_written_empty(self, scoped):
        """An empty course definition is the honest starting state.

        It is why per-course cost reads "Unconfirmed" instead of a guess, so
        saving a product without one must not write `{}` over anything.
        """
        scoped.post(reverse("supply_chain:product_create"), PRODUCT)
        assert Commodity.objects.get(slug="resomal").course_definition == {}

    def test_a_slug_already_in_use_is_refused_rather_than_overwriting(self, scoped, rutf):
        """`commodity_upsert` is an upsert: this would have rewritten RUTF."""
        response = scoped.post(reverse("supply_chain:product_create"), {**PRODUCT, "slug": "rutf"})
        assert response.status_code == 200
        body = response.content.decode()
        assert "Ready-to-use therapeutic food" in body, "the message must name what it would have overwritten"

        rutf.refresh_from_db()
        assert rutf.name == "Ready-to-use therapeutic food"

    def test_editing_keeps_the_slug_fixed(self, scoped, rutf):
        """An upsert under a new slug forks a second row; it does not rename."""
        response = scoped.post(
            reverse("supply_chain:product_edit", args=[rutf.slug]),
            {**PRODUCT, "slug": "something-else", "name": "Renamed"},
        )
        assert response.status_code == 302
        assert not Commodity.objects.filter(slug="something-else").exists()
        rutf.refresh_from_db()
        assert rutf.name == "Renamed", "everything else still saves"

    def test_a_product_from_another_programme_is_not_found(self, scoped):
        theirs = Commodity.objects.create(scope_key="prog:99999", slug="theirs", name="Theirs")
        assert scoped.get(reverse("supply_chain:product_edit", args=[theirs.slug])).status_code == 404


class TestAddingATradeItem:
    def test_an_item_is_created_against_its_product(self, scoped, rutf):
        response = scoped.post(reverse("supply_chain:item_create"), item_post(commodity=rutf.pk))
        assert response.status_code == 302

        made = Item.objects.get(sku="NUT-RUTF-92")
        assert made.commodity_id == rutf.pk
        assert made.base_per_pack == 150, "as the manufacturer states it, not as the product assumes"

    def test_the_product_picker_offers_only_this_programmes_catalogue(self, scoped, rutf):
        Commodity.objects.create(scope_key="prog:99999", slug="theirs", name="Somebody else's product")
        body = scoped.get(reverse("supply_chain:item_create")).content.decode()
        assert "Ready-to-use therapeutic food" in body
        assert "Somebody else's product" not in body

    def test_arriving_from_a_product_page_preselects_it(self, scoped, rutf):
        body = scoped.get(f"{reverse('supply_chain:item_create')}?product=rutf").content.decode()
        chosen = re.search(r'<option value="(\d+)"\s+selected', body)
        assert chosen and chosen.group(1) == str(rutf.pk)

    def test_a_mistyped_barcode_is_refused_by_the_operation_not_stored(self, scoped, rutf):
        """The GS1 check digit is the operation's job; this proves the refusal reaches the page."""
        response = scoped.post(
            reverse("supply_chain:item_create"),
            item_post(commodity=rutf.pk, gtin_base="30000000000009"),
        )
        assert response.status_code == 200, "a refusal re-renders rather than redirecting"
        assert not Item.objects.filter(sku="NUT-RUTF-92").exists()

    def test_an_sku_already_in_use_is_refused(self, scoped, rutf):
        Item.objects.create(scope_key=SCOPE, sku="NUT-RUTF-92", name="Already here", commodity=rutf)
        response = scoped.post(reverse("supply_chain:item_create"), item_post(commodity=rutf.pk))
        assert response.status_code == 200
        assert "Already here" in response.content.decode()

    def test_editing_an_item_saves(self, scoped, rutf):
        item = Item.objects.create(scope_key=SCOPE, sku="NUT-RUTF-92", name="Old name", commodity=rutf)
        response = scoped.post(
            reverse("supply_chain:item_edit", args=[item.pk]),
            item_post(commodity=rutf.pk, name="Plumpy'Nut 92 g", base_per_pack="144"),
        )
        assert response.status_code == 302
        item.refresh_from_db()
        assert item.name == "Plumpy'Nut 92 g"
        assert item.base_per_pack == 144


class TestAddingASupplier:
    def test_a_supplier_is_created_in_this_program(self, scoped):
        response = scoped.post(
            reverse("supply_chain:supplier_create"),
            {"name": "Northwind Foods", "type": "manufacturer", "status": "identified", "country": "ng", "city": ""},
        )
        assert response.status_code == 302
        made = Supplier.objects.get(org__name="Northwind Foods")
        assert made.scope_key == SCOPE
        assert made.country == "NG", "stored upper case, so 'ng' and 'NG' are one country"

    def test_the_same_name_twice_is_refused(self, scoped):
        Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods")
        response = scoped.post(
            reverse("supply_chain:supplier_create"),
            {"name": "  northwind foods  ", "status": "identified", "country": ""},
        )
        assert response.status_code == 200
        assert Supplier.objects.filter(scope_key=SCOPE).count() == 1

    def test_a_near_match_is_allowed_because_it_is_often_a_real_second_company(self, scoped):
        Supplier.objects.enrol(scope_key=SCOPE, name="Nutriset")
        response = scoped.post(
            reverse("supply_chain:supplier_create"),
            {"name": "Nutriset Nigeria", "status": "identified", "country": ""},
        )
        assert response.status_code == 302
        assert Supplier.objects.filter(scope_key=SCOPE).count() == 2

    def test_editing_a_supplier_saves_and_stays_on_the_same_row(self, scoped):
        supplier = Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods", status="identified")
        response = scoped.post(
            reverse("supply_chain:supplier_edit", args=[supplier.pk]),
            {"name": "Northwind Foods", "type": "manufacturer", "status": "quoting", "country": "NG", "city": "Kano"},
        )
        assert response.status_code == 302
        assert response.url == reverse("supply_chain:supplier_detail", args=[supplier.pk])
        supplier.refresh_from_db()
        assert supplier.status == "quoting"
        assert supplier.city == "Kano"
        assert Supplier.objects.filter(scope_key=SCOPE).count() == 1, "an edit is not a second supplier"

    def test_a_supplier_from_another_programme_is_not_found(self, scoped):
        theirs = Supplier.objects.enrol(scope_key="prog:99999", name="Theirs")
        assert scoped.get(reverse("supply_chain:supplier_edit", args=[theirs.pk])).status_code == 404

    def test_the_edit_screen_opens_with_the_companys_details(self, scoped):
        """Name, type and country are the company's, not model fields on the
        program's supplier -- so the form has to be handed them, or an edit
        opens blank and saving it would clear them."""
        supplier = Supplier.objects.enrol(
            scope_key=SCOPE, name="Northwind Foods", type="manufacturer", country="NG", city="Kano"
        )
        response = scoped.get(reverse("supply_chain:supplier_edit", args=[supplier.pk]))
        form = response.context["form"]
        assert form["name"].value() == "Northwind Foods"
        assert form["type"].value() == "manufacturer"
        assert form["country"].value() == "NG"
        assert form["city"].value() == "Kano"
        assert "every program that buys from them" in response.content.decode()

    def test_contacts_survive_an_edit_that_does_not_show_them(self, scoped):
        """The form omits `contacts`, so the operation must not clear them."""
        supplier = Supplier.objects.enrol(
            scope_key=SCOPE,
            name="Northwind Foods",
            status="identified",
            contacts=[{"name": "A. Buyer", "email": "someone@example.invalid"}],
        )
        scoped.post(
            reverse("supply_chain:supplier_edit", args=[supplier.pk]),
            {"name": "Northwind Foods", "status": "quoting", "country": "", "city": ""},
        )
        supplier.refresh_from_db()
        assert supplier.contacts == [{"name": "A. Buyer", "email": "someone@example.invalid"}]


class TestTheScreensAreReachable:
    def test_the_catalogue_offers_both_kinds_of_addition(self, scoped):
        body = scoped.get(reverse("supply_chain:catalogue")).content.decode()
        assert reverse("supply_chain:product_create") in body
        assert reverse("supply_chain:item_create") in body

    def test_a_product_page_offers_editing_and_adding_an_item_to_it(self, scoped, rutf):
        body = scoped.get(reverse("supply_chain:product_detail", args=[rutf.slug])).content.decode()
        assert reverse("supply_chain:product_edit", args=[rutf.slug]) in body
        assert f"{reverse('supply_chain:item_create')}?product={rutf.slug}" in body

    def test_an_item_page_offers_editing(self, scoped, rutf):
        item = Item.objects.create(scope_key=SCOPE, sku="X", name="X", commodity=rutf)
        body = scoped.get(reverse("supply_chain:item_detail", args=[item.pk])).content.decode()
        assert reverse("supply_chain:item_edit", args=[item.pk]) in body

    def test_the_supplier_directory_and_a_supplier_offer_their_screens(self, scoped):
        supplier = Supplier.objects.enrol(scope_key=SCOPE, name="Northwind Foods")
        directory = scoped.get(reverse("supply_chain:suppliers")).content.decode()
        assert reverse("supply_chain:supplier_create") in directory

        detail = scoped.get(reverse("supply_chain:supplier_detail", args=[supplier.pk])).content.decode()
        assert reverse("supply_chain:supplier_edit", args=[supplier.pk]) in detail


class TestTheScreensRefuseWithoutAProgramme:
    """Every one of these builds a scoped queryset, so an unscoped GET is the
    500 this guard exists to prevent."""

    def test_every_reference_write_screen_asks_for_a_programme(self, client, user):
        for name, args in [
            ("supply_chain:product_create", []),
            ("supply_chain:item_create", []),
            ("supply_chain:supplier_create", []),
        ]:
            response = client.get(reverse(name, args=args))
            assert response.status_code == 200, name
            assert "No programme selected" in response.content.decode(), name
