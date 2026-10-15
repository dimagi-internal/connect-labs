"""The IPTSc shortfall, screen by screen: the order and its update-link panels, the orders list,
stock across the network, the trade item page, and the update link page itself.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on shortfall_world.
"""

import re
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Contract, SupplyPoint
from connect_labs.supply_chain.tests import shortfall_world
from connect_labs.supply_chain.tests.shortfall_world import _cover, _dispatch, _page, _public, _receive, _visible, op
from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.forms import RecordReceiptForm
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = shortfall_world.da
scoped = shortfall_world.scoped
world = shortfall_world.world
played = shortfall_world.played


class TestEachLinkPanelListsOnlyItsOwnRecords:
    def test_the_dispatch_sits_under_the_distributor_and_the_receipt_under_the_partner(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        panels = {}
        for chunk in body.split('data-link-updates="')[1:]:
            org, rest = chunk.split('"', 1)
            panels[org] = _visible(rest.split("</section>", 1)[0])
        assert set(panels) == {"Harmattan Health Supplies", "Sahel Community Health Initiative"}
        assert "HHS-WB-3310" in panels["Harmattan Health Supplies"]
        assert "SCHI-GRN-0922" not in panels["Harmattan Health Supplies"]
        assert "SCHI-GRN-0922" in panels["Sahel Community Health Initiative"]
        assert "HHS-WB-3310: 450" not in panels["Sahel Community Health Initiative"]


class TestACoveredShortOrderHasNothingOutstanding:
    def test_the_card_shows_the_cover_and_nothing_still_outstanding(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        card = _visible(body.split('id="match"', 1)[1].split("</table>", 1)[0])
        # "on this order": the cover's 250 is received on the other one (iteration 2).
        assert "Received on this order 450 packets" in card
        assert re.search(r"Covered by SCHI-LP-0921 from Tamarind Pharmacy Wholesale 250 packets", card)
        assert "Still outstanding 0 packets" in card
        assert "Still outstanding 250" not in card

    def test_an_uncovered_short_order_still_says_what_is_outstanding(self, scoped, da, world):
        _receive(world, _dispatch(world))
        body = _page(scoped, "order_detail", world["order"]["id"])
        card = _visible(body.split('id="match"', 1)[1].split("</table>", 1)[0])
        assert "Still outstanding 250 packets" in card


class TestTheStockPage:
    def test_no_consumption_rate_is_not_painted_as_a_warning(self, scoped, played):
        # Said once, across the forecast columns (iteration 2), and never orange.
        body = _page(scoped, "stock")
        assert "No consumption yet" in body
        assert not re.search(r"text-orange-700[^>]*>\s*No consumption yet", body)

    def test_one_supply_point_is_singular(self, scoped, played):
        text = _visible(_page(scoped, "stock"))
        assert "1 supply point · 1 has no count yet" in text

    def test_a_kit_leads_with_the_packets_it_is_counted_in(self, scoped, played):
        body = _page(scoped, "stock")
        cell = body.split('data-ledger="', 1)[1].split("</td>", 1)[0]
        text = _visible(cell)
        assert text.index("700 packets") < text.index("14 cartons")

    def test_the_balance_links_to_the_movements_behind_it(self, scoped, played):
        body = _page(scoped, "stock")
        cell = body.split('data-ledger="', 1)[1].split("</td>", 1)[0]
        href = re.search(r'href="([^"]+)"', cell).group(1).replace("&amp;", "&")
        assert href.startswith(reverse("supply_chain:movements"))
        assert f"supply_point_id={played['store']['id']}" in href
        assert f"item_id={played['item']['id']}" in href
        history = _visible(scoped.get(href).content.decode())
        assert "SCHI district store" in history
        assert "450 packets" in history and "250 packets" in history
        assert "SCHI-GRN-0922" in history and "SCHI-GRN-0923" in history


class TestTheTradeItemPage:
    def test_the_breadcrumb_names_the_item_and_the_code_is_not_a_chip_under_the_title(self, scoped, played):
        body = _page(scoped, "item_detail", played["item"]["id"])
        crumb = _visible(body.split('<nav class="text-sm', 1)[1].split("</nav>", 1)[0])
        assert crumb.endswith("IPTSc three-day packet (standard)")
        assert "iptsc-3day-packet" not in crumb
        header = body.split("<h1", 1)[1].split("How it is packed", 1)[0]
        assert "iptsc-3day-packet" not in header
        # Still findable, among the identifiers.
        assert "iptsc-3day-packet" in body


class TestTheUpdateLinkPage:
    def test_the_receiving_partner_is_not_offered_a_dispatch(self, client, world):
        _dispatch(world)
        body = _public(client, world["partner_token"])
        assert "record a dispatch" not in body.lower()
        assert "move a dispatch along" not in body.lower()
        titles = re.findall(r'<span class="text-base font-semibold text-gray-900">([^<]+)</span>', body)
        assert titles[0] == "Record goods received"

    def test_the_distributor_is_still_offered_a_dispatch(self, client, world):
        body = _public(client, world["distributor_token"])
        titles = re.findall(r'<span class="text-base font-semibold text-gray-900">([^<]+)</span>', body)
        assert "Record a dispatch" in titles

    def test_a_dispatch_line_says_when_it_left_and_how(self, client, world):
        _dispatch(world)
        text = _visible(_public(client, world["distributor_token"]))
        assert "HHS-WB-3310 — 450 packets, dispatched, left 19 Sep 2026 via Kano Freight, expected 22 Sep 2026" in text

    def test_a_delivered_dispatch_says_when_it_arrived_not_when_it_was_expected(self, client, world):
        _receive(world, _dispatch(world))
        text = _visible(_public(client, world["partner_token"]))
        assert "HHS-WB-3310 — 450 packets, left 19 Sep 2026 via Kano Freight, delivered 24 Sep 2026" in text
        assert "expected 22 Sep" not in text

    def test_the_banner_is_a_sentence_about_the_dispatch(self, client, world):
        _dispatch(world)
        text = _visible(_public(client, world["distributor_token"], done="record_shipment"))
        assert (
            "Recorded: dispatch HHS-WB-3310, 450 packets, batch DP-2608-A. The programme team can see it now." in text
        )
        assert "record a dispatch —" not in text

    def test_the_banner_is_a_sentence_about_the_receipt(self, client, world):
        _receive(world, _dispatch(world))
        text = _visible(_public(client, world["partner_token"], done="record_receipt"))
        assert "Recorded: receipt SCHI-GRN-0922, 450 packets accepted, batch DP-2608-A at SCHI district store." in text
        assert "record goods received —" not in text


class TestABundledOrderPage:
    def test_it_offers_no_invoice(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        assert "Record an invoice" not in body
        assert reverse("supply_chain:invoice_record", args=[played["cover"]["id"]]) not in body

    def test_the_evidence_note_names_no_duty_relief(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        evidence = _visible(body.split(">Evidence<", 1)[1])
        assert "duty relief" not in evidence

    def test_the_cost_card_is_not_about_importing(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        card = _visible(body.split('id="cost"', 1)[1].split("</div>", 2)[1])
        assert "Cost" in _visible(body.split('id="cost"', 1)[1].split("</span>", 1)[0])
        assert "assuming" not in _visible(body.split('id="cost"', 1)[1].split("<table", 1)[0])
        assert "Landed cost" not in card

    def test_a_bought_order_still_offers_an_invoice_and_names_the_duty_relief(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        assert "Record an invoice" in body
        assert "Landed cost" in body


class TestTheOrderHeader:
    def test_it_names_the_trade_item_the_delivery_point_and_who_told_us(self, scoped, played):
        body = _page(scoped, "order_detail", played["cover"]["id"])
        header = _visible(body.split('data-testid="order-header"', 1)[1].split('data-testid="on-us"', 1)[0])
        assert "IPTSc three-day packet (standard)" in header
        assert "SCHI district store" in header
        assert "a partner told us" in header


class TestTheOrdersList:
    def test_each_order_says_when_it_is_due_and_how_much_arrived(self, scoped, played):
        text = _visible(_page(scoped, "orders"))
        assert "due 29 Aug 2026" in text
        assert "450 of 700 packets received" in text
        assert "250 of 250 packets received" in text


DUE = date(2026, 8, 29)  # signed 15 Jul 2026 + 45 days promised


def _days_past():
    return (date.today() - DUE).days


def _row(body, reference):
    """The visible text of the orders-list row naming this reference."""
    for chunk in body.split("<tr")[1:]:
        if f">{reference}</span>" in chunk:
            return _visible(chunk.split("</tr>", 1)[0].split(">", 1)[1])
    raise AssertionError(f"no row for {reference}")


def _org_link(da, world):
    """SCHI's link issued as "Everything involving" the partner (#1987)."""
    issued = op(
        da, "update_link_issue", data={"org_id": world["partner"]["id"], "coverage": "organisation", "label": "SCHI"}
    )
    return UpdateLink.objects.get(pk=issued["id"])


def _card(body):
    return body.split('id="match"', 1)[1].split("</table>", 1)[0]


class TestAPartnersOrgLinkReceivesTheCoverOrder:
    def test_an_order_placed_after_the_link_can_be_received_through_it(self, da, world):
        link = _org_link(da, world)
        cover = _cover(da, world, received=False)
        scope = service.scope_for(link)
        assert scope.contracts.filter(pk=cover["id"]).exists()
        # Offered by the receipt form, and not as something the partner supplies.
        form = RecordReceiptForm(scope=scope)
        assert form.fields["contract"].queryset.filter(pk=cover["id"]).exists()
        assert not scope.supplied.filter(pk=cover["id"]).exists()

        service.submit(
            link,
            "record_receipt",
            {
                "contract": Contract.objects.get(pk=cover["id"]),
                "supply_point": SupplyPoint.objects.get(pk=world["store"]["id"]),
                "received_on": date(2026, 9, 22),
                "reference": "SCHI-GRN-0923",
                "quantity_accepted": "250",
                "unit_basis": "base",
            },
        )
        match = op(da, "contract_match", contract_id=cover["id"])
        assert match["received"]["amount"] in ("250", "250.0000", "250.00")

    def test_the_distributors_listed_link_does_not_gain_the_partners_orders(self, da, world):
        cover = _cover(da, world, received=False)
        scope = service.scope_for(world["distributor_link"])
        assert not scope.contracts.filter(pk=cover["id"]).exists()


class TestTheOrdersListRows:
    def test_a_late_order_says_how_late_as_the_order_page_does(self, scoped, world):
        _receive(world, _dispatch(world))
        row = _row(_page(scoped, "orders"), "IPTSC-PO-0715")
        assert f"{_days_past()} days past due" in row
        page = _visible(_page(scoped, "order_detail", world["order"]["id"]).split('data-testid="status-tiles"', 1)[1])
        assert f"Days late {_days_past()}" in page

    def test_a_covered_order_is_not_late(self, scoped, played):
        row = _row(_page(scoped, "orders"), "IPTSC-PO-0715")
        assert "past due" not in row

    def test_each_row_names_the_trade_item(self, scoped, played):
        body = _page(scoped, "orders")
        assert "IPTSc three-day packet (standard)" in _row(body, "IPTSC-PO-0715")
        assert "IPTSc three-day packet (standard)" in _row(body, "SCHI-LP-0921")

    def test_told_by_says_who_recorded_it(self, scoped, played):
        row = _row(_page(scoped, "orders"), "IPTSC-PO-0715")
        assert "Recorded by" in row
        assert "we recorded" not in row


class TestTheTradeItemValues:

    def test_an_unrecorded_packs_per_case_is_not_shown(self, scoped, played):
        assert "Packs per case" not in _page(scoped, "item_detail", played["item"]["id"])

    def test_missing_values_have_one_neutral_treatment(self, scoped, played):
        body = _page(scoped, "item_detail", played["item"]["id"])
        packing = body.split("How it is packed", 1)[1].split("</dl>", 1)[0]
        assert "not stated" not in packing
        assert "text-message-warning-text" not in packing
        assert "not recorded" in packing


class TestTheOrderPage:
    def test_the_feed_names_what_was_recorded_not_a_button(self, scoped, played):
        text = _visible(_page(scoped, "order_detail", played["order"]["id"]))
        assert "Dispatch —" in text
        assert "Goods received —" in text
        assert "Record shipment" not in text
        assert "Record receipt" not in text

    def test_the_link_panel_names_whose_link_it_was(self, scoped, played):
        text = _visible(_page(scoped, "order_detail", played["order"]["id"]))
        assert "update link held by Harmattan Health Supplies" in text
        assert "Supplies's" not in text

    def test_the_card_carries_the_lateness_and_leads_with_what_is_outstanding(self, scoped, world):
        _receive(world, _dispatch(world))
        card = _card(_page(scoped, "order_detail", world["order"]["id"]))
        assert f"Due 29 Aug 2026 — {_days_past()} days past" in _visible(card)

    def test_a_covered_order_says_received_on_this_order(self, scoped, played):
        card = _visible(_card(_page(scoped, "order_detail", played["order"]["id"])))
        assert "Received on this order 450 packets" in card

    def test_equal_totals_per_buyer_are_one_line(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        assert 'data-testid="same-for-any-buyer">same total for any buyer<' in body
        assert "Same total whichever party buys" not in body

    def test_the_header_is_labelled_fields_under_a_plain_heading(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        header = body.split('data-testid="order-header"', 1)[1].split('data-testid="on-us"', 1)[0]
        labels = [_visible(t) for t in re.findall(r"<dt[^>]*>(.*?)</dt>", header, re.S)]
        for label in ("Buyer", "Recorded by"):
            assert label in labels
        assert "· Dimagi" not in _visible(header)
        # The fulfilment status is a chip; how it was paid for is a labelled fact, so the
        # two do not read as two statuses (iteration 4).
        assert re.search(r'<span class="status-chip[^"]*"[^>]*>\s*short', header)
        cover = _page(scoped, "order_detail", played["cover"]["id"])
        cover_header = cover.split('data-testid="order-header"', 1)[1].split('data-testid="on-us"', 1)[0]
        assert re.search(r"<dt[^>]*>Payment</dt><dd[^>]*>Bundled in setup fee", cover_header)

    def test_a_receipt_the_programme_took_down_names_who_told_it(self, scoped, da, world):
        cover = _cover(da, world, received=False)
        op(
            da,
            "receipt_record",
            data={
                "contract_id": cover["id"],
                "supply_point_id": world["store"]["id"],
                "reference": "SCHI-GRN-0923",
                "received_on": "2026-09-22",
                "source": "partner_reported",
                "recorded_by_org_id": world["us"]["id"],
                "lines": [{"item_id": world["item"]["id"], "quantity_accepted": "250", "quantity_unit": "packet"}],
            },
        )
        body = _page(scoped, "order_detail", cover["id"])
        received = _visible(body.split(">Received</h2>", 1)[1].split("</table>", 1)[0])
        assert "The programme, for Sahel Community Health Initiative (they told us)" in received


class TestTheStockRows:
    def test_each_row_names_the_item(self, scoped, played):
        body = _page(scoped, "stock")
        row = _visible(body.split('data-ledger="', 1)[1].split("</tr>", 1)[0])
        assert "IPTSc three-day packet (standard)" in row

    def test_no_consumption_is_one_merged_neutral_cell(self, scoped, played):
        body = _page(scoped, "stock")
        row = body.split('data-ledger="', 1)[1].split("</tr>", 1)[0]
        assert re.search(r'colspan="4"[^>]*>\s*No consumption yet — cover can.t be computed', row)
        assert "Unconfirmed" not in _visible(row)
        assert "cannot be assessed" not in row


class TestWhatEachLinkAndLedgerSays:
    def test_the_partners_link_reads_the_short_order_as_covered(self, da, world):
        from django.test import Client

        issued = op(
            da, "update_link_issue", data={"org_id": world["partner"]["id"], "coverage": "organisation", "label": "x"}
        )
        _receive(world, _dispatch(world))
        _cover(da, world, received=False)
        body = _visible(_public(Client(), issued["token"]))
        order = body.split("IPTSC-PO-0715", 1)[1][:60]
        assert "short — covered by SCHI-LP-0921" in order
        assert "part received" not in order.lower()

    def test_the_distributors_link_does_not_learn_the_covers_reference(self, da, world):
        from django.test import Client

        _receive(world, _dispatch(world))
        _cover(da, world, received=False)
        body = _visible(_public(Client(), world["distributor_token"]))
        assert "SCHI-LP-0921" not in body
        assert "short — covered by another order" in body

    def test_the_item_page_counts_a_kit_in_packets(self, scoped, played):
        body = _page(scoped, "item_detail", played["item"]["id"])
        section = _visible(body.split("Where it is now", 1)[1].split("</table>", 1)[0])
        assert "700 packets" in section

    def test_movements_name_the_supplier_and_add_up_to_the_balance(self, scoped, played):
        body = _page(
            scoped,
            "movements",
            supply_point_id=played["store"]["id"],
            item_id=played["item"]["id"],
        )
        text = _visible(body)
        assert "Harmattan Health Supplies (supplier)" in text
        assert "Tamarind Pharmacy Wholesale (supplier)" in text
        foot = _visible(body.split("data-balance", 1)[1].split("</tr>", 1)[0])
        assert "Balance at SCHI district store" in foot and "700 packets" in foot

    def test_an_empty_store_reads_as_stocked_out(self, scoped, world):
        row = _visible(_page(scoped, "stock").split('data-ledger="', 1)[1].split("</tr>", 1)[0])
        assert "Stocked out" in row
        assert "expected: 700 packets of IPTSc three-day packet (standard) from Harmattan Health Supplies" in row


class TestTheCoveringOrderPage:
    def test_a_bundled_order_says_it_was_paid_inside_the_setup_fee(self, scoped, played):
        text = _visible(_page(scoped, "order_detail", played["cover"]["id"]))
        assert "Not purchased" not in text
        assert "bought out of Sahel Community Health Initiative's setup fee" in text

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


def test_the_order_header_shows_when_it_was_signed(scoped, played):
    text = _visible(_page(scoped, "order_detail", played["order"]["id"]))
    assert "Signed 15 Jul 2026" in text


class TestTheFillTable:
    def test_the_fill_table_drops_a_column_that_repeats_received(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        section = body.split('id="fulfilment"', 1)[1].split("</section>", 1)[0]
        assert "Counts toward it" not in section
        assert "700 packets received" in _visible(section)
