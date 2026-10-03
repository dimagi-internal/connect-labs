"""The only rules that put a move on "On us" or "On suppliers" (moves.py).

THIS REPOSITORY IS PUBLIC. Every name, id and figure below is invented.

  (a) owed           open commitments, grouped per counterparty, and held documents
  (b) deadline       an open tender past its response deadline
  (c) invoice check  an invoice above its order (the invoice_above_contract check)
  (d) a shipment held on us is (a)'s held documents and promises -- not a second item
  (e) no reply       an invited supplier with neither a reply nor a quote
  (f) a question we asked a supplier is not recorded, so it raises nothing

Nothing else generates moves: a quote missing a fact is not one.
"""

import datetime

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import moves
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.fulfilment.services.holds import Hold
from connect_labs.supply_chain.models import Commitment, Tender
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.standing import move_counts, standing_rows

PROGRAM = 20871
TODAY = datetime.date(2026, 10, 3)


@pytest.fixture
def da(db):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=PROGRAM,
        program_id=PROGRAM,
        labs_only=True,
        enabled=True,
        label="moves tests",
        allowed_domains=["example.invalid"],
    )
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload, channel="command")


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"})
    names = ("Quill Foods", "Marsh Nutrition", "Ember Pastes")
    suppliers = {n: op(da, "supplier_create", data={"name": n}) for n in names}
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Test tender 1",
            "delivery_point": {"city": "Kano"},
            "response_deadline": "2026-09-30",
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    op(da, "tender_update", tender_id=tender["id"], data={"status": "open"})
    for supplier in suppliers.values():
        op(
            da,
            "outreach_log",
            data={"tender_id": tender["id"], "supplier_id": supplier["id"], "sent_on": "2026-09-16"},
        )
    return {"tender": tender, "suppliers": suppliers}


def _tender(world):
    return Tender.objects.get(pk=world["tender"]["id"])


def _rules(items):
    return [m.rule for m in items]


def test_a_open_questions_are_one_move_per_counterparty(da, world):
    quill = world["suppliers"]["Quill Foods"]
    for text in ("Who imports?", "Which pallet?", "Which label language?"):
        op(
            da,
            "commitment_record",
            data={
                "kind": "question",
                "supplier_id": quill["id"],
                "tender_id": world["tender"]["id"],
                "text": text,
                "raised_on": "2026-09-19",
                "source": "supplier_reported",
            },
        )
    ours, _ = moves.tender_moves(_tender(world), TODAY)
    owed = [m for m in ours if m.rule == moves.RULE_OWED]
    assert len(owed) == 1
    assert owed[0].text == "Reply to Quill Foods (3 questions)"
    assert owed[0].href.endswith(f"#draft-reply-{quill['id']}")
    assert owed[0].detail == "open since 19 Sep"


def test_a_answering_the_questions_clears_the_move(da, world):
    quill = world["suppliers"]["Quill Foods"]
    asked = op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": quill["id"],
            "tender_id": world["tender"]["id"],
            "text": "Who imports?",
            "raised_on": "2026-09-19",
            "source": "supplier_reported",
        },
    )
    op(da, "commitment_resolve", commitment_id=asked["id"], resolution="We do.")
    ours, _ = moves.tender_moves(_tender(world), TODAY)
    assert moves.RULE_OWED not in _rules(ours)


def test_a_held_documents_are_grouped_by_who_asked_and_d_is_folded_in():
    holds = [
        Hold(what="import permit", since=None, shipment_id=1, kind="import_permit", asked_by="Delta Clearing"),
        Hold(what="customs declaration", since=None, shipment_id=1, kind="customs", asked_by="Delta Clearing"),
    ]
    out = moves.owed_moves([], holds, contract_id=7)
    assert [m.text for m in out] == ["Provide 2 documents to Delta Clearing"]
    assert out[0].rule == moves.RULE_OWED


def test_b_an_open_tender_past_its_deadline_is_ours_to_decide(da, world):
    ours, _ = moves.tender_moves(_tender(world), TODAY)
    deadline = [m for m in ours if m.rule == moves.RULE_DEADLINE]
    assert [m.text for m in deadline] == ["Decide on Test tender 1: extend, close or award"]
    before, _ = moves.tender_moves(_tender(world), datetime.date(2026, 9, 29))
    assert moves.RULE_DEADLINE not in _rules(before)


def test_c_an_invoice_above_its_order_is_one_review(monkeypatch):
    class Found:
        facts = {
            "currency": "USD",
            "above": [{"field": "total", "difference": "3550.00"}, {"field": "freight_amount", "invoice": "INV-9"}],
        }
        since = datetime.date(2026, 9, 22)

    monkeypatch.setattr("connect_labs.supply_chain.checks._invoice_above_contract", lambda *a: Found())
    monkeypatch.setattr("connect_labs.supply_chain.fulfilment.services.landed.landed_total", lambda c: {})

    class Contract:
        pk = 9
        consideration = "priced"

    out = moves.invoice_moves(Contract(), TODAY)
    assert [(m.text, m.rule, m.detail) for m in out] == [
        ("Review invoice INV-9", moves.RULE_INVOICE, "USD 3,550.00 above agreed")
    ]


def test_c_no_invoice_check_no_move(monkeypatch):
    monkeypatch.setattr("connect_labs.supply_chain.checks._invoice_above_contract", lambda *a: None)
    monkeypatch.setattr("connect_labs.supply_chain.fulfilment.services.landed.landed_total", lambda c: {})

    class Contract:
        pk = 9
        consideration = "priced"

    assert moves.invoice_moves(Contract(), TODAY) == []


def test_e_each_silent_supplier_is_on_suppliers_until_it_replies_or_quotes(da, world):
    _, theirs = moves.tender_moves(_tender(world), TODAY)
    assert {m.text for m in theirs} == {
        "Quill Foods: reply (silent 17 days)",
        "Marsh Nutrition: reply (silent 17 days)",
        "Ember Pastes: reply (silent 17 days)",
    }
    assert set(_rules(theirs)) == {moves.RULE_NO_REPLY}
    marsh = world["suppliers"]["Marsh Nutrition"]
    op(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": marsh["id"],
            "as_quoted_amount": "42.50",
            "as_quoted_unit": "per_pack",
            "received_on": "2026-09-20",
        },
    )
    _, theirs = moves.tender_moves(_tender(world), TODAY)
    assert "Marsh Nutrition: reply (silent 17 days)" not in {m.text for m in theirs}


def test_e_a_closed_tender_chases_nobody(da, world):
    op(da, "tender_update", tender_id=world["tender"]["id"], data={"status": "closed"})
    ours, theirs = moves.tender_moves(_tender(world), TODAY)
    assert theirs == []
    assert moves.RULE_DEADLINE not in _rules(ours)


def test_a_quote_missing_a_fact_raises_no_move(da, world):
    """Quote gaps are blanks in the table and the grid, never a move (and (f) records nothing)."""
    marsh = world["suppliers"]["Marsh Nutrition"]
    op(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "rutf",
            "supplier_id": marsh["id"],
            "as_quoted_amount": "0.31",
            "as_quoted_unit": "per_base_unit",
            "as_quoted_currency": "EUR",
            "incoterm": "EXW Niamey",
            "received_on": "2026-09-20",
        },
    )
    ours, theirs = moves.tender_moves(_tender(world), datetime.date(2026, 9, 21))
    assert ours == []
    assert all(m.rule == moves.RULE_NO_REPLY for m in theirs)
    assert not any("Marsh" in m.text for m in theirs)


def test_the_overview_counts_the_same_moves_as_the_tender(da, world):
    rows = standing_rows(PROGRAM, TODAY)
    row = next(r for r in rows if r.tender_id == world["tender"]["id"])
    ours, theirs = moves.tender_moves(_tender(world), TODAY)
    assert [m.text for m in row.ours] == [m.text for m in ours]
    assert [m.text for m in row.theirs] == [m.text for m in theirs]
    assert row.whose == moves.US
    assert row.next_move.rule == moves.RULE_DEADLINE
    assert move_counts(rows) == {"ours": len(ours), "theirs": len(theirs)}
    assert row.bars[:2] == ["done", "now"]


def test_the_tender_page_lists_the_moves_with_their_rule(da, world, client, django_user_model, monkeypatch):
    from connect_labs.supply_chain.procurement import views as procurement_views

    client.force_login(django_user_model.objects.create_user(username="s", email="s@dimagi.com", password="x"))
    monkeypatch.setattr(procurement_views, "_access", lambda request: da)
    monkeypatch.setattr(procurement_views, "has_program_context", lambda request: True)
    response = client.get(reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]]))
    assert response.status_code == 200
    body = response.content.decode()
    assert 'data-testid="on-us"' in body
    assert body.count('data-rule="deadline"') == 1
    assert body.count('data-rule="no reply"') == 3


def test_replying_marks_open_questions_answered(da, world):
    """Marking the drafted reply sent clears "Reply to ..." (commitment_resolve + commitment_reply_sent)."""
    quill = world["suppliers"]["Quill Foods"]
    asked = op(
        da,
        "commitment_record",
        data={
            "kind": "question",
            "supplier_id": quill["id"],
            "tender_id": world["tender"]["id"],
            "text": "Who imports?",
            "raised_on": "2026-09-19",
            "source": "supplier_reported",
        },
    )
    op(da, "commitment_resolve", commitment_id=asked["id"], resolution="Answered in our reply.")
    op(da, "commitment_reply_sent", commitment_ids=[asked["id"]])
    assert Commitment.objects.get(pk=asked["id"]).reply_sent_on is not None
    ours, _ = moves.tender_moves(_tender(world), TODAY)
    assert moves.RULE_OWED not in _rules(ours)
