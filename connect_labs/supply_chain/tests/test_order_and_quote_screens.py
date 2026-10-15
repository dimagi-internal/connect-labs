"""The order's per-buyer panel and the quote page: what is known, in the product's own unit.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on chlorine_world.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.templatetags.supply_chain_extras import buyer_comparison
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import (
    _comparable_pair,
    _compare_url,
    op,
)

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


class TestThePerBuyerPanelSaysWhatIsKnown:
    """CodeRabbit on #1975: the panel claimed different amounts from totals that
    were equal, and "none are payable" from totals that were unconfirmed."""

    def test_equal_confirmed_totals_are_said_to_be_the_same_and_nothing_more(self):
        cell = {"amount": "2400", "currency": "USD"}
        assert buyer_comparison({"programme_org": cell, "partner_org": dict(cell), "agency": dict(cell)}) == {
            "state": "same",
            "unconfirmed": [],
        }

    def test_the_same_amount_written_two_ways_is_the_same(self):
        compared = buyer_comparison(
            {
                "programme_org": {"amount": "2400", "currency": "USD"},
                "agency": {"amount": "2400.00", "currency": "USD"},
            }
        )
        assert compared["state"] == "same"

    def test_any_unconfirmed_total_means_it_cannot_be_said(self):
        cell = {"amount": "2400", "currency": "USD"}
        compared = buyer_comparison(
            {"programme_org": cell, "partner_org": {"unconfirmed": ["duty rate not known"]}, "agency": cell}
        )
        assert compared == {"state": "unknown", "unconfirmed": ["partner_org"]}

    def test_confirmed_totals_that_differ_are_said_to_differ(self):
        compared = buyer_comparison(
            {
                "programme_org": {"amount": "2400", "currency": "USD"},
                "partner_org": {"amount": "2760", "currency": "USD"},
                "agency": {"unconfirmed": ["x"]},
            }
        )
        assert compared == {"state": "differ", "unconfirmed": ["agency"]}

    def test_the_order_page_never_infers_none_payable_from_unconfirmed_totals(self, client_in_programme, da, world):
        order = op(
            da,
            "contract_create",
            data={
                "supplier_id": world["supplier"]["id"],
                "commodity_slug": "chlorine",
                "buyer_of_record": "partner_org",
                "buyer_org_id": world["us"]["id"],
                "reference": "CL-9",
                "quantity": "600",
                "quantity_unit": "jerry_can",
                "unit_price": "4.00",
                "unit_price_unit": "per_pack",
                "currency": "USD",
                "freight_basis": "included",
                "duties_basis": "not_specified",
                "vat_basis": "not_specified",
                "source": "we_recorded",
            },
        )
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        panel = body.split("The same order, per buyer", 1)[1][:4000]
        assert "none are payable" not in panel
        assert "costing different amounts" not in panel
        # The status view states each buyer's total and leaves the reading to it: an
        # unconfirmed one says so, rather than a sentence drawing a conclusion.
        assert "Unconfirmed" in panel


class TestTheQuotePageAsksNoCourseOfAConsumable:
    """The comparison stopped offering per-course figures, and stopped asking
    for a treatment protocol, for a category that has no course. The page for
    one quote kept doing both -- "USD per course: Unconfirmed" against
    water-treatment chlorine, and an internal question nobody could close. One
    rule, from the same place, on both screens."""

    def _quote_on(self, da, world, slug, name, category):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": slug,
                "name": name,
                "category": category,
                "base_unit": "L",
                "pack_unit": "jerry_can",
                "base_per_pack": 20,
            },
        )
        tender = op(
            da,
            "tender_create",
            data={
                "label": f"{name} tender",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": slug, "quantity": "600", "quantity_unit": "jerry_can"}],
            },
        )
        return op(
            da,
            "quote_record",
            data={
                "tender_id": tender["id"],
                "commodity_slug": slug,
                "supplier_id": world["supplier"]["id"],
                "as_quoted_amount": "4.00",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
            },
        )

    def _page(self, client, quote):
        return client.get(reverse("supply_chain:procurement_quote_detail", args=[quote["id"]])).content.decode()

    def test_a_consumable_quote_shows_no_course_figure_and_asks_no_protocol(self, client_in_programme, da, world):
        quote = self._quote_on(da, world, "dispenser-chlorine", "Dispenser chlorine solution", "consumable")
        body = self._page(client_in_programme, quote)
        assert "USD per course" not in body
        assert "USD per child treated" not in body
        assert "course definition" not in body
        assert "treatment protocol" not in body
        # The rest of the derivation is still there, named in the product's unit.
        assert "USD per jerry can" in body

    def test_the_operation_agrees_with_the_page(self, da, world):
        quote = self._quote_on(da, world, "dispenser-chlorine", "Dispenser chlorine solution", "consumable")
        detail = op(da, "quote_get", quote_id=quote["id"])
        assert not {"usd_per_course", "usd_per_child_treated"} & set(detail["figures"])
        assert "course_definition" not in {q["key"] for q in detail["missing"]}

    def test_a_therapeutic_food_quote_still_shows_and_asks_them(self, client_in_programme, da, world):
        quote = self._quote_on(da, world, "rutf-paste", "RUTF paste", "therapeutic_food")
        body = self._page(client_in_programme, quote)
        assert "USD per course" in body
        assert "USD per child treated" in body
        assert "treatment protocol" in body


class TestAFigureHeaderNamesItsUnitInWords:
    """The comparison's header read "USD per jerry_can", the stored code."""

    def test_the_comparison_header_reads_jerry_can(self, client_in_programme, da, world):
        _comparable_pair(da, world)
        body = client_in_programme.get(_compare_url(world)).content.decode()
        grid = re.search(r'<table [^>]*data-testid="comparison-grid".*?</table>', body, re.S).group(0)
        assert re.search(r">Landed per jerry can<", grid)
        assert "jerry_can" not in grid

    def test_the_operation_labels_its_columns_the_same_way(self, da, world):
        _comparable_pair(da, world)
        comparison = op(da, "tender_compare", tender_id=world["tender"]["id"], commodity_slug="chlorine")
        labels = {c["key"]: c["label"] for c in comparison["columns"]}
        assert labels["usd_per_pack_normalized"] == "USD per jerry can"

    def test_the_quote_page_names_the_same_unit(self, client_in_programme, da, world):
        body = client_in_programme.get(
            reverse("supply_chain:procurement_quote_detail", args=[world["quote"]["id"]])
        ).content.decode()
        assert "USD per jerry can" in body
        assert "USD per pack<" not in body
