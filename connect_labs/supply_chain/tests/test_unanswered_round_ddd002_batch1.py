"""DDD 002 batch 1 on the tender page: the drafts count adds up, an answer marked
today is written into the reply it belongs to, and a restricted tender names who
"the suppliers below" are."""

import datetime
import re

import pytest

from connect_labs.supply_chain.procurement.views import _drafts_breakdown
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


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
        assert re.search(r'data-testid="drafts-breakdown"[^>]*>\([^)]*1 reply to Northgate', body)
        # Only on the row just saved.
        assert 'data-testid="owed-in-draft"' not in _tender_page(client_in_program, world["tender"]["id"])


@pytest.mark.django_db
def test_a_restricted_tender_does_not_repeat_who_was_asked(da, world, client_in_program):
    from connect_labs.supply_chain.models import Tender

    Tender.objects.filter(pk=world["tender"]["id"]).update(visibility="private")
    body = _tender_page(client_in_program, world["tender"]["id"])
    start = body.index(">Invited suppliers</h2>")
    panel = body[start : body.index("</ul>", start)]
    # Said once each: who can see it in the header's Visibility line, who was asked in Outreach
    # (unanswered round, run 003 batch 1).
    assert "only the suppliers below" not in panel and 'data-testid="invited-asked"' not in panel
    assert "Only the suppliers you invite see it" in body
