"""An order's invoices: the advance as a row, variance against agreed in columns, disputes, part paid.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import Invoice
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import _contract, _held_on_our_form_m, _overbilled, _text, op
from connect_labs.supply_chain.views import _mark_invoices, _order_status

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


class TestOrderInvoices:
    def test_an_advance_is_its_own_invoice_table_row(self, da, world, client_in_program):
        contract = _contract(da, world, payment_terms="advance")
        paid = op(
            da,
            "payment_record",
            channel="web",
            data={
                "contract_id": contract["id"],
                "paid_on": "2026-07-28",
                "amount": "53400.00",
                "source": "we_recorded",
            },
        )
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
                "acknowledges_payment_ids": [paid["id"]],
                "source": "supplier_reported",
            },
        )
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body))
        # Its own row of the invoices table, one value to a column.
        row = re.search(r'<tr data-testid="advance-row">(.*?)</tr>', body, re.S).group(1)
        cells = [_text(c) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)]
        assert cells[:5] == ["Advance paid", "28 Jul 2026", "USD 53,400.00", "—", "applied to INV-REH-1"]
        assert "confirmed by the payee 21 Sep 2026" in text
        # Not repeated as a footnote under the invoice. (The above-agreed row is
        # TestTheOrderHeaderAndInvoiceColumns'.)
        assert "(before this invoice)" not in body


def test_the_unit_price_against_agreed_has_its_own_row(da, world, client_in_program):
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
            "unit_price": "51.20",
            "quantity_billed": 2000,
            "quantity_unit": "carton",
            "source": "supplier_reported",
        },
    )
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    sub = re.search(r'<tr data-testid="invoice-against-agreed".*?</tr>', body, re.S).group(0)
    assert "Unit price per carton USD 51.20 USD 49.80 +USD 1.40" in _text(sub)
    assert 'data-testid="above-agreed-tag"' in sub
    amount_cell = re.search(r'data-testid="invoice-above-agreed"[^>]*>(.*?)</td>', body, re.S).group(1)
    assert "unit price" not in amount_cell


def test_the_chain_counts_part_paid_apart():
    from connect_labs.supply_chain.templatetags.supply_chain_extras import _invoiced_note

    assert _invoiced_note({"count": 1, "unpaid": 0, "part_paid": 1}) == "1 part paid"
    assert _invoiced_note({"count": 3, "unpaid": 2, "part_paid": 1}) == "2 unpaid · 1 part paid"


def test_the_summary_does_not_count_a_part_paid_invoice_as_unpaid(da, world):
    from connect_labs.supply_chain.models import Invoice
    from connect_labs.supply_chain.summary import _order

    contract = _contract(da, world)
    invoice = op(
        da,
        "invoice_record",
        data={"contract_id": contract["id"], "reference": "INV-1", "amount": "100.00", "source": "supplier_reported"},
    )
    Invoice.objects.filter(pk=invoice["id"]).update(status="part_paid")
    invoiced = _order(da)["invoiced"]
    assert (invoiced["unpaid"], invoiced["part_paid"]) == (0, 1)


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


class TestTheOrderHeaderAndInvoiceColumns:
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
        # Billed, agreed and the difference each in a column of their own, under a header row.
        assert _text(row) == "Total USD 110,350.00 USD 106,800.00 +USD 3,550.00 above agreed"
        # A small table of its own, indented under the invoice, with its own column heads.
        head = re.search(r'<thead data-testid="invoice-variance-head".*?</thead>', body, re.S).group(0)
        assert _text(head) == "Against agreed Against agreed Billed Agreed Difference Flag"
        assert 'data-testid="above-agreed-tag"' in row
        # The invoice's own row carries the amount alone.
        invoice_row = re.search(r"<tr>\s*<td[^>]*>INV-REH-1</td>.*?</tr>", body, re.S).group(0)
        assert "above agreed" not in invoice_row
        assert "the supplier told us" in invoice_row


@pytest.mark.django_db
class TestDisputingAnInvoice:
    def test_the_dispute_marks_it_queried_with_a_dated_reason_and_a_revision(self, da, world):
        _, invoice = _overbilled(da, world)
        out = op(
            da,
            "invoice_dispute",
            invoice_id=invoice["id"],
            reason="Unit price 51.20 against 49.80 agreed.",
            disputed_on="2026-10-03",
        )
        assert out["status"] == "queried"
        stored = Invoice.objects.get(pk=invoice["id"])
        assert stored.note == "Disputed 3 Oct 2026: Unit price 51.20 against 49.80 agreed."
        revisions = Revision.objects.filter(object_id=str(invoice["id"]), changes__has_key="status")
        assert revisions.exists()

    def test_a_paid_invoice_cannot_be_disputed(self, da, world):
        _, invoice = _overbilled(da, world)
        Invoice.objects.filter(pk=invoice["id"]).update(status="paid")
        with pytest.raises(ValueError):
            op(da, "invoice_dispute", invoice_id=invoice["id"], reason="Too much.")

    def test_the_order_offers_dispute_beside_pay_and_then_reads_disputed(self, da, world, client_in_program):
        contract, invoice = _overbilled(da, world)
        page = reverse("supply_chain:order_detail", args=[contract["id"]])
        body = client_in_program.get(page).content.decode()
        assert 'data-testid="invoice-dispute"' in body
        assert reverse("supply_chain:invoice_dispute", args=[invoice["id"]]) in body
        form = client_in_program.get(reverse("supply_chain:invoice_dispute", args=[invoice["id"]]))
        assert form.status_code == 200
        assert "51.20 against 49.80 agreed" in form.content.decode()
        posted = client_in_program.post(
            reverse("supply_chain:invoice_dispute", args=[invoice["id"]]),
            {"reason": "Freight above the agreed 7,200.00.", "disputed_on": "2026-10-03"},
        )
        assert posted.status_code == 302
        body = client_in_program.get(page).content.decode()
        assert 'data-testid="invoice-disputed"' in body
        assert 'data-testid="invoice-dispute"' not in body
        assert "Freight above the agreed 7,200.00." in body

    def test_each_variance_line_carries_its_currency_and_difference(self, da, world, client_in_program):
        contract, _ = _overbilled(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        rows = [_text(r) for r in re.findall(r'data-testid="invoice-against-agreed".*?</tr>', body, re.S)]
        assert any("USD 51.20 USD 49.80 +USD 1.40" in r for r in rows)
        assert any("USD 7,950.00 USD 7,200.00 +USD 750.00" in r for r in rows)


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


@pytest.mark.django_db
def test_variance_rows_read_in_order_and_the_total_is_tagged(da, world, client_in_program):
    contract, _ = _overbilled(da, world)
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    table = re.search(r'<table data-testid="invoice-variance-table".*?</table>', body, re.S).group(0)
    rows = [
        _text(r)
        for r in re.findall(r"<tr data-testid=\"invoice-(?:above-agreed-row|against-agreed)\".*?</tr>", table, re.S)
    ]
    assert [r.split()[0] for r in rows] == ["Unit", "Freight", "Total"]
    assert all(r.endswith("above agreed") for r in rows)
