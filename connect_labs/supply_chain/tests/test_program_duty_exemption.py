"""The program's duty exemption: one paper, kept once, read by every record that rests on it.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

A tender costed on the waiver and an order whose nil duty rests on it used to
look for the exemption on their own records: attaching it to the tender left
the order owing it, and the two pages offered different controls for the same
paper. It is now a program-level document (no link) with the days it holds for,
attached with `document_attach`, and attaching it once clears both -- while it
is in force on the import's day.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from connect_labs.supply_chain import moves
from connect_labs.supply_chain.fulfilment.services import landed
from connect_labs.supply_chain.fulfilment.services.holds import holds_on_us
from connect_labs.supply_chain.history.models import OperationCall
from connect_labs.supply_chain.models import Contract, Document, Tender
from connect_labs.supply_chain.procurement import status
from connect_labs.supply_chain.procurement.services.pricing import program_duty_exemption
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, _contract, op
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _quote

da = reality.da
world = reality.world
client_in_program = reality.client_in_program

WAIVED = {"duties_basis": "excluded", "duties_amount": "0.00", "vat_basis": "included", "incoterm": "CPT Kano"}
TODAY = date.today()


def _waived_world(da, world):
    """A tender under the waiver with a quote costed on it, and an order at nil duty with a shipment to come."""
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="buyer_waiver")
    quote = _quote(da, world)
    contract = _contract(da, world, **WAIVED)
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "TRK-EX-1",
            "status": "dispatched",
            "carrier": "Rehearsal Freight & Clearing",
            "source": "supplier_reported",
        },
    )
    return quote, Contract.objects.get(pk=contract["id"]), shipment


def _attach_exemption(da, **window):
    return op(
        da,
        "document_attach",
        data={
            "kind": "duty_exemption",
            "title": "Duty exemption certificate",
            "external_url": "https://example.invalid/exemption.pdf",
            "source": "document",
            **{k: v.isoformat() for k, v in window.items()},
        },
    )


def _tender_gaps(world, quote):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    return status.quote_open_facts(tender, {}, quote, waiver_on_file=status.waiver_on_file(tender))


def _exemption_holds(contract):
    return [h for h in holds_on_us(contract) if h.kind == "duty_exemption"]


@pytest.mark.django_db
class TestOneAttachClearsBoth:
    def test_before_it_is_attached_both_the_tender_and_the_order_owe_it(self, da, world):
        quote, contract, _ = _waived_world(da, world)
        assert "duty exemption" in _tender_gaps(world, quote)
        assert len(_exemption_holds(contract)) == 1
        assert landed.relief_unevidenced(contract)

    def test_one_program_attach_clears_the_quote_gap_and_the_owed_document(self, da, world):
        quote, contract, _ = _waived_world(da, world)
        document = _attach_exemption(
            da, valid_from=TODAY - timedelta(days=30), valid_until=TODAY + timedelta(days=365)
        )
        # Kept once, linked to no record, through the attributed operation path.
        row = Document.objects.get(pk=document["id"])
        assert row.tender_id is None and row.contract_id is None and row.shipment_id is None
        assert OperationCall.objects.filter(program_id=PROGRAM, operation="document_attach").exists()
        assert "duty exemption" not in _tender_gaps(world, quote)
        assert _exemption_holds(contract) == []
        assert not landed.relief_unevidenced(contract)

    def test_the_pages_read_it_too(self, da, world, client_in_program):
        _waived_world(da, world)
        _attach_exemption(da)
        tender_id = world["tender"]["id"]
        comparison = client_in_program.get(
            reverse("supply_chain:procurement_comparison", args=[tender_id]) + "?commodity=rutf"
        ).content.decode()
        assert re.search(r'data-testid="waiver-evidence"[^>]*>[^<]*on file', comparison)
        assert "not on file" not in comparison
        assert 'data-testid="duty-exemption-attach"' not in comparison

    def test_one_attached_to_a_single_order_still_clears_that_order_only(self, da, world):
        quote, contract, shipment = _waived_world(da, world)
        op(
            da,
            "document_attach",
            data={
                "kind": "duty_exemption",
                "shipment_id": shipment["id"],
                "external_url": "https://example.invalid/one-order.pdf",
                "source": "document",
            },
        )
        assert _exemption_holds(contract) == []
        # The order's own paper is not the program's: the tender still owes it.
        assert "duty exemption" in _tender_gaps(world, quote)


@pytest.mark.django_db
class TestValidityDates:
    def test_an_expired_exemption_is_not_on_file(self, da, world):
        quote, contract, _ = _waived_world(da, world)
        _attach_exemption(da, valid_from=TODAY - timedelta(days=400), valid_until=TODAY - timedelta(days=1))
        assert program_duty_exemption(PROGRAM) is None
        assert "duty exemption" in _tender_gaps(world, quote)
        assert len(_exemption_holds(contract)) == 1

    def test_one_not_yet_in_force_is_not_on_file(self, da, world):
        quote, _, _ = _waived_world(da, world)
        _attach_exemption(da, valid_from=TODAY + timedelta(days=10))
        assert "duty exemption" in _tender_gaps(world, quote)

    def test_an_order_reads_it_on_the_day_its_goods_enter(self, da, world):
        _, contract, shipment = _waived_world(da, world)
        _attach_exemption(da, valid_from=TODAY - timedelta(days=5), valid_until=TODAY + timedelta(days=20))
        assert _exemption_holds(contract) == []
        # The shipment now enters after the exemption runs out: owed again.
        op(
            da,
            "shipment_update",
            shipment_id=shipment["id"],
            data={"expected_on": (TODAY + timedelta(days=40)).isoformat()},
        )
        assert len(_exemption_holds(contract)) == 1
        assert landed.relief_unevidenced(contract)

    def test_a_period_that_ends_before_it_starts_is_refused(self, da, world):
        with pytest.raises(ValueError, match="before valid_from"):
            _attach_exemption(da, valid_from=TODAY, valid_until=TODAY - timedelta(days=1))
        assert not Document.objects.filter(program_id=PROGRAM, kind="duty_exemption").exists()


@pytest.mark.django_db
class TestOneControl:
    def test_the_order_s_owed_exemption_is_attached_at_program_level(self, da, world, client_in_program):
        _, contract, _ = _waived_world(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract.pk])).content.decode()
        link = re.search(r'<a data-testid="owed-hold-provide"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', body)
        assert link.group(2) == "Attach"
        assert link.group(1).startswith(reverse("supply_chain:program_document_attach") + "?kind=duty_exemption")
        assert "Mark provided" not in body

    def test_the_overview_move_says_attach(self, da, world):
        _, contract, _ = _waived_world(da, world)
        ours, _ = moves.contract_moves(contract, TODAY)
        assert [m.cta for m in ours] == ["Attach"]

    def test_the_tender_and_comparison_attach_to_the_program(self, da, world, client_in_program):
        _waived_world(da, world)
        tender_id = world["tender"]["id"]
        for name in ("procurement_tender_detail", "procurement_comparison"):
            body = client_in_program.get(reverse(f"supply_chain:{name}", args=[tender_id])).content.decode()
            link = re.search(r'data-testid="duty-exemption-attach"[^>]*href="([^"]*)"[^>]*>([^<]*)</a>', body)
            assert link, name
            assert link.group(2) == "Attach"
            assert link.group(1).startswith(reverse("supply_chain:program_document_attach") + "?kind=duty_exemption")

    def test_the_program_attach_screen_takes_the_dates_and_writes_through_the_operation(
        self, da, world, client_in_program, monkeypatch
    ):
        # The screen writes as the program's caller, like the other pages under this fixture.
        monkeypatch.setattr("connect_labs.supply_chain.form_views._access", lambda request: da)
        quote, contract, _ = _waived_world(da, world)
        back = reverse("supply_chain:order_detail", args=[contract.pk])
        url = reverse("supply_chain:program_document_attach") + f"?kind=duty_exemption&next={back}"
        page = client_in_program.get(url).content.decode()
        assert re.search(r'<option value="duty_exemption"[^>]*selected', page)
        assert 'name="valid_from"' in page and 'name="valid_until"' in page
        response = client_in_program.post(
            url,
            {
                "kind": "duty_exemption",
                "title": "Exemption",
                "external_url": "https://example.invalid/exemption.pdf",
                "source": "document",
                "valid_from": (TODAY - timedelta(days=1)).isoformat(),
                "valid_until": (TODAY + timedelta(days=90)).isoformat(),
            },
        )
        assert response.status_code == 302, response.context["form"].errors
        assert response["Location"] == back
        row = Document.objects.get(program_id=PROGRAM, kind="duty_exemption")
        assert row.valid_until == TODAY + timedelta(days=90) and row.tender_id is None
        assert "duty exemption" not in _tender_gaps(world, quote)
        assert _exemption_holds(contract) == []

    def test_a_tender_link_asking_for_the_exemption_goes_to_the_program_screen(self, world, client_in_program):
        url = reverse("supply_chain:tender_document_attach", args=[world["tender"]["id"]]) + "?kind=duty_exemption"
        response = client_in_program.get(url)
        assert response.status_code == 302
        assert response["Location"].startswith(reverse("supply_chain:program_document_attach"))
