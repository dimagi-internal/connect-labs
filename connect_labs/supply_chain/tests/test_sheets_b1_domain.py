"""The sheets walkthrough, judged: three domain fixes.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

1. A corrected Received day moves the reply it arrived as (one email, one day).
2. A supplier whose questions we answered is waited on for its quote (rule e),
   not dropped from both lists.
3. A shipment's Told by is its latest change's teller and day; the order's owed
   documents say how many of them hold the shipment.

Builds on test_tracking_reality's world: one open RUTF tender, Kanem invited on
6 Jul, Northgate a second supplier.
"""

import datetime
import re

from django.urls import reverse

from connect_labs.supply_chain import cells
from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.history.context import seed_overrides
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.history.timeline import timeline_for_tender
from connect_labs.supply_chain.models import Outreach, Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.procurement.status import tender_status
from connect_labs.supply_chain.standing import move_counts, standing_rows
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, TODAY, _contract, _kanem_quote, op

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _tender(world):
    return Tender.objects.get(pk=world["tender"]["id"])


# ---- 1. one email, one day ------------------------------------------------------


def _kanem_replied_with_a_quote(da, world, *, replied_on, received_on):
    quote = op(da, "quote_record", data=_kanem_quote(world, received_on=received_on))
    op(
        da,
        "outreach_update",
        outreach_id=world["outreach"]["id"],
        data={"responded": True, "response_kind": "quote", "responded_on": replied_on},
    )
    return quote


class TestACorrectedReceivedDayMovesTheReply:
    def test_the_reply_day_follows_the_quote_s_corrected_received_day(self, da, world):
        quote = _kanem_replied_with_a_quote(da, world, replied_on="2026-10-01", received_on="2026-10-01")

        result = cells.apply(da, "quote", quote["id"], "received_on", "30 Sep 2026", was="1 Oct 2026")

        reply = Outreach.objects.get(pk=world["outreach"]["id"])
        assert reply.responded_on == datetime.date(2026, 9, 30)
        # Recorded as its own change, in the same call as the correction, so the history keeps both days.
        outreach_change = Revision.objects.filter(
            object_id=str(reply.pk), action="update", changes__has_key="responded_on"
        ).latest("recorded_at", "id")
        assert outreach_change.changes["responded_on"] == ["2026-10-01", "2026-09-30"]
        new_version = int(result["key"].split(":")[1])
        assert Revision.objects.filter(
            call_id=outreach_change.call_id, action="create", object_id=str(new_version)
        ).exists()
        assert outreach_change.call.operation == "quote_correct"

    def test_the_history_says_it_once_on_the_correction_s_line(self, da, world):
        quote = _kanem_replied_with_a_quote(da, world, replied_on="2026-10-01", received_on="2026-10-01")
        cells.apply(da, "quote", quote["id"], "received_on", "30 Sep 2026", was="1 Oct 2026")

        lines = [e.sentence for e in timeline_for_tender(world["tender"]["id"], program_id=PROGRAM)]
        assert "Quote corrected: received 1 Oct → 30 Sep — reply 1 Oct → 30 Sep" in lines
        assert not [line for line in lines if line.startswith("Responded on")]
        # The reply as first recorded stays in the history.
        assert "Replied with a quote" in lines

    def test_a_reply_on_another_day_is_another_email_and_stays(self, da, world):
        quote = _kanem_replied_with_a_quote(da, world, replied_on="2026-09-28", received_on="2026-10-01")

        cells.apply(da, "quote", quote["id"], "received_on", "30 Sep 2026", was="1 Oct 2026")

        assert Outreach.objects.get(pk=world["outreach"]["id"]).responded_on == datetime.date(2026, 9, 28)

    def test_the_tender_page_s_replied_cell_reads_the_corrected_day(self, da, world, client_in_program):
        quote = _kanem_replied_with_a_quote(da, world, replied_on="2026-10-01", received_on="2026-10-01")
        cells.apply(da, "quote", quote["id"], "received_on", "30 Sep 2026", was="1 Oct 2026")

        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        ).content.decode()
        row = re.search(rf'<tr data-outreach-id="{world["outreach"]["id"]}".*?</tr>', body, re.S).group(0)
        assert re.search(r'data-testid="replied">(.*?)</span>', row).group(1) == "30 Sep"


# ---- 2. answered, awaiting a quote ------------------------------------------------


def _northgate_asked_questions(da, world):
    """Northgate is invited on 16 Sep and replies on 18 Sep with a question instead of a price."""
    outreach = op(
        da,
        "outreach_log",
        data={"tender_id": world["tender"]["id"], "supplier_id": world["northgate"]["id"], "sent_on": "2026-09-16"},
    )
    op(
        da,
        "outreach_update",
        outreach_id=outreach["id"],
        data={"responded": True, "response_kind": "needs_info", "responded_on": "2026-09-18"},
    )
    asked = op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": world["northgate"]["id"],
            "tender_id": world["tender"]["id"],
            "text": "Who clears the goods at the border?",
            "raised_on": "2026-09-18",
            "source": "supplier_reported",
        },
    )
    return outreach, asked


def _we_answered(da, asked, *, sent_on="2026-09-22"):
    op(da, "commitment_resolve", commitment_id=asked["id"], resolution="We do.", resolved_on=sent_on)
    op(da, "commitment_reply_sent", commitment_ids=[asked["id"]], sent_on=sent_on)


NORTHGATE = "Northgate Rehearsal Commodities"


class TestAnsweredSupplierIsWaitedOnForItsQuote:
    def test_while_its_question_is_open_it_is_ours_not_theirs(self, da, world):
        _northgate_asked_questions(da, world)
        ours, theirs = rules.tender_moves(_tender(world), TODAY)
        assert [m.text for m in ours if m.rule == rules.RULE_OWED] == [f"Reply to {NORTHGATE} (1 question)"]
        assert world["northgate"]["id"] not in {m.supplier_id for m in theirs}

    def test_once_answered_it_waits_on_suppliers_for_the_quote(self, da, world):
        _, asked = _northgate_asked_questions(da, world)
        _we_answered(da, asked)

        ours, theirs = rules.tender_moves(_tender(world), TODAY)
        assert not [m for m in ours if m.rule == rules.RULE_OWED]
        northgate = next(m for m in theirs if m.supplier_id == world["northgate"]["id"])
        assert northgate.rule == rules.RULE_NO_REPLY
        assert northgate.state == rules.AWAITING_QUOTE
        assert northgate.text == f"{NORTHGATE}: quote (answered 9 days ago)"
        assert northgate.chip == "Answered · awaiting quote"
        assert northgate.detail == "replied 18 Sep · answered 22 Sep"
        assert northgate.since == datetime.date(2026, 9, 22)
        assert northgate.cta == "Record a quote"
        assert northgate.href == reverse("supply_chain:procurement_quote_entry") + f"?tender={world['tender']['id']}"

    def test_the_overview_counts_and_names_it(self, da, world):
        _, asked = _northgate_asked_questions(da, world)
        before = move_counts([r for r in standing_rows(PROGRAM, TODAY) if r.tender_id == world["tender"]["id"]])
        _we_answered(da, asked)

        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.tender_id == world["tender"]["id"])
        after = move_counts([row])
        assert (before["ours"] - after["ours"], after["theirs"] - before["theirs"]) == (1, 1)
        assert (NORTHGATE, "Answered · awaiting quote") in row.silent_chips

    def test_the_supplier_row_follows(self, da, world):
        _, asked = _northgate_asked_questions(da, world)
        _we_answered(da, asked)

        status = tender_status(_tender(world), TODAY, program_id=PROGRAM)
        row = next(r for r in status["suppliers"] if r["supplier_id"] == world["northgate"]["id"])
        assert row["chip"] == {"label": "Answered · awaiting quote", "tone": "theirs"}
        assert row["action"]["label"] == "Record a quote"
        rail = next(m for m in status["theirs"] if m.supplier_id == world["northgate"]["id"])
        assert (rail.text, rail.chip) == (NORTHGATE, "Answered · awaiting quote")

    def test_on_a_closed_tender_nobody_is_waited_on_and_the_row_says_answered(self, da, world):
        _, asked = _northgate_asked_questions(da, world)
        _we_answered(da, asked)
        op(da, "tender_close", tender_id=world["tender"]["id"])

        status = tender_status(_tender(world), TODAY, program_id=PROGRAM)
        assert status["theirs"] == []
        row = next(r for r in status["suppliers"] if r["supplier_id"] == world["northgate"]["id"])
        assert row["chip"] == {"label": "Questions answered", "tone": "neutral"}

    def test_a_quote_ends_the_wait(self, da, world):
        _, asked = _northgate_asked_questions(da, world)
        _we_answered(da, asked)
        op(da, "quote_record", data=_kanem_quote(world, supplier_id=world["northgate"]["id"]))

        _, theirs = rules.tender_moves(_tender(world), TODAY)
        assert world["northgate"]["id"] not in {m.supplier_id for m in theirs}

    def test_a_supplier_that_declined_is_not_waited_on(self, da, world):
        outreach = op(
            da,
            "outreach_log",
            data={
                "tender_id": world["tender"]["id"],
                "supplier_id": world["northgate"]["id"],
                "sent_on": "2026-09-16",
            },
        )
        op(
            da,
            "outreach_update",
            outreach_id=outreach["id"],
            data={"responded": True, "response_kind": "declined", "responded_on": "2026-09-18"},
        )
        _, theirs = rules.tender_moves(_tender(world), TODAY)
        assert world["northgate"]["id"] not in {m.supplier_id for m in theirs}

    def test_an_award_records_it_apart_from_the_silent(self, da, world):
        _, asked = _northgate_asked_questions(da, world)
        _we_answered(da, asked)
        facts = rules.open_at_decision(_tender(world), TODAY)
        # Kanem was asked on 6 Jul and never replied; Northgate answered and owes a quote.
        assert (facts["silent"], facts["awaiting_quote"]) == (1, 1)
        assert {"label": "1 awaiting quote", "tone": "theirs"} in rules.open_at_decision_chips(facts)


# ---- 3. the order page: who told us last, and what holds the shipment -----------


def _order(client, contract_id):
    return client.get(reverse("supply_chain:order_detail", args=[contract_id])).content.decode()


def _shipment_cell(body, testid):
    return _text(re.search(rf'data-testid="{testid}"[^>]*>(.*?)</td>', body, re.S).group(1))


def _held_shipment(da, world, **contract):
    contract = _contract(da, world, signed_on="2026-07-28", promised_lead_time_days=35, **contract)
    shipment = op(
        da,
        "shipment_record",
        data={
            "contract_id": contract["id"],
            "reference": "PO-REH-1",
            "status": "dispatched",
            "dispatched_on": "2026-08-26",
            "expected_on": "2026-09-05",
            "source": "supplier_reported",
            "lines": [{"quantity": 2000, "quantity_unit": "carton"}],
        },
    )
    return contract, shipment


class TestToldByIsTheLatestChange:
    def test_a_shipment_never_changed_reads_who_first_told_us(self, da, world, client_in_program):
        contract, _ = _held_shipment(da, world)
        assert _shipment_cell(_order(client_in_program, contract["id"]), "shipment-told-by") == "the supplier told us"

    def test_the_forwarder_s_update_then_sophie_s_edit_each_take_the_cell(
        self, da, world, client_in_program, django_user_model
    ):
        contract, shipment = _held_shipment(da, world)
        op(
            da,
            "shipment_update",
            source={"ref": "<cfc-1@forwarder.example.invalid>", "sender": "Crescent Rehearsal Freight"},
            shipment_id=shipment["id"],
            data={"status": "at_customs", "expected_on": "2026-09-19"},
        )
        assert (
            _shipment_cell(_order(client_in_program, contract["id"]), "shipment-told-by")
            == "Crescent Rehearsal Freight, 1 Oct"
        )

        sophie = django_user_model.objects.get(username="sophie-reh")
        with seed_overrides(PROGRAM, recorded_at=datetime.datetime(2026, 10, 6, 9, 0, tzinfo=datetime.UTC)):
            call_operation(
                "shipment_update",
                da,
                {"shipment_id": shipment["id"], "data": {"expected_on": "2026-10-09"}},
                channel="web",
                actor=sophie,
            )
        body = _order(client_in_program, contract["id"])
        assert _shipment_cell(body, "shipment-told-by") == "Sophie, 6 Oct"
        # Who first told us is kept: the record's own source, unchanged.
        assert op(da, "shipment_get", shipment_id=shipment["id"])["source"] == "supplier_reported"


class TestOwedDocumentsSayWhatHoldsTheShipment:
    def test_the_heading_counts_the_documents_the_shipment_is_held_on(self, da, world, client_in_program):
        # Costed at nil duty on a relief, so the exemption is owed too -- under the duty terms, not the shipment.
        contract, shipment = _held_shipment(da, world, duties_basis="excluded", duties_amount="0.00")
        op(
            da,
            "shipment_update",
            shipment_id=shipment["id"],
            data={
                "status": "at_customs",
                "required_documents": [
                    {"kind": "import_permit", "name": "Form M", "owed_by_org_id": world["us"]["id"]},
                    {"kind": "customs_declaration", "name": "PAAR", "owed_by_org_id": world["us"]["id"]},
                ],
            },
        )
        body = _order(client_in_program, contract["id"])
        assert _text(re.search(r'data-testid="owed-open-count"[^>]*>(.*?)</span>', body).group(1)) == "— 3 open"
        holding = re.search(r'data-testid="owed-holding-count"[^>]*>(.*?)</span>', body).group(1)
        assert _text(holding) == "· 2 hold the shipment"
        # The shipment row's own list: the same two.
        assert "0 of 2 documents on file" in body

    def test_no_split_when_everything_open_holds_the_shipment(self, da, world, client_in_program):
        contract, shipment = _held_shipment(da, world)
        op(
            da,
            "shipment_update",
            shipment_id=shipment["id"],
            data={
                "status": "at_customs",
                "required_documents": [{"kind": "import_permit", "owed_by_org_id": world["us"]["id"]}],
            },
        )
        body = _order(client_in_program, contract["id"])
        assert _text(re.search(r'data-testid="owed-open-count"[^>]*>(.*?)</span>', body).group(1)) == "— 1 open"
        assert 'data-testid="owed-holding-count"' not in body
