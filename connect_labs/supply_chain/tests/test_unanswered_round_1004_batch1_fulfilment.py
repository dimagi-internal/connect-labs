"""The unanswered round, DDD run 2026-10-04-001, batch 1: the order page.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

F4: an order we import carries clearing & forwarding on its landed cost, the
way the comparison costs the quote it came from -- paid, else the tender's
estimate (calculated), else "Not recorded" -- and a total resting on a relief
nobody has evidenced is marked unconfirmed.

F5: the duty exemption a nil duty rests on is a document we owe while the
goods are still to arrive: it is held, counted On us, and has its own Attach
(at program level, test_program_duty_exemption).
"""

import re
from datetime import date
from decimal import Decimal

from django.urls import reverse

from connect_labs.supply_chain import moves
from connect_labs.supply_chain.fulfilment.services import landed
from connect_labs.supply_chain.fulfilment.services.holds import holds_on_us
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, op

da = reality.da
world = reality.world
client_in_program = reality.client_in_program

WAIVED = {"duties_basis": "excluded", "duties_amount": "0.00", "vat_basis": "included", "incoterm": "CPT Kano"}


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _order(client, contract_id):
    return client.get(reverse("supply_chain:order_detail", args=[contract_id])).content.decode()


def _estimate(da, world, per_unit="1.20"):
    op(da, "tender_set_import_estimates", tender_id=world["tender"]["id"], clearing_estimate_per_unit=per_unit)


def _shipment(da, contract, world, *, status="at_customs", required=True):
    return op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "TRK-1",
            "status": status,
            "carrier": "Rehearsal Freight & Clearing",
            "source": "supplier_reported",
            "required_documents": (
                [
                    {"kind": "import_permit", "name": "Form M", "owed_by_org_id": world["us"]["id"]},
                    {"kind": "customs_declaration", "name": "PAAR", "owed_by_org_id": world["us"]["id"]},
                ]
                if required
                else []
            ),
        },
    )


def _attach(da, shipment, kind):
    return op(
        da,
        "document_attach",
        data={
            "kind": kind,
            "shipment_id": shipment["id"],
            "external_url": "https://example.invalid/doc.pdf",
            "source": "document",
        },
    )


# ---- F4: clearing & forwarding on the order's landed cost


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


# ---- F5: the relief document is owed while the goods are to arrive


class TestTheReliefDocumentIsOwed:
    def test_it_is_held_beside_the_shipments_own_documents(self, da, world, client_in_program):
        contract = _contract(da, world, **WAIVED)
        shipment = _shipment(da, contract, world)
        record = Contract.objects.get(pk=contract["id"])
        kinds = [h.kind for h in holds_on_us(record)]
        assert sorted(kinds) == ["customs_declaration", "duty_exemption", "import_permit"]
        ours, _ = moves.contract_moves(record, date(2026, 10, 4))
        assert [m.text for m in ours] == ["Provide 3 documents to Rehearsal Freight & Clearing"]
        body = _order(client_in_program, contract["id"])
        assert body.count('data-testid="owed-hold"') == 3
        # One Attach per document: the shipment's own to the shipment, the duty exemption to the program.
        provide = re.findall(r'data-testid="owed-hold-provide"[^>]*href="([^"]*)"', body)
        assert len(provide) == 3
        assert any(href.startswith("/supply/documents/new/?kind=duty_exemption") for href in provide)
        assert re.search(r'data-testid="owed-open-count"[^>]*>— 3 open<', body)
        _attach(da, shipment, "duty_exemption")
        assert "duty_exemption" not in [h.kind for h in holds_on_us(record)]

    def test_not_once_the_goods_are_received_or_when_duty_is_priced(self, da, world):
        priced = _contract(da, world, duties_basis="included", vat_basis="included")
        _shipment(da, priced, world, required=False, status="dispatched")
        assert holds_on_us(Contract.objects.get(pk=priced["id"])) == []
        delivered = _contract(da, world, reference="PO-REH-3", **WAIVED)
        _shipment(da, delivered, world, required=False, status="delivered")
        assert holds_on_us(Contract.objects.get(pk=delivered["id"])) == []

    def test_a_partners_relief_is_the_partners_to_answer(self, da, world):
        partner = op(da, "org_upsert", data={"slug": "rehearsal-partner", "name": "Rehearsal Partner"})
        contract = _contract(
            da, world, buyer_of_record="partner_org", buyer_org_id=partner["id"], duty_relief_claimed=True, **WAIVED
        )
        _shipment(da, contract, world, required=False, status="dispatched")
        assert holds_on_us(Contract.objects.get(pk=contract["id"])) == []
