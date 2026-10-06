"""Sheets batch 5: every draft says why it is due as a labelled row of facts, never a
sentence, and the tender header's terms are separate fields rather than one run-on line."""

import html
import re

import pytest

from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _set

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


def _drafts(da, world):
    return op(da, "tender_drafts_render", tender_id=world["tender"]["id"])["drafts"]


def _facts(draft):
    return {f["label"]: f["value"] for f in draft["facts"]}


@pytest.mark.django_db
class TestEveryDraftCarriesFacts:
    def test_a_reply_says_how_many_since_when_and_who_owes_it(self, da, world):
        _question(da, world)
        _question(da, world, text="Can you take delivery on a Saturday?")
        reply = next(d for d in _drafts(da, world) if d["kind"] == "reply")
        assert _facts(reply) == {"Questions open": "2", "Open since": "11 Jul 2026", "Owed by": "us"}

    def test_a_reply_answered_today_counts_what_is_written_in(self, da, world):
        question = _question(da, world)
        op(da, "commitment_resolve", commitment_id=question["id"], resolution="We are.")
        reply = next(d for d in _drafts(da, world) if d["kind"] == "reply")
        assert _facts(reply) == {"Answered today": "1, written in", "Owed by": "us"}

    def test_no_fact_is_a_sentence(self, da, world):
        _question(da, world)
        _set(da, world, "buyer_waiver")
        drafts = _drafts(da, world)
        assert "reply" in {d["kind"] for d in drafts}
        for d in drafts:
            assert d["facts"], d["kind"]
            for f in d["facts"]:
                assert len(f"{f['label']} {f['value']}".split()) <= 8, f


@pytest.mark.django_db
class TestTheTenderPage:
    def test_every_draft_card_shows_a_facts_row_and_no_why_sentence(self, da, world, client_in_program):
        _question(da, world)
        _set(da, world, "buyer_waiver")
        body = _tender_page(client_in_program, world["tender"]["id"])
        drafts = body.split('id="drafts"', 1)[1]
        cards = drafts.count('data-testid="draft"')
        assert cards >= 1
        assert drafts.count('<dl class="mt-2 flex') == cards
        assert '<p class="text-sm text-gray-700 mt-1" data-testid="draft-why">' not in body
        assert "we owe the answer" not in body
        assert "every invited supplier is told" not in body
        row = re.search(r'id="draft-reply-[^"]*".*?data-testid="draft-why">(.*?)</dl>', drafts, re.S).group(1)
        assert ">Questions open</dt>" in row and ">Owed by</dt>" in row

    def test_the_header_terms_are_labelled_fields(self, da, world, client_in_program):
        _set(da, world, "buyer_waiver")
        body = _tender_page(client_in_program, world["tender"]["id"])
        summary = re.search(r'data-testid="tender-summary".*?</div>', body, re.S).group(0)
        assert 'data-testid="tender-visibility"' in summary
        assert " · " not in _text(summary)
        # Who set the terms, and the waiver's evidence, sit beside the terms as their own fields.
        assert 'data-testid="duty-terms-set"' in body
        duty = body.split('data-testid="tender-duty-terms"', 1)[1].split('data-testid="duty-terms-set"', 1)[0]
        assert "· set" not in duty and "exemption" not in duty
