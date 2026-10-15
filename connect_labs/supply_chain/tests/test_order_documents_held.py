"""Documents we owe on an order: held only where a message held the shipment, owed under the
duty terms otherwise, each with its own Attach and the message behind it.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import re
from datetime import date

from django.urls import reverse

from connect_labs.supply_chain import moves
from connect_labs.supply_chain.fulfilment.services.holds import Hold, holds_on_us
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import (
    FORWARDER,
    HELD_EMAIL,
    WAIVED,
    _attach,
    _contract,
    _held_by_the_forwarders_email,
    _held_on_our_form_m,
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


def test_a_document_hold_is_listed_as_something_we_owe(da, world, client_in_program):
    contract, _ = _held_on_our_form_m(da, world)
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    owed = re.search(r'data-testid="owed">(.*?)</div>\s*(?:<details|<div class="mb)', body, re.S).group(1)
    hold = _text(re.search(r'data-testid="owed-hold"[^>]*>(.*?)</div>', owed, re.S).group(1))
    # Since DDD 003 batch 8 the hold carries the action that clears it.
    # Since unanswered round 1004 b3 what it rests on is a chip: a recorded requirement holds the shipment.
    assert hold == "Import permit shipment held Attach"
    assert "?kind=import_permit" in owed
    assert "Nothing owed" not in owed
    assert _text(re.search(r'<h2 id="owed".*?</h2>', body, re.S).group(0)) == (
        # Since DDD 005 batch 3 the section names who the document goes through.
        "Documents to clear the shipment (via Crescent Rehearsal Freight) — 1 open"
    )
    ours = body.split('data-testid="on-us"', 1)[1].split("</section>", 1)[0]
    assert 'data-rule="owed"' in ours and "Provide 1 document to Crescent Rehearsal Freight" in _text(ours)


_ROW = (
    r'<div data-testid="owed-hold" data-basis="(\w*)"[^>]*>(.*?)'
    r'(?=<div data-testid="owed-hold"|<p |</div>\s*</div>\s*(?:<details|<div class="mb))'
)


def _owed_rows(body):
    return {
        _text(re.search(r"<span[^>]*>(.*?)</span>", row, re.S).group(1)): (basis, row)
        for basis, row in re.findall(_ROW, body, re.S)
    }


class TestHeldOnlyWhereAMessageHeldIt:
    def test_holds_say_what_they_rest_on_and_the_message_behind_a_held_one(self, da, world):
        contract = _held_by_the_forwarders_email(da, world)
        holds = {h.kind: h for h in holds_on_us(Contract.objects.get(pk=contract["id"]))}
        assert set(holds) == {"import_permit", "customs_declaration", "duty_exemption"}
        for kind in ("import_permit", "customs_declaration"):
            assert holds[kind].basis == "held"
            assert holds[kind].source_excerpt == HELD_EMAIL
            assert holds[kind].source_kind == "Email"
            assert holds[kind].asked_by == FORWARDER
            assert holds[kind].source_on.isoformat() == "2026-10-01"
        relief = holds["duty_exemption"]
        assert relief.basis == "duty_terms" and relief.source_excerpt == ""
        assert holds["import_permit"].as_dict()["basis"] == "held"
        assert relief.as_dict()["source_on"] is None

    def test_the_order_marks_held_and_owed_apart(self, da, world, client_in_program):
        contract = _held_by_the_forwarders_email(da, world)
        body = _order(client_in_program, contract["id"])
        assert "held until" not in body
        rows = _owed_rows(body)
        assert {name: basis for name, (basis, _) in rows.items()} == {
            "Import permit (Form M)": "held",
            "Customs declaration (PAAR)": "held",
            "Duty exemption": "duty_terms",
        }
        for name in ("Import permit (Form M)", "Customs declaration (PAAR)"):
            row = rows[name][1]
            assert re.search(
                r'data-testid="owed-hold-basis" class="status-chip status-chip--ours">shipment held<', row
            )
            # The message that holds it opens from its own Source toggle, sender and day beside it.
            source = re.search(r'<details[^>]*data-testid="owed-hold-source">.*?</details>', row, re.S).group(0)
            assert "Source email" in source
            assert re.search(r'data-testid="owed-hold-sender"[^>]*>Rehearsal Freight &amp; Clearing<', source)
            assert "1 Oct 2026" in source
            assert "CONSIGNEE TO PROVIDE FORM M" in source
        relief = rows["Duty exemption"][1]
        chip = re.search(r'<span data-testid="owed-hold-basis"[^>]*title="([^"]+)"[^>]*>([^<]*)<', relief)
        assert chip.group(2) == "owed · duty terms" and chip.group(1)
        assert "shipment held" not in relief and "owed-hold-source" not in relief
        # Every owed document still counts: three open, three to provide.
        assert body.count('data-testid="owed-hold-provide"') == 3
        assert re.search(r'data-testid="owed-open-count"[^>]*>— 3 open<', body)


def test_the_on_us_item_splits_held_documents_from_those_owed_under_duty_terms():
    import datetime as dt

    from connect_labs.supply_chain.fulfilment.services.holds import Hold
    from connect_labs.supply_chain.moves import owed_moves

    day = dt.date(2026, 9, 12)
    holds = [
        Hold("customs declaration", day, shipment_id=1, name="PAAR", asked_by="Forwarder", basis="held"),
        Hold("duty exemption", day, shipment_id=1, asked_by="Forwarder", basis="duty_terms"),
        Hold("import permit", day, shipment_id=1, name="Form M", asked_by="Forwarder", basis="held"),
    ]
    (move,) = owed_moves([], holds, contract_id=7)
    assert move.text == "Provide 3 documents to Forwarder"
    assert move.detail == "shipment held: PAAR, Form M · owed under duty terms: duty exemption"


def test_an_item_with_only_held_documents_names_no_duty_terms():
    import datetime as dt

    from connect_labs.supply_chain.fulfilment.services.holds import Hold
    from connect_labs.supply_chain.moves import owed_moves

    holds = [Hold("import permit", dt.date(2026, 9, 12), shipment_id=1, name="Form M", asked_by="F", basis="held")]
    (move,) = owed_moves([], holds, contract_id=7)
    assert move.detail == "shipment held: Form M"


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


class TestOwedNamesItsCounterparty:
    def test_a_held_document_says_who_asked_for_it(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        (hold,) = holds_on_us(Contract.objects.get(pk=contract["id"]))
        assert hold.asked_by == "Crescent Rehearsal Freight"
        # Who asked is not who it is owed to: the overview's words are unchanged.
        assert hold.words == "import permit"


def test_a_held_document_carries_its_kind():
    hold = Hold(what="customs declaration", since=None, shipment_id=4, name="PAAR", kind="customs_declaration")
    assert hold.as_dict()["kind"] == "customs_declaration"
