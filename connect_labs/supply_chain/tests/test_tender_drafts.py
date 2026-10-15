"""The tender's drafted emails: reminders, replies, and recording the chase.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import datetime
import re
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Outreach, Tender
from connect_labs.supply_chain.procurement.views import (
    _drafts_breakdown,
    _message_rows,
    _reminder_facts,
    next_reminder_due,
)
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import _contract, _question, _quote, _tender_page, op

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


class TestChaseFromTheDraft:
    def test_the_reminder_draft_carries_a_chase_form(self, da, world, web):
        body = _tender_page(web, world["tender"]["id"])
        drafts = body.split('id="drafts"', 1)[1]
        form = re.search(r'<form [^>]*data-testid="chase-form".*?</form>', drafts, re.S).group(0)
        url = reverse("supply_chain:procurement_outreach_chase", args=[world["outreach"]["id"]])
        assert f'action="{url}"' in form
        today = f"{datetime.date.today():%-d %b %Y}"
        assert re.search(rf'data-testid="chase-date" type="text" name="last_reminder_on"\s+value="{today}"', form)
        assert 'data-testid="mark-sent"' in form and ">Mark sent</button>" in form

    def test_recording_a_chase_sets_only_the_day_and_returns_to_the_row(self, da, world, web):
        outreach_id = world["outreach"]["id"]
        op(
            da,
            "outreach_update",
            outreach_id=outreach_id,
            data={"responded": True, "response_kind": "needs_info", "responded_on": "2026-07-09"},
        )
        response = web.post(
            reverse("supply_chain:procurement_outreach_chase", args=[outreach_id]), {"last_reminder_on": "2026-07-14"}
        )
        tender_url = reverse("supply_chain:procurement_tender_detail", args=[world["tender"]["id"]])
        assert response.url == f"{tender_url}?changed=outreach-{outreach_id}&cell=last_chased#outreach-{outreach_id}"
        row = Outreach.objects.get(pk=outreach_id)
        assert row.last_reminder_on == datetime.date(2026, 7, 14)
        # The reply it held is untouched: the chase form posts the date alone.
        assert row.responded and row.response_kind == "needs_info"


def test_the_drafts_count_breaks_down_by_kind_and_names_a_reply():
    drafts = [
        {"kind": "reminder", "supplier_name": "A"},
        {"kind": "reminder", "supplier_name": "B"},
        {"kind": "request", "supplier_name": "C"},
        {"kind": "reply", "supplier_name": "Northgate Commodities"},
    ]
    assert _drafts_breakdown(drafts) == "1 quote request · 2 reminders · 1 reply to Northgate Commodities"
    assert _drafts_breakdown([]) == ""


@pytest.mark.django_db
class TestAnAnswerGoesIntoTheReply:
    def test_answered_today_is_written_in_and_an_earlier_answer_is_not_owed(self, da, world):
        first = _question(da, world, "Who is the importer of record?")
        _question(da, world, "One warehouse, or several?")
        today = datetime.date.today().isoformat()
        op(
            da,
            "commitment_resolve",
            channel="web",
            commitment_id=first["id"],
            resolution="We import, under the duty waiver.",
            resolved_on=today,
        )
        drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"], today=today)["drafts"]
        reply = next(d for d in drafts if d["kind"] == "reply")
        assert "1. Who is the importer of record?\n   We import, under the duty waiver." in reply["text"]
        assert "2. One warehouse, or several?\n   [Your answer]" in reply["text"]
        assert first["id"] in reply["commitment_ids"]
        # The next day the answered one is no longer drafted.
        tomorrow = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        later = op(da, "tender_drafts_render", tender_id=world["tender"]["id"], today=tomorrow)["drafts"]
        reply = next(d for d in later if d["kind"] == "reply")
        assert "importer" not in reply["text"] and len(reply["commitment_ids"]) == 1

    def test_the_row_just_answered_links_to_the_draft_reply(self, da, world, client_in_program):
        asked = _question(da, world)
        op(da, "commitment_resolve", channel="web", commitment_id=asked["id"], resolution="We are.")
        body = _tender_page(client_in_program, world["tender"]["id"], f"?changed=commitment-{asked['id']}")
        note = re.search(r'data-testid="owed-in-draft".*?</div>', body, re.S).group(0)
        anchor = re.search(r'href="#(draft-reply-[^"]+)"', note).group(1)
        assert "Added to your draft reply to Northgate Rehearsal Commodities" in note
        assert f'id="{anchor}"' in body
        assert re.search(r'data-testid="drafts-breakdown"[^>]*>[^<]*1 reply to Northgate', body)
        # Only on the row just saved.
        assert 'data-testid="owed-in-draft"' not in _tender_page(client_in_program, world["tender"]["id"])


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
        assert re.search(r'data-testid="draft-text" readonly rows="\d+"', card)
        today = f"{datetime.date.today():%-d %b %Y}"
        assert re.search(rf'data-testid="chase-date" type="text" name="last_reminder_on"\s+value="{today}"', card)
        # The why line is body text, not the smallest grey on the card.
        # Every draft's why is a labelled row (DDD 003 batch 2; sheets b5), never a sentence.
        assert re.search(r'<dl class="[^"]*" data-testid="draft-why">', card)

    def test_a_chase_day_typed_as_the_page_writes_it_is_recorded(self, da, world, web):  # noqa: F811
        outreach_id = world["outreach"]["id"]
        web.post(
            reverse("supply_chain:procurement_outreach_chase", args=[outreach_id]), {"last_reminder_on": "2 Oct 2026"}
        )
        assert Outreach.objects.get(pk=outreach_id).last_reminder_on == datetime.date(2026, 10, 2)


@pytest.mark.django_db
class TestTheDrafts:
    def test_no_draft_names_an_operation(self, da, world, client_in_program):
        _question(da, world)
        drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"])
        reply = next(d for d in drafts["drafts"] if d["kind"] == "reply")
        assert "commitment_resolve" not in reply["why"]
        assert "Questions and promises" in reply["why"]

    def test_a_supplier_facing_subject_carries_no_internal_label(self, da, world):
        tender = Tender.objects.get(pk=world["tender"]["id"])
        Tender.objects.filter(pk=tender.pk).update(label="RUTF round 9: internal")
        _quote(da, world)
        drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"])
        for draft in drafts["drafts"]:
            assert "RUTF round 9" not in draft["subject"]


@pytest.mark.django_db
class TestTheReminderCard:
    def test_the_facts_say_which_reminder_this_would_be(self):
        facts = _reminder_facts(
            {"sent_on": "2026-09-16", "last_reminder_on": "2026-09-24", "reminder_count": 2},
            7,
            as_of=__import__("datetime").date(2026, 10, 3),
        )
        words = {f["label"]: f["value"] for f in facts}
        assert words["Asked"] == "16 Sep 2026 (17 days ago)"
        assert words["Last reminded"] == "24 Sep 2026 · 2nd reminder"
        assert words["This would be"] == "the 3rd reminder"
        assert words["Due every"] == "7 days"

    def test_a_draft_is_signed_by_the_buyer_of_record_when_the_caller_is_not_resolvable(self, da, world):
        _contract(da, world)
        drafts = op(da, "tender_drafts_render", tender_id=world["tender"]["id"], today="2026-10-03")["drafts"]
        for draft in drafts:
            assert "[your organisation]" not in draft["text"]


def test_next_reminder_is_never_after_the_deadline():
    sent = date(2026, 10, 2)
    assert next_reminder_due(sent, 7, date(2026, 10, 20)) == date(2026, 10, 9)
    assert next_reminder_due(sent, 7, date(2026, 10, 6)) is None
    assert next_reminder_due(sent, 7, None) == date(2026, 10, 9)
