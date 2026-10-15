"""A shortfall on one order covered by another -- a partner buying locally what the main
supplier could not deliver -- and the screens that say so.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on field_gaps_world.
"""

from datetime import timedelta

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import field_gaps_world
from connect_labs.supply_chain.tests.field_gaps_world import (
    TODAY,
    _contract,
    _header,
    _receive,
    _short_and_covered,
    _visible,
    op,
)

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = field_gaps_world.da
world = field_gaps_world.world
dispensers = field_gaps_world.dispensers
scoped = field_gaps_world.scoped


class TestAShortfallCoveredByAnotherOrder:
    def test_an_uncovered_shortfall_is_left_open(self, da, world):
        short = _contract(da, world, world["main"], "700")
        _receive(da, world, short, "450")
        match = op(da, "contract_match", contract_id=short["id"])
        assert match["status"] == "part_received"
        assert match["covered_by"] == []

    def test_a_covering_order_closes_it_by_name(self, da, world):
        short = _contract(da, world, world["main"], "700")
        _receive(da, world, short, "450")
        cover = _contract(
            da,
            world,
            world["local"],
            "250",
            covers_shortfall_of_id=short["id"],
            buyer_of_record="partner_org",
            buyer_org_id=world["partner"]["id"],
            consideration="bundled",
            unit_price=None,
            unit_price_unit=None,
        )
        match = op(da, "contract_match", contract_id=short["id"])
        assert match["status"] == "shortfall_covered"
        assert match["covered_by"] == [
            {
                "contract_id": cover["id"],
                "reference": cover["reference"],
                "supplier": {"id": world["local"]["id"], "name": "Local vendor"},
                "quantity": "250",
                "quantity_unit": "packet",
            }
        ]
        # The outstanding quantity is still stated: covered is not the same as
        # received, and the arithmetic stays visible.
        assert match["outstanding"]["amount"] == "250"

    def test_a_covered_order_is_not_reported_late(self, da, world):
        short = _contract(
            da,
            world,
            world["main"],
            "700",
            signed_on=(TODAY - timedelta(days=90)).isoformat(),
            promised_lead_time_days=30,
        )
        _receive(da, world, short, "450")
        _contract(da, world, world["local"], "250", covers_shortfall_of_id=short["id"])
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_delivery_overdue" not in kinds

    def test_a_cancelled_covering_order_does_not_cover_it(self, da, world):
        short = _contract(
            da,
            world,
            world["main"],
            "700",
            signed_on=(TODAY - timedelta(days=90)).isoformat(),
            promised_lead_time_days=30,
        )
        _receive(da, world, short, "450")
        cover = _contract(da, world, world["local"], "250", covers_shortfall_of_id=short["id"])
        op(da, "contract_update", contract_id=cover["id"], data={"status": "cancelled"})
        match = op(da, "contract_match", contract_id=short["id"])
        assert match["status"] == "part_received"
        assert match["covered_by"] == []
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_delivery_overdue" in kinds

    def test_a_fully_received_order_invoiced_beyond_receipt_is_not_late(self, da, world):
        full = _contract(
            da,
            world,
            world["main"],
            "700",
            signed_on=(TODAY - timedelta(days=90)).isoformat(),
            promised_lead_time_days=30,
        )
        _receive(da, world, full, "700")
        op(
            da,
            "invoice_record",
            data={
                "contract_id": full["id"],
                "amount": "1600.00",
                "quantity_billed": "800",
                "quantity_unit": "packet",
                "source": "supplier_reported",
            },
        )
        assert op(da, "contract_match", contract_id=full["id"])["status"] == "over_invoiced"
        kinds = [c["kind"] for c in op(da, "checks_list")["checks"]]
        assert "contract_delivery_overdue" not in kinds

    def test_an_order_cannot_cover_itself(self, da, world):
        short = _contract(da, world, world["main"], "700")
        with pytest.raises(ValueError, match="its own shortfall"):
            op(da, "contract_update", contract_id=short["id"], data={"covers_shortfall_of_id": short["id"]})

    def test_it_must_be_an_order_in_this_programme(self, da, world):
        with pytest.raises(ValueError, match="contract"):
            _contract(da, world, world["local"], "250", covers_shortfall_of_id=999999)


class TestTheMatchSpeaksTheOrdersUnit:
    """Ordered, received, invoiced and the gaps between them in ONE unit.

    An order for 700 packets of an item packed 50 to the carton, invoiced for
    450 packets before anything arrived, read "Ordered 700 packet · Received 0
    carton · Billed beyond what arrived 9 carton": the empty receipt fell back
    to the pack unit and the invoice was restated into it.
    """

    @pytest.fixture
    def packets(self, da, world):
        op(
            da,
            "item_upsert",
            data={
                "sku": "PKT",
                "name": "Packet",
                "commodity_slug": "iptsc",
                "base_unit": "packet",
                "pack_unit": "carton",
                "base_per_pack": 50,
            },
        )
        return op(da, "item_list")[0]

    def test_nothing_received_yet_is_zero_of_what_was_ordered(self, da, world, packets):
        order = _contract(da, world, world["main"], "700", item_id=packets["id"])
        op(
            da,
            "invoice_record",
            data={
                "contract_id": order["id"],
                "amount": "900.00",
                "quantity_billed": "450",
                "quantity_unit": "packet",
                "source": "supplier_reported",
            },
        )
        match = op(da, "contract_match", contract_id=order["id"])
        assert match["received"] == {"amount": "0", "unit": "packet"}
        assert match["invoiced"] == {"amount": "450", "unit": "packet"}
        assert match["over_invoiced"] == {"amount": "450", "unit": "packet"}

    def test_a_receipt_in_cartons_is_restated_in_packets(self, da, world, packets):
        order = _contract(da, world, world["main"], "700", item_id=packets["id"])
        op(
            da,
            "receipt_record",
            data={
                "contract_id": order["id"],
                "supply_point_id": world["store"]["id"],
                "received_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [{"item_id": packets["id"], "quantity_accepted": "9", "quantity_unit": "carton"}],
            },
        )
        match = op(da, "contract_match", contract_id=order["id"])
        assert match["received"] == {"amount": "450", "unit": "packet"}
        assert match["outstanding"] == {"amount": "250", "unit": "packet"}


def test_the_short_order_names_the_order_that_covers_it(scoped, da, world):
    short = _contract(da, world, world["main"], "700")
    _receive(da, world, short, "450")
    cover = _contract(da, world, world["local"], "250", covers_shortfall_of_id=short["id"])
    body = scoped.get(reverse("supply_chain:order_detail", args=[short["id"]])).content.decode()
    assert "Covered by" in body
    assert reverse("supply_chain:order_detail", args=[cover["id"]]) in body
    covering = scoped.get(reverse("supply_chain:order_detail", args=[cover["id"]])).content.decode()
    assert "Covers the shortfall on" in covering
    assert reverse("supply_chain:order_detail", args=[short["id"]]) in covering
    # By the reference people use for it, not by a database id.
    assert short["reference"] in covering


def test_the_orders_list_says_a_short_order_is_covered_and_by_what(scoped, da, world):
    short = _contract(da, world, world["main"], "700", reference="IPTSC-PO-1")
    _receive(da, world, short, "450")
    _contract(da, world, world["local"], "250", reference="LOCAL-1", covers_shortfall_of_id=short["id"])
    body = scoped.get(reverse("supply_chain:orders")).content.decode()
    assert "Part received" in body
    assert "shortfall covered by LOCAL-1" in body
    assert "covers IPTSC-PO-1" in body
    assert "part_received" not in body


def test_a_bundled_order_says_it_was_paid_for_elsewhere_not_that_it_was_not_bought(scoped, da, world):
    """SCHI did pay for the 250 packets -- out of its setup fee. "These goods
    were not bought" is true of a donation and false of this."""
    bundled = _contract(
        da,
        world,
        world["local"],
        "250",
        consideration="bundled",
        unit_price=None,
        unit_price_unit=None,
    )
    body = scoped.get(reverse("supply_chain:order_detail", args=[bundled["id"]])).content.decode()
    assert "were not bought" not in body
    assert "bought out of" in body and "setup fee" in body


def test_billing_that_matches_what_arrived_is_not_called_billed_beyond_it(scoped, da, world):
    order = _contract(da, world, world["main"], "700")
    _receive(da, world, order, "450")
    op(
        da,
        "invoice_record",
        data={
            "contract_id": order["id"],
            "amount": "900.00",
            "quantity_billed": "450",
            "quantity_unit": "packet",
            "source": "supplier_reported",
        },
    )
    body = scoped.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
    assert "Billed beyond what arrived" not in body


def test_the_order_form_offers_the_order_it_covers(scoped, da, world):
    _contract(da, world, world["main"], "700")
    body = scoped.get(reverse("supply_chain:contract_create")).content.decode()
    assert 'name="covers_shortfall_of"' in body


class TestACoveredShortOrderPage:
    def test_a_bundled_order_has_no_billing_rows_and_one_cost_line(self, scoped, da, world):
        _, cover = _short_and_covered(da, world)
        body = scoped.get(reverse("supply_chain:order_detail", args=[cover["id"]])).content.decode()
        cards = _visible(body.split('id="cost"', 1)[1].split(">Shipments<", 1)[0])
        assert cards.lower().count("bundled in setup fee") == 1
        for row in ("Invoiced", "Billed", "Paid", "Safe to pay now", "Freight", "Import duty"):
            assert row not in cards, row
        assert "250 packet" in cards

    def test_a_covered_short_order_reads_short_covered_by_its_cover(self, scoped, da, world):
        short, _ = _short_and_covered(da, world)
        body = scoped.get(reverse("supply_chain:order_detail", args=[short["id"]])).content.decode()
        header = " ".join(_visible(_header(body)).split())
        assert "short — covered by SCHI-LP-0921" in header
        assert "part received" not in header

    def test_the_short_order_shows_one_fulfilment_view_that_adds_up(self, scoped, da, world):
        short, cover = _short_and_covered(da, world)
        body = scoped.get(reverse("supply_chain:order_detail", args=[short["id"]])).content.decode()
        section = body.split('id="fulfilment"', 1)[1].split("</section>", 1)[0]
        text = " ".join(_visible(section).split())
        assert "700 packets ordered" in text
        assert "450 packet" in text and "Main supplier" in text
        assert "250 packet" in text and "Local vendor" in text
        assert "700 packets received" in text
        assert reverse("supply_chain:order_detail", args=[short["id"]]) in section
        assert reverse("supply_chain:order_detail", args=[cover["id"]]) in section

    def test_an_order_nothing_covers_has_no_fulfilment_view(self, scoped, da, world):
        plain = _contract(da, world, world["main"], "10")
        body = scoped.get(reverse("supply_chain:order_detail", args=[plain["id"]])).content.decode()
        assert 'id="fulfilment"' not in body

    def test_a_bundled_order_needs_no_currency_or_landing_terms(self, scoped, da, world):
        from connect_labs.supply_chain.models import Commodity, Contract

        response = scoped.post(
            reverse("supply_chain:contract_create"),
            {
                "supplier": world["local"]["id"],
                "commodity": Commodity.objects.get(slug="iptsc").pk,
                "buyer_of_record": "partner_org",
                "buyer_org": world["partner"]["id"],
                "reference": "BUNDLED-1",
                "status": "placed",
                "consideration": "bundled",
                "quantity": "250",
                "quantity_unit": "packet",
                "source": "partner_reported",
            },
        )
        assert response.status_code == 302, response.content.decode()[:4000]
        assert Contract.objects.get(reference="BUNDLED-1").consideration == "bundled"

    def test_a_bought_order_still_needs_its_currency_and_landing_terms(self, scoped, da, world):
        from connect_labs.supply_chain.models import Commodity, Contract

        response = scoped.post(
            reverse("supply_chain:contract_create"),
            {
                "supplier": world["main"]["id"],
                "commodity": Commodity.objects.get(slug="iptsc").pk,
                "buyer_of_record": "programme_org",
                "buyer_org": world["us"]["id"],
                "reference": "PRICED-1",
                "status": "placed",
                "consideration": "priced",
                "quantity": "10",
                "quantity_unit": "packet",
                "source": "we_recorded",
            },
        )
        assert response.status_code == 200
        form = response.context["form"]
        assert {"currency", "freight_basis", "duties_basis", "vat_basis"} <= set(form.errors)
        assert not Contract.objects.filter(reference="PRICED-1").exists()
