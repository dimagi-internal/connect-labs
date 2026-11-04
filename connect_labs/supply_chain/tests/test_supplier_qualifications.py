"""Qualifications: the usual ones, who lacks them, and the page that records them.

Sophie's RUTF feedback, Suppliers: Qualifications. Invented names only (public repo).
"""

from datetime import date

import pytest
from django.urls import reverse

from connect_labs.supply_chain.reference import qualifications as q
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import _compare_url, op

pytestmark = pytest.mark.django_db

da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world

TODAY = date(2026, 11, 4)


class TestUsualOnes:
    def test_rutf_has_three_usual_ones_and_other_categories_none(self):
        assert [u.name for u in q.usual_for(["therapeutic_food"])] == [
            "UNICEF supplier approval",
            "National product registration",
            "Certificate of analysis for each batch",
        ]
        assert q.usual_for(["equipment"]) == []

    def test_a_held_one_is_matched_by_its_words(self):
        usual = q.usual_for(["therapeutic_food"])
        held = [{"name": "UNICEF approved supplier"}, "NAFDAC registration", {"name": "CoA with every batch"}]
        assert q.missing(held, usual, TODAY) == []

    def test_nothing_held_means_all_missing_and_an_expired_one_is_said_so(self):
        usual = q.usual_for(["therapeutic_food"])
        assert [s for _, s in q.missing([], usual, TODAY)] == ["missing"] * 3
        expired = [{"name": "UNICEF approval", "expires_on": "2026-01-01"}]
        assert ("UNICEF supplier approval", "expired") in [(u.name, s) for u, s in q.missing(expired, usual, TODAY)]

    def test_gaps_lists_only_suppliers_that_lack_something(self):
        everything = [{"name": "UNICEF"}, {"name": "registration"}, {"name": "certificate"}]
        suppliers = [
            {"id": 1, "name": "Has All", "qualifications": everything},
            {"id": 2, "name": "Has None", "qualifications": []},
        ]
        gaps = q.gaps(suppliers, ["therapeutic_food"], TODAY)
        assert [g["name"] for g in gaps] == ["Has None"]
        assert q.gaps(suppliers, ["equipment"], TODAY) == []


def _rutf(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "chlorine",
            "name": "Chlorine",
            "base_unit": "L",
            "pack_unit": "jerry_can",
            "base_per_pack": 20,
            "category": "therapeutic_food",
        },
    )


class TestTheScreens:
    def test_the_editor_saves_and_offers_the_usual_names(self, client_in_programme, da, world):
        _rutf(da)
        url = reverse("supply_chain:supplier_qualifications", args=[world["supplier"]["id"]])
        page = client_in_programme.get(url).content.decode()
        assert "UNICEF supplier approval" in page and 'list="qualification-suggestions"' in page
        data = {
            "quals-TOTAL_FORMS": 1,
            "quals-INITIAL_FORMS": 0,
            "quals-MIN_NUM_FORMS": 0,
            "quals-MAX_NUM_FORMS": 1000,
            "quals-0-name": "UNICEF supplier approval",
            "quals-0-expires_on": "2027-06-30",
            "quals-0-note": "ref 123",
        }
        assert client_in_programme.post(url, data).status_code == 302
        stored = op(da, "supplier_get", supplier_id=world["supplier"]["id"])["qualifications"]
        assert stored == [{"name": "UNICEF supplier approval", "expires_on": "2027-06-30", "note": "ref 123"}]

    def test_the_supplier_page_and_list_say_what_is_not_on_file(self, client_in_programme, da, world):
        _rutf(da)
        detail = client_in_programme.get(reverse("supply_chain:supplier_detail", args=[world["supplier"]["id"]]))
        assert "National product registration" in detail.content.decode()
        listing = client_in_programme.get(reverse("supply_chain:suppliers")).content.decode()
        assert "3 not on file" in listing

    def test_the_list_flags_only_suppliers_asked_to_quote_or_awarded(self, client_in_programme, da, world):
        _rutf(da)
        op(da, "supplier_create", data={"name": "Idle Supplier"})
        listing = client_in_programme.get(reverse("supply_chain:suppliers")).content.decode()
        rows = {
            name: row for row in listing.split("<tr") for name in ("Idle Supplier", "Sahel Chemicals") if name in row
        }
        assert "not on file" in rows["Sahel Chemicals"]
        assert "not on file" not in rows["Idle Supplier"]

    def test_the_comparison_page_warns_before_awarding(self, client_in_programme, da, world):
        _rutf(da)
        body = client_in_programme.get(_compare_url(world)).content.decode()
        assert 'data-testid="qualification-gaps"' in body and "Sahel Chemicals" in body

    def test_no_warning_when_the_product_has_no_usual_list(self, client_in_programme, da, world):
        body = client_in_programme.get(_compare_url(world)).content.decode()
        assert 'data-testid="qualification-gaps"' not in body

    def test_the_tender_page_names_who_lacks_them(self, client_in_programme, da, world):
        _rutf(da)
        body = client_in_programme.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        assert 'data-testid="qualification-gaps"' in body
