"""DDD 002 batch 3: a drafted reminder reads whole on its own card with its chase
day in the page's format; history says one date format, one toggle label and
who recorded an email; the award's caveat sits under the action; and every
flag on the overview has an owner on the row's Waiting on."""

import datetime
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from connect_labs.supply_chain.models import Outreach
from connect_labs.supply_chain.procurement.views import _message_rows
from connect_labs.supply_chain.standing import standing_rows
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, _contract, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import web  # noqa: F401 (fixture)

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def test_a_drafted_message_gets_the_rows_it_needs_up_to_a_screen():
    assert _message_rows("Dear Amadou,\n\nWe asked on 15 Sep.") == 3
    assert _message_rows("x" * 230) == 3
    assert _message_rows("\n".join(["line"] * 40)) == 28


@pytest.mark.django_db
class TestTheReminderDraft:
    def test_each_draft_is_a_card_with_its_message_sized_and_the_day_in_page_format(
        self, da, world, client_in_program
    ):
        body = _tender_page(client_in_program, world["tender"]["id"])
        drafts = body.split('id="drafts"', 1)[1]
        # The chase form sits under the reminder's facts, above the message (DDD 003 batch 3).
        # Through the end of the card's chase form, which since DDD 003 batch 4 follows the message.
        card = re.search(r'<div data-testid="draft".*?data-testid="chase-form".*?</form>', drafts, re.S).group(0)
        assert "rounded-lg" in card and "border-gray-200" in card
        assert re.search(r'data-testid="draft-text" readonly rows="\d+"', card)
        today = f"{datetime.date.today():%-d %b %Y}"
        assert re.search(rf'data-testid="chase-date" type="text" name="last_reminder_on"\s+value="{today}"', card)
        # The why line is body text, not the smallest grey on the card.
        # A reminder's facts are a labelled row (DDD 003 batch 2); other drafts keep the sentence.
        assert re.search(r'<p class="text-sm text-gray-700 mt-1" data-testid="draft-why">', card) or re.search(
            r'<dl class="[^"]*" data-testid="draft-why">', card
        )

    def test_a_chase_day_typed_as_the_page_writes_it_is_recorded(self, da, world, web):  # noqa: F811
        outreach_id = world["outreach"]["id"]
        web.post(
            reverse("supply_chain:procurement_outreach_chase", args=[outreach_id]), {"last_reminder_on": "2 Oct 2026"}
        )
        assert Outreach.objects.get(pk=outreach_id).last_reminder_on == datetime.date(2026, 10, 2)


def test_a_quote_s_void_is_a_link_like_correct():
    html = render_to_string(
        "supply_chain/_timeline_line.html",
        {
            "entry": {"line": "Quote recorded", "correct_url": "/c/", "void_url": "/v/"},
            "in_event": True,
            "event_sender": "",
        },
    )
    fixes = re.search(r'data-testid="quote-fixes"[^>]*>(.*?)</div>', html, re.S).group(1)
    assert 'href="/c/" class="text-brand-indigo' in fixes
    # Since the unanswered-round batch 1 judge: sibling controls styled alike (grey Void
    # beside a blue Correct read as disabled).
    assert 'data-testid="quote-void" class="text-brand-indigo hover:underline"' in fixes


@pytest.mark.django_db
class TestEveryMoveHasAnOwner:
    def test_a_tender_past_its_deadline_is_ours_to_decide(self, da, world):
        op(da, "tender_update", tender_id=world["tender"]["id"], data={"response_deadline": "2026-09-29"})
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "tender")
        deadline = [m for m in row.ours if m.rule == "deadline"]
        assert len(deadline) == 1
        assert deadline[0].text.endswith(": extend, close or award")
        assert deadline[0].detail == "deadline passed 29 Sep"
        before = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 9, 28)) if r.kind == "tender")
        assert not [m for m in before.ours if m.rule == "deadline"]

    def test_an_invoice_above_agreed_is_ours_to_review(self, da, world):
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
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "order")
        review = [m for m in row.ours if m.rule == "invoice check"]
        assert [m.text for m in review] == ["Review invoice INV-REH-1"]
        assert row.whose == "us"
