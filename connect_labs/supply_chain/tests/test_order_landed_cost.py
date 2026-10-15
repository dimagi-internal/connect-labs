"""An order's landed cost: clearing & forwarding as the comparison costs it, a total resting on
an unevidenced relief marked unconfirmed, and the delivery term as a chip.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import re
from decimal import Decimal

import pytest
from django.urls import reverse

from connect_labs.supply_chain.fulfilment.services import landed
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.procurement.services import pricing
from connect_labs.supply_chain.templatetags.supply_chain_extras import incoterm_name
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import (
    WAIVED,
    _attach,
    _contract,
    _estimate,
    _order,
    _shipment,
    _text,
    op,
)

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


class TestClearingOnTheLandedCost:
    def test_the_tenders_estimate_times_the_quantity_is_added_and_marked_calculated(
        self, da, world, client_in_program
    ):
        _estimate(da, world)
        contract = _contract(da, world, **WAIVED)
        costed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert costed["clearing"]["source"] == "estimate"
        assert Decimal(costed["clearing"]["amount"]["amount"]) == Decimal("2400")
        # Goods 99,600 + freight 7,200 + clearing 2,400.
        assert Decimal(costed["landed_total"]["amount"]) == Decimal("109200")
        row = re.search(
            r'<tr data-testid="landed-clearing" data-source="estimate">.*?</tr>',
            _order(client_in_program, contract["id"]),
            re.S,
        )
        assert row is not None
        assert 'data-src="calc"' in row.group(0)
        assert "Clearing &amp; forwarding" in row.group(0)
        assert "2,400.00" in _text(row.group(0))

    def test_with_no_estimate_it_reads_not_recorded_and_the_total_is_marked(self, da, world, client_in_program):
        contract = _contract(da, world, **WAIVED)
        costed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert costed["clearing"]["source"] == "not_recorded"
        assert Decimal(costed["landed_total"]["amount"]) == Decimal("106800")
        body = _order(client_in_program, contract["id"])
        row = re.search(r'<tr data-testid="landed-clearing" data-source="not_recorded">.*?</tr>', body, re.S)
        assert row is not None and 'status-chip--ours">Not recorded<' in row.group(0)
        assert 'data-testid="landed-excl-clearing"' in body

    def test_a_paid_clearing_charge_is_the_line_and_is_counted_once(self, da, world, client_in_program):
        _estimate(da, world)
        contract = _contract(da, world, **WAIVED)
        shipment = _shipment(da, contract, world, required=False)
        agent = op(da, "org_upsert", data={"slug": "rehearsal-agent", "name": "Rehearsal Clearing Agent"})
        op(
            da,
            "charge_record",
            data={
                "shipment_id": shipment["id"],
                "kind": "clearing",
                "payee_org_id": agent["id"],
                "amount": "2000.00",
                "currency": "USD",
                "paid_on": "2026-09-20",
                "source": "we_recorded",
            },
        )
        costed = op(da, "contract_landed_cost", contract_id=contract["id"])
        assert costed["clearing"]["source"] == "paid"
        # The paid charge, not the estimate, and not both.
        assert Decimal(costed["landed_total"]["amount"]) == Decimal("108800")
        body = _order(client_in_program, contract["id"])
        assert 'data-source="paid"' in body
        # The charge is the clearing line, not a second row beside it.
        assert "to Rehearsal Clearing Agent" not in body

    def test_not_ours_when_the_supplier_imports_or_an_agency_buys(self, da, world):
        _estimate(da, world)
        ddp = _contract(da, world, incoterm="DDP Kano", duties_basis="included", vat_basis="included")
        assert op(da, "contract_landed_cost", contract_id=ddp["id"])["clearing"] is None
        agency = _contract(da, world, buyer_of_record="agency", reference="PO-REH-2", **WAIVED)
        assert op(da, "contract_landed_cost", contract_id=agency["id"])["clearing"] is None


class TestATotalOnAnUnevidencedRelief:
    def test_is_marked_unconfirmed_until_the_exemption_is_on_file(self, da, world, client_in_program):
        contract = _contract(da, world, **WAIVED)
        assert 'data-testid="landed-unconfirmed"' in _order(client_in_program, contract["id"])
        shipment = _shipment(da, contract, world, required=False)
        _attach(da, shipment, "duty_exemption")
        assert 'data-testid="landed-unconfirmed"' not in _order(client_in_program, contract["id"])

    def test_duty_inside_the_price_is_no_relief(self, da, world, client_in_program):
        contract = _contract(da, world, duties_basis="included", vat_basis="included")
        assert not landed.rests_on_relief(Contract.objects.get(pk=contract["id"]))
        assert 'data-testid="landed-unconfirmed"' not in _order(client_in_program, contract["id"])


class TestDutyRelief:

    def test_the_rule_is_the_costing_s_own(self, da, world):
        """The order reads the one predicate (pricing.relief_unevidenced) off contract_landed_cost."""
        from connect_labs.supply_chain.fulfilment.services import landed
        from connect_labs.supply_chain.models import Contract

        contract = _contract(da, world, duties_basis="excluded", duties_amount="0.00")
        assert landed.relief_unevidenced(Contract.objects.get(pk=contract["id"]))
        included = _contract(da, world, duties_basis="included")
        assert not landed.relief_unevidenced(Contract.objects.get(pk=included["id"]))


@pytest.mark.django_db
def test_the_order_s_nil_duty_is_the_figure_and_the_comparison_s_chip(da, world, client_in_program):
    contract = _contract(da, world, duties_basis="excluded", duties_amount="0.00")
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    cell = re.search(r'data-testid="duty-relief-unevidenced"[^>]*>(.*?)</span>\s*</td>', body, re.S).group(1)
    assert _text(cell) == "USD 0.00 (waiver) duty exemption not on file"
    assert 'data-testid="waiver-pending"' in cell
    assert "duty relief is documented" not in body and "no document on file" not in body


def test_the_order_reads_the_same_predicate(monkeypatch):
    """landed.relief_unevidenced is pricing.relief_unevidenced over the order's own relief."""
    from types import SimpleNamespace

    from connect_labs.supply_chain.fulfilment.services import landed

    seen = []
    monkeypatch.setattr(pricing, "relief_unevidenced", lambda rests, **kw: seen.append((rests, kw)) or "x")
    contract = SimpleNamespace(
        consideration="priced",
        buyer_of_record="programme_org",
        duties_basis="excluded",
        duties_amount=0,
        duty_relief_claimed=False,
    )
    assert landed.relief_unevidenced(contract) == "x"
    assert seen == [(True, {"contract": contract})]


class TestTheDeliveryTermIsAChip:
    def test_the_term_is_a_chip_with_its_meaning_on_hover(self, da, world, client_in_program):
        contract = _contract(da, world, **WAIVED)
        body = _order(client_in_program, contract["id"])
        term = re.search(r"<dt[^>]*>Delivery term</dt><dd[^>]*>(.*?)</dd>", body, re.S).group(1)
        chip = re.search(
            r'data-testid="order-incoterm" class="status-chip status-chip--neutral" title="([^"]+)">([^<]+)<', term
        )
        assert chip.group(2) == "CPT Kano"
        assert "the supplier pays freight to the named place" in chip.group(1)
        assert _text(term) == "CPT Kano carriage paid to"

    def test_the_name_alone_and_nothing_for_an_unknown_term(self):
        assert incoterm_name("DAP Kano") == "delivered at place"
        assert incoterm_name("XYZ") == ""
        assert incoterm_name("") == ""
