"""The sheets walkthrough, judged (batch 4): the reply that went out is said on the
supplier's row, and anything scrolled to on a supply page clears the top bar.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented.
Builds on test_sheets_b1_domain's world (one open RUTF tender, Northgate asked
questions on 18 Sep).
"""

import re

from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain.procurement.status import tender_status
from connect_labs.supply_chain.tests import test_sheets_b1_domain as b1
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, TODAY, op

da = b1.da
world = b1.world
client_in_program = b1.client_in_program


def _northgate(moves, world):
    return next(m for m in moves if m.supplier_id == world["northgate"]["id"])


def test_a_reply_marked_sent_is_said_under_the_waiting_row(da, world):
    _, asked = b1._northgate_asked_questions(da, world)
    b1._we_answered(da, asked, sent_on="2026-09-22")

    status = tender_status(b1._tender(world), TODAY, program_id=PROGRAM)
    assert _northgate(status["theirs"], world).note == "reply sent 22 Sep"
    # Read without being handed anything, the same.
    _, theirs = rules.tender_moves(b1._tender(world), TODAY)
    assert _northgate(theirs, world).note == "reply sent 22 Sep"


def test_an_answer_only_marked_resolved_is_not_a_reply_sent(da, world):
    _, asked = b1._northgate_asked_questions(da, world)
    op(da, "commitment_resolve", commitment_id=asked["id"], resolution="Answered by phone.", resolved_on="2026-09-22")

    _, theirs = rules.tender_moves(b1._tender(world), TODAY)
    northgate = _northgate(theirs, world)
    assert northgate.state == rules.AWAITING_QUOTE
    assert northgate.note == ""


def test_the_tender_page_shows_reply_sent_on_the_row(client_in_program, da, world):
    _, asked = b1._northgate_asked_questions(da, world)
    b1._we_answered(da, asked, sent_on="2026-09-22")

    body = client_in_program.get(
        reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
    ).content.decode()
    rail = body.split('data-testid="on-suppliers"', 1)[1].split("</section>", 1)[0]
    row = next(r for r in re.split(r'data-testid="move"', rail) if b1.NORTHGATE in r)
    assert "Answered · awaiting quote" in row
    assert re.search(r'data-testid="move-note"[^>]*>reply sent 22 Sep<', row)


def test_anything_scrolled_to_clears_the_top_bar(client_in_program, world):
    """Not only headings and ids: a recorder's scroll to the stage bar landed it under the bar."""
    body = client_in_program.get(
        reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
    ).content.decode()
    assert ".supply-page, .supply-page * { scroll-margin-top: 5rem; }" in body
    # And an anchor is landed again once fonts and bundles have reflowed the page.
    assert 'window.addEventListener("load"' in body
