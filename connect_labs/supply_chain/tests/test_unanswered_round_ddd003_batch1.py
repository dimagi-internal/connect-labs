"""DDD run 003, batch 1: the round's facts as states, drafts that name no tool, the
comparison under the waiver, the duty-terms history line, and objective order copy."""

import html
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.labels import sentence
from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.procurement.views import missing_item
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question, web  # noqa: F401 (fixture)
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _quote, _set

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


@pytest.mark.django_db
class TestTheTenderHeader:
    def test_the_facts_are_a_label_value_block(self, da, world, client_in_program):
        body = _tender_page(client_in_program, world["tender"]["id"])
        facts = re.search(r'data-testid="tender-facts".*?</dl>', body, re.S).group(0)
        for label in ("Where the goods go", "Visibility"):
            assert f">{label}</dt>" in facts
        terms = _text(re.search(r'data-testid="tender-terms".*?</section>', body, re.S).group(0))
        # The day asked is the stage bar's, not repeated on the terms line.
        for label in ("Import duty", "Deadline"):
            assert label in terms

    def test_the_waiver_carries_its_evidence(self, da, world, client_in_program):
        _set(da, world, "buyer_waiver")
        body = _tender_page(client_in_program, world["tender"]["id"])
        terms = _text(re.search(r'data-testid="tender-terms".*?</section>', body, re.S).group(0))
        assert "Duty exemption not on file" in terms


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
class TestTheComparisonUnderTheWaiver:
    def test_the_blocked_card_says_duty_is_waived(self, da, world, client_in_program):
        _quote(da, world, pack_spec_source="not_stated")
        _set(da, world, "buyer_waiver")
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        note = _text(re.search(r'data-testid="comparison-duty-terms".*?</summary>', body, re.S).group(0))
        assert "we import under the duty waiver" in note
        grid = _text(re.search(r'data-testid="comparison-grid".*?</table>', body, re.S).group(0))
        assert "our terms: not settled" not in grid

    def test_the_banner_lists_each_supplier_s_missing_facts(self):
        row = {
            "supplier_name": "Sahel",
            "blockers": [
                {"label": "freight amount", "fact": "Freight excluded"},
                {"label": "exchange rate", "fact": "Quote is in EUR and no exchange rate was recorded"},
            ],
        }
        assert missing_item(row) == "Sahel: freight amount, exchange rate (EUR)"


def test_the_duty_terms_history_line_reads_as_a_sentence():
    line = sentence(
        Tender, "update", {"duty_terms": ["", "buyer_waiver"], "duty_terms_set_on": [None, "2026-10-03"]}, None
    )
    assert line == "Set the tender's import duties: we import under the duty waiver"
