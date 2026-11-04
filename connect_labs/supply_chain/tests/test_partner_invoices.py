"""The partner's invoice to us (buying model b), tracked apart from the supplier's bills.

Sophie Feintuch's RUTF feedback, "Partner invoices to us are not tracked": when the partner pays the
supplier on our quote and invoices us, the delivery and payment of that reimbursement need a place,
and the supplier-bill checks and "Pay" must not mistake it for the supplier's bill.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented. Builds on rehearsal_world.
"""

import re

from django.urls import reverse

from connect_labs.supply_chain.fulfilment.services.match import three_way_match
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import _contract, op

da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


def _invoice(da, contract, **extra):
    return op(
        da,
        "invoice_record",
        data={
            "contract_id": contract["id"],
            "reference": "INV-1",
            "currency": "USD",
            "amount": "1000.00",
            "quantity_billed": 100,
            "quantity_unit": "carton",
            "source": "supplier_reported",
            **extra,
        },
    )


def test_an_invoice_is_the_suppliers_unless_it_says_otherwise(da, world):
    contract = _contract(da, world)
    assert _invoice(da, contract)["direction"] == "from_supplier"


def test_the_partners_invoice_is_not_a_second_bill_from_the_supplier(da, world):
    contract = _contract(da, world)
    _invoice(da, contract)
    before = three_way_match(Contract.objects.get(pk=contract["id"]))
    partner = _invoice(da, contract, reference="PARTNER-1", direction="from_partner", received_on="2026-11-01")
    assert partner["direction"] == "from_partner" and partner["received_on"] == "2026-11-01"
    after = three_way_match(Contract.objects.get(pk=contract["id"]))
    assert after["invoiced"] == before["invoiced"] and after["status"] == before["status"]


def test_paying_the_partner_is_not_paying_the_supplier(da, world):
    contract = _contract(da, world)
    partner = _invoice(da, contract, direction="from_partner")
    before = three_way_match(Contract.objects.get(pk=contract["id"]))
    op(
        da,
        "payment_record",
        data={"invoice_id": partner["id"], "paid_on": "2026-11-02", "amount": "1000.00", "source": "we_recorded"},
    )
    after = three_way_match(Contract.objects.get(pk=contract["id"]))
    assert after["paid_amount"] == before["paid_amount"]


def test_the_order_page_tracks_it_on_its_own_and_pay_names_the_partner(da, world, client_in_program):
    contract = _contract(da, world)
    op(da, "contract_update", contract_id=contract["id"], data={"buying_model": "partner_buys"})
    partner = _invoice(da, contract, reference="PARTNER-9", direction="from_partner", received_on="2026-11-01")
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", body))
    section = body[body.index('data-testid="partner-invoices"') :]
    assert "The partner's invoice to us" in text and "PARTNER-9" in section and "Pay the partner" in section
    pay = client_in_program.get(reverse("supply_chain:payment_record", args=[partner["id"]])).content.decode()
    assert "We pay the partner, not the supplier" in re.sub(r"\s+", " ", pay)


def test_the_record_link_opens_on_the_partner_as_biller(da, world, client_in_program):
    contract = _contract(da, world)
    page = client_in_program.get(reverse("supply_chain:invoice_record", args=[contract["id"]]) + "?from=partner")
    assert re.search(r'<option value="from_partner"\s+selected', page.content.decode())
