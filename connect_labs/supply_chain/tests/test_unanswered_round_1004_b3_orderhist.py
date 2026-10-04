"""The unanswered round, DDD run 2026-10-04-001, batch 3: the order page and its History.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

H: "held" is said only of a document a recorded message set as a requirement
the shipment is held on -- with that message as its source. The duty exemption
a nil duty rests on is owed under the tender's duty terms, and says so as a
chip, not as "held until we provide it".

E: every email in a History reads in the one email-event shape, even when one
record came of it; why a held document is ours is a chip ("consignee: us"),
and the order's delivery term is a chip whose meaning is on hover.
"""

import html
import re

from django.urls import reverse

from connect_labs.supply_chain.fulfilment.services.holds import holds_on_us
from connect_labs.supply_chain.history.timeline import Entry, email_events
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.templatetags.supply_chain_extras import incoterm_name
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, op

da = reality.da
world = reality.world
client_in_program = reality.client_in_program

WAIVED = {"duties_basis": "excluded", "duties_amount": "0.00", "vat_basis": "included", "incoterm": "CPT Kano"}
FORWARDER = "Rehearsal Freight & Clearing"
HELD_EMAIL = "HELD AT BORDER - DOCUMENTATION (FORM M) / CONSIGNEE TO PROVIDE FORM M / PAAR."


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _order(client, contract_id):
    return client.get(reverse("supply_chain:order_detail", args=[contract_id])).content.decode()


def _held_by_the_forwarders_email(da, world):
    """An order costed at no duty on a relief, its shipment held on Form M and PAAR by an email."""
    contract = _contract(da, world, **WAIVED)
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "TRK-B3",
            "status": "dispatched",
            "dispatched_on": "2026-08-28",
            "expected_on": "2026-09-14",
            "source": "supplier_reported",
        },
    )
    op(
        da,
        "shipment_update",
        source={"ref": "<held-1@forwarder.example.invalid>", "sender": FORWARDER, "excerpt": HELD_EMAIL},
        shipment_id=shipment["id"],
        data={
            "status": "at_customs",
            "required_documents": [
                {"kind": "import_permit", "name": "Form M", "owed_by_org_id": world["us"]["id"]},
                {"kind": "customs_declaration", "name": "PAAR", "owed_by_org_id": world["us"]["id"]},
            ],
        },
    )
    return contract


# One owed-hold row: up to the next row, or the end of the owed list.
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


class TestOneShapeForEveryEmail:
    def test_a_lone_email_line_is_an_event_of_one_and_a_document_stays_a_line(self):
        email = Entry(
            when=None, sentence="x", actor="ACE (agent)", is_ai=True, excerpt="HELD", source_ref="<a@b.invalid>"
        )
        email.source_kind = "Email"
        document = Entry(when=None, sentence="y", actor="Sophie", is_ai=False, excerpt="p. 3", source_ref="scan-7")
        document.source_kind = "Document"
        folded = email_events([email, document])
        assert folded[0].is_group and folded[0].members == [email]
        assert folded[1] is document

    def test_the_forwarders_email_reads_as_an_event_on_the_order(self, da, world, client_in_program):
        contract = _held_by_the_forwarders_email(da, world)
        body = _order(client_in_program, contract["id"])
        history = body[body.index('id="history"') :]
        event = next(
            e for e in re.findall(r'<li data-testid="email-event".*?</ol>\s*</li>', history, re.S) if "Status" in e
        )
        head = re.search(
            r'data-testid="email-event-head"[^>]*>(.*?)</span>\s*<span data-testid="actor-badge"', event, re.S
        )
        assert html.unescape(_text(head.group(1))) == FORWARDER
        assert 'data-testid="source-toggle"' in event and "CONSIGNEE TO PROVIDE FORM M" in event
        # No "Email from ..., recorded by the AI assistant on ..." line: the head says it.
        assert "Email from" not in history and 'data-testid="source-heading"' not in history
        hold = re.search(r'data-testid="revision-hold"[^>]*>(.*?)</div>', event, re.S).group(1)
        assert _text(hold).startswith("Waiting on us: import permit (Form M) and customs declaration (PAAR)")
        reason = re.search(r'<span data-testid="hold-reason" class="([^"]*)" title="([^"]+)">([^<]*)<', hold)
        assert reason.group(3) == "consignee: us" and "status-chip" in reason.group(1).split()
        assert "we are the consignee" not in body


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
