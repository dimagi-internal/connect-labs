"""DDD 003 batch 4: the overview reads where an order's goods are as the order page does,
an ordered award is no longer provisional, and the invoice's figures each sit in a column."""

import re

from django.urls import reverse

from connect_labs.supply_chain import records
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _held_on_our_form_m
from connect_labs.supply_chain.views import _mark_invoices

# Those modules' fixtures, shared rather than copied.
da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


class TestWhereaboutsIsOneHelper:
    def test_the_newest_moving_shipment_and_its_words(self):
        shipments = [
            {"id": 1, "status": "delivered", "dispatched_on": "2026-07-01"},
            {"id": 2, "status": "at_customs", "dispatched_on": "2026-08-26"},
        ]
        assert records.latest_moving_shipment(shipments)["id"] == 2
        assert records.latest_moving_shipment([{"id": 1, "status": "planned"}]) is None
        assert records.shipment_whereabouts("at_customs", True) == "at customs — held, waiting on us"
        assert records.shipment_whereabouts("at_customs") == "at customs"


class TestTheOverviewAgreesWithTheOrderPage:
    def test_the_order_row_says_held_at_customs_on_us(self, da, world):
        _held_on_our_form_m(da, world)
        row = next(r for r in standing_rows(reality.PROGRAM, reality.TODAY) if r.kind == "order")
        # The stage is the fact; Waiting on says the move is ours, so the stage does not repeat it.
        assert row.stage.endswith("at customs, held")
        assert "waiting on us" not in row.stage
        assert "in transit" not in row.stage

    def test_the_chain_s_dispatched_tile_says_so_too(self, da, world, client_in_program):
        _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        assert "1 at customs — held, waiting on us · not yet stock on hand" in _text(body)
        assert "in transit — not yet stock on hand" not in _text(body)


class TestTheUnitPriceSaysPerWhat:
    def test_the_label_names_the_unit(self):
        invoices = [{"id": 7}]
        facts = {
            "currency": "USD",
            "above": [{"field": "unit_price", "invoice_id": 7, "billed": "51.20", "agreed": "49.80", "per": "carton"}],
        }
        _mark_invoices(invoices, {"facts": facts})
        mark = invoices[0]["above_agreed"][0]
        assert mark["label"] == "Unit price per carton"
        assert (mark["billed"], mark["agreed"], mark["difference"]) == ("51.20", "49.80", "1.40")
