"""What the IPTSc shortfall render (iteration 2) still showed, screen by screen.

THIS REPOSITORY IS PUBLIC. Every organisation, product, reference and figure
here is invented. The world is iteration 1's (test_iptsc_render_fixes.py): an
order of 700 packets, 450 dispatched and received through two update links,
the missing 250 bought locally by the partner out of its setup fee.
"""

import re
from datetime import date

import pytest

from connect_labs.supply_chain.models import Contract, SupplyPoint
from connect_labs.supply_chain.tests import test_iptsc_render_fixes as first
from connect_labs.supply_chain.tests.test_iptsc_render_fixes import _cover, _dispatch, _page, _receive, _visible, op
from connect_labs.supply_chain.update_links import service
from connect_labs.supply_chain.update_links.forms import RecordReceiptForm
from connect_labs.supply_chain.update_links.models import UpdateLink

pytestmark = pytest.mark.django_db

# Iteration 1's world, fixture for fixture (not its tests: importing those
# would run them twice).
da, scoped, world, played = first.da, first.scoped, first.world, first.played

DUE = date(2026, 8, 29)  # signed 15 Jul 2026 + 45 days promised


def _days_past():
    return (date.today() - DUE).days


def _row(body, reference):
    """The visible text of the orders-list row naming this reference."""
    for chunk in body.split("<tr")[1:]:
        if f">{reference}</span>" in chunk:
            return _visible(chunk.split("</tr>", 1)[0].split(">", 1)[1])
    raise AssertionError(f"no row for {reference}")


# ---- 1. a partner's organisation-wide link records the cover order arriving --


def _org_link(da, world):
    """SCHI's link issued as "Everything involving" the partner (#1987)."""
    issued = op(
        da, "update_link_issue", data={"org_id": world["partner"]["id"], "coverage": "organisation", "label": "SCHI"}
    )
    return UpdateLink.objects.get(pk=issued["id"])


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


# ---- 2. the orders list --------------------------------------------------------


class TestTheOrdersList:
    def test_a_late_order_says_how_late_as_the_order_page_does(self, scoped, world):
        _receive(world, _dispatch(world))
        row = _row(_page(scoped, "orders"), "IPTSC-PO-0715")
        assert f"{_days_past()} days past due" in row
        page = _visible(_page(scoped, "order_detail", world["order"]["id"]))
        assert f"{_days_past()} days past the promised lead time" in page

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

    def test_the_explanation_is_behind_a_disclosure(self, scoped, played):
        body = _page(scoped, "orders")
        details = body.split("<details", 1)[1].split("</details>", 1)[0]
        assert "About orders" in details
        assert "An award is a decision" in details


# ---- 3. the trade item page -----------------------------------------------------


class TestTheTradeItemPage:
    def test_what_is_inside_is_a_subheading_like_how_it_is_packed(self, scoped, played):
        body = _page(scoped, "item_detail", played["item"]["id"])
        packed = re.search(r"<(h\d)[^>]*>\s*How it is packed", body).group(1)
        assert re.search(rf"<{packed}[^>]*>\s*What is inside", body)

    def test_an_unrecorded_packs_per_case_is_not_shown(self, scoped, played):
        assert "Packs per case" not in _page(scoped, "item_detail", played["item"]["id"])

    def test_the_helper_copy_does_not_name_another_commodity(self, scoped, played):
        assert "RUTF" not in _visible(_page(scoped, "item_detail", played["item"]["id"]))

    def test_missing_values_have_one_neutral_treatment(self, scoped, played):
        body = _page(scoped, "item_detail", played["item"]["id"])
        packing = body.split("How it is packed", 1)[1].split("</dl>", 1)[0]
        assert "not stated" not in packing
        assert "text-message-warning-text" not in packing
        assert "not recorded" in packing


# ---- 4. the order page -----------------------------------------------------------


def _card(body):
    return body.split('id="match"', 1)[1].split("</table>", 1)[0]


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
        outstanding = card.split("Still outstanding", 1)[0].rsplit("<tr", 1)[1]
        assert "font-semibold" in outstanding
        safe = card.split("Safe to pay now", 1)[0].rsplit("<tr", 1)[1]
        assert "font-semibold" not in safe

    def test_a_covered_order_says_received_on_this_order(self, scoped, played):
        card = _visible(_card(_page(scoped, "order_detail", played["order"]["id"])))
        assert "Received on this order 450 packets" in card

    def test_equal_totals_per_buyer_are_one_line(self, scoped, played):
        text = _visible(_page(scoped, "order_detail", played["order"]["id"]))
        assert "Same total whichever party buys: USD 1,260.00" in text

    def test_the_header_is_labelled_fields_under_a_plain_heading(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        header = body.split("Orders</a> ›", 1)[1].split("Edit order", 1)[0]
        heading = header.split("<h2", 1)[1].split("</h2>", 1)[0]
        assert "text-brand-indigo" not in heading
        labels = [_visible(t) for t in re.findall(r"<dt[^>]*>(.*?)</dt>", header, re.S)]
        for label in ("Buyer", "Status", "Recorded by"):
            assert label in labels
        assert "· Dimagi" not in _visible(header)
        # The fulfilment status is a pill; how it was paid for is a plain tag, so the
        # two no longer read as two statuses (iteration 4).
        assert re.search(r'<span class="[^"]*rounded-full[^"]*"[^>]*>\s*short', header)
        cover_header = _page(scoped, "order_detail", played["cover"]["id"]).split("Edit order", 1)[0]
        tag = re.search(r'<span class="([^"]*)"[^>]*>\s*Bundled in setup fee', cover_header).group(1)
        assert "rounded-full" not in tag and "bg-gray-100" in tag

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
        received = _visible(body.split(">Received</h3>", 1)[1].split("</table>", 1)[0])
        assert "The programme, for Sahel Community Health Initiative (they told us)" in received

    def test_a_missing_certificate_is_not_alarm_coloured(self, scoped, played):
        body = _page(scoped, "order_detail", played["order"]["id"])
        assert not re.search(r"text-amber-800[^>]*>\s*none\s*<", body)


# ---- 5. stock across the network ----------------------------------------------


class TestTheStockPage:
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

    def test_the_send_column_says_what_it_is(self, scoped, played):
        body = _page(scoped, "stock")
        headers = [_visible(h) for h in re.findall(r"<th[^>]*>(.*?)</th>", body, re.S)]
        assert "To reach its maximum" in headers
        assert "Send" not in headers

    def test_muted_text_is_body_grey(self, scoped, played):
        body = _page(scoped, "stock")
        assert not re.search(r"text-gray-400[^>]*>\s*never", body)
        assert not re.search(r'text-gray-500"?>\s*1 supply point', body)
        assert not re.search(r"text-gray-500[^>]*>\s*Cover is measured", body)
        assert not re.search(r"text-gray-500[^>]*>\s*Consumption is derived", body)

    def test_the_balance_reads_as_a_link(self, scoped, played):
        body = _page(scoped, "stock")
        cell = body.split('data-ledger="', 1)[1].split("</td>", 1)[0]
        link = re.search(r"<a ([^>]*)>\s*700 packets", cell).group(1)
        assert "font-semibold" in link
        assert "text-brand-indigo" in link
        assert "stock/movements/" in link


# ---- iteration 3: what the render still showed -------------------------------


class TestIterationThree:
    def test_the_partners_link_reads_the_short_order_as_covered(self, da, world):
        from django.test import Client

        issued = op(
            da, "update_link_issue", data={"org_id": world["partner"]["id"], "coverage": "organisation", "label": "x"}
        )
        _receive(world, _dispatch(world))
        _cover(da, world, received=False)
        body = _visible(first._public(Client(), issued["token"]))
        order = body.split("IPTSC-PO-0715", 1)[1][:60]
        assert "short — covered by SCHI-LP-0921" in order
        assert "part received" not in order.lower()

    def test_the_distributors_link_does_not_learn_the_covers_reference(self, da, world):
        from django.test import Client

        _receive(world, _dispatch(world))
        _cover(da, world, received=False)
        body = _visible(first._public(Client(), world["distributor_token"]))
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
