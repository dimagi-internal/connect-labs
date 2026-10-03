"""DDD batch 5 of the unanswered round: the order page reads one value per column,
and its header status says where the goods are while they move."""

import re

from django.urls import reverse

from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, _held_on_our_form_m, op
from connect_labs.supply_chain.views import _order_status

# That module's fixtures, shared rather than copied.
da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _status(body):
    return _text(re.search(r'data-testid="order-status"[^>]*>(.*?)</span>', body, re.S).group(1))


class TestOrderStatus:
    def test_a_moving_shipment_leads_over_placed(self):
        shipments = [{"id": 1, "status": "dispatched", "dispatched_on": "2026-08-01"}]
        assert _order_status({"status": "placed"}, shipments, []) == {"label": "Dispatched", "tone": "info"}

    def test_a_hold_on_us_is_said_beside_it(self):
        shipments = [
            {"id": 1, "status": "delivered", "dispatched_on": "2026-07-01"},
            {"id": 2, "status": "at_customs", "dispatched_on": "2026-08-26"},
        ]
        status = _order_status({"status": "placed"}, shipments, [{"what": "Import permit"}])
        assert status == {"label": "At customs — held, waiting on us", "tone": "warning"}

    def test_without_a_moving_shipment_the_order_status_stands(self):
        assert _order_status({"status": "placed"}, [{"id": 1, "status": "planned"}], [])["label"] == "Placed"
        assert _order_status({"status": "received"}, [{"id": 1, "status": "at_customs"}], [])["tone"] == "done"
        assert _order_status({}, [], []) is None


class TestTheOrderPage:
    def test_the_header_says_held_at_customs_on_us(self, da, world, client_in_program):
        contract, _ = _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert _status(body) == "At customs — held, waiting on us"

    def test_the_invoice_columns_hold_one_kind_of_value(self, da, world, client_in_program):
        contract = _contract(da, world)
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "110350.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        assert ">Quantity billed</th>" in body
        row = re.search(r'<tr data-testid="invoice-above-agreed-row".*?</tr>', body, re.S).group(0)
        assert _text(row) == "Total · +USD 3,550.00 USD 110,350.00 billed 106,800.00 agreed above agreed"
        assert 'data-testid="above-agreed-tag"' in row
        # The invoice's own row carries the amount alone.
        invoice_row = re.search(r"<tr>\s*<td[^>]*>INV-REH-1</td>.*?</tr>", body, re.S).group(0)
        assert "above agreed" not in invoice_row
        # One size and weight down the Told by column.
        assert '<td class="px-4 py-2 text-sm text-gray-700">the supplier told us</td>' in invoice_row
