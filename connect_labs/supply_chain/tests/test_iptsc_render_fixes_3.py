"""What the IPTSc shortfall render (iteration 4) still showed.

THIS REPOSITORY IS PUBLIC. Every organisation, product, reference and figure
here is invented. The world is iteration 1's (test_iptsc_render_fixes.py).
"""

import pytest

from connect_labs.supply_chain.tests import test_iptsc_render_fixes as first
from connect_labs.supply_chain.tests.test_iptsc_render_fixes import _page, _visible, op

pytestmark = pytest.mark.django_db

da, scoped, world, played = first.da, first.scoped, first.world, first.played


class TestTheCoveringOrderPage:
    def test_a_bundled_order_says_it_was_paid_inside_the_setup_fee(self, scoped, played):
        text = _visible(_page(scoped, "order_detail", played["cover"]["id"]))
        assert "Not purchased" not in text
        assert "Bought out of Sahel Community Health Initiative's setup fee" in text

    def test_the_cover_field_is_labelled_as_the_reason(self, scoped, played):
        text = _visible(_page(scoped, "order_detail", played["cover"]["id"]))
        assert "Why this order Covers the shortfall on IPTSC-PO-0715" in text
        assert "Placed to" not in text


class TestTheBuyerPicker:
    def test_the_programmes_own_organisations_come_first(self, scoped, da, world):
        # An organisation that sorts before SCHI and has nothing to do with the programme.
        op(da, "org_upsert", data={"slug": "aardvark", "name": "Aardvark Relief"})
        body = _page(scoped, "contract_create")
        select = body.split('name="buyer_org"', 1)[1].split("</select>", 1)[0]
        assert select.index("Sahel Community Health Initiative") < select.index("Aardvark Relief")


class TestTheStockScreens:
    def test_an_empty_store_says_what_it_is_waiting_for(self, scoped, world):
        row = _visible(_page(scoped, "stock").split('data-ledger="', 1)[1].split("</tr>", 1)[0])
        assert "expected: 700 packets of IPTSc three-day packet (standard) from Harmattan Health Supplies" in row

    def test_the_movements_page_keeps_the_stock_tab_current(self, scoped, played):
        body = _page(scoped, "movements", supply_point_id=played["store"]["id"], item_id=played["item"]["id"])
        tab = body.split(">Stock</a>", 1)[0].rsplit("<a ", 1)[1]
        assert "bg-brand-indigo" in tab


def test_the_order_header_shows_when_it_was_signed(scoped, played):
    text = _visible(_page(scoped, "order_detail", played["order"]["id"]))
    assert "Signed 15 Jul 2026" in text
