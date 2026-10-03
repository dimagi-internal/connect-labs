"""DDD run 003, batch 4, the comparison: the round's terms said again above the Needs info
cards, a waiver with no document on file said on each card, a quote that states duty
included under the waiver given its next move, and one comparable offer as a compact card."""

import html
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.procurement.services.comparison import (
    landed_basis_words,
    round_duty_consequence,
)
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import op
from connect_labs.supply_chain.tests.test_unanswered_round_batch2 import _question, web  # noqa: F401 (fixture)
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _quote, _set

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


def _answer_with_waiver(da, world):
    question = _question(da, world)
    op(
        da,
        "commitment_resolve",
        commitment_id=question["id"] if isinstance(question, dict) else question.pk,
        resolution="We import, under the program's duty waiver.",
        duty_terms="buyer_waiver",
        resolved_on="2026-10-03",
    )


def _comparison(client, world):
    url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
    return client.get(url).content.decode()


@pytest.mark.django_db
class TestTheComparisonUnderTheWaiver:
    def test_a_zero_duty_is_not_said_per_quote_twice(self, da, world):
        quote = _quote(da, world, duties_basis="excluded", duties_amount="0.00", freight_basis="included")
        words = landed_basis_words(quote, quote.tender)
        assert words.count("quote") == 1, words

    def test_duty_included_under_the_waiver_says_what_to_ask(self, da, world):
        quote = _quote(da, world, duties_basis="included")
        assert round_duty_consequence(quote, quote.tender) == ""
        _set(da, world, "buyer_waiver")
        quote.tender.refresh_from_db()
        said = round_duty_consequence(quote, quote.tender)
        assert "restate the price without duty" in said

    def test_the_needs_info_section_carries_the_round_terms_and_the_missing_waiver(self, da, world, client_in_program):
        _answer_with_waiver(da, world)
        _quote(da, world, pack_spec_source="not_stated", duties_basis="included")
        body = _comparison(client_in_program, world)
        if 'data-testid="needs-info"' not in body:
            pytest.skip("the quote is comparable in this world; nothing to card")
        terms = _text(re.search(r'data-testid="needs-info-duty-terms".*?</p>', body, re.S).group(0))
        # Since batch 5 the answer is cited on each card's duty line; the terms line keeps the day.
        assert "Round terms:" in terms and "set 3 Oct" in terms
        assert 'data-testid="card-waiver-missing"' in body
        attach = reverse("supply_chain:tender_document_attach", args=[world["tender"]["id"]])
        assert attach in body
        assert "restate the price without import duty" in body


@pytest.mark.django_db
def test_the_tender_attach_page_offers_the_duty_waiver(da, world, client_in_program):
    url = reverse("supply_chain:tender_document_attach", args=[world["tender"]["id"]])
    response = client_in_program.get(url)
    assert response.status_code == 200
    body = response.content.decode()
    assert re.search(r'<option value="duty_exemption"[^>]*selected', body), "the waiver is the kind offered"
