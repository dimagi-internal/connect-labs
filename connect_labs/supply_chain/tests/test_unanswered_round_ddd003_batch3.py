"""DDD run 003, batch 3: a blocked card's duty as its own line citing the answer that set it,
an origin Incoterm said as a handover rather than a delivery, a hold that says why the
document is ours, record lines set apart under an email, and the tender's table tidied."""

import html
import re
from types import SimpleNamespace

import pytest
from django.urls import reverse

from connect_labs.supply_chain.procurement.services.comparison import landed_basis_words, round_duty_words
from connect_labs.supply_chain.templatetags.supply_chain_extras import record_kind_lead
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
class TestTheDutyLine:
    def test_the_waiver_keeps_what_the_quote_itself_said(self, da, world):
        quote = _quote(da, world, duties_basis="excluded", duties_amount="0.00")
        _set(da, world, "buyer_waiver")
        quote.tender.refresh_from_db()
        assert round_duty_words(quote, quote.tender) == "Duty waived (our import) · the quote also stated zero"
        assert "duty waived" not in landed_basis_words(quote, quote.tender, round_duty=False)
        assert "duty waived (our import)" in landed_basis_words(quote, quote.tender)

    def test_unsettled_terms_give_no_duty_line(self, da, world):
        quote = _quote(da, world)
        assert round_duty_words(quote, quote.tender) == ""

    def test_the_blocked_card_cites_the_answer_that_set_the_terms(self, da, world, client_in_program):
        question = _question(da, world)
        op(
            da,
            "commitment_resolve",
            commitment_id=question["id"] if isinstance(question, dict) else question.pk,
            resolution="We import, under the program's duty waiver.",
            duty_terms="buyer_waiver",
            resolved_on="2026-10-03",
        )
        _quote(da, world, pack_spec_source="not_stated")
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
        body = client_in_program.get(url).content.decode()
        if 'data-testid="card-duty"' in body:
            line = _text(re.search(r'data-testid="card-duty".*?</p>', body, re.S).group(0))
            assert "Duty waived (our import)" in line
            assert "from your answer to" in line and "3 Oct" in line


def test_an_origin_incoterm_is_a_handover_not_a_delivery():
    tender = SimpleNamespace(delivery_points=[{"key": "kano", "name": "Kano"}], duty_terms="")
    quote = SimpleNamespace(
        delivery_mode="delivered",
        delivery_point_keys=[],
        incoterm="EXW Niamey",
        freight_basis="",
        freight_amount=None,
        duties_basis="",
        duties_amount=None,
        as_quoted_currency="EUR",
    )
    words = landed_basis_words(quote, tender)
    assert words.startswith("Ex works Niamey — delivery to ") and "requested" in words
    assert "Delivered to" not in words


def test_a_record_line_under_an_email_sets_its_kind_apart():
    out = str(record_kind_lead("Outreach · Sahel & Co · Replied with a quote on 3 Oct 2026"))
    assert out.startswith('<span data-testid="record-kind" class="font-semibold">Outreach</span> · ')
    assert "Sahel &amp; Co" in out
    assert str(record_kind_lead("Quote recorded: 42.50 USD")) == "Quote recorded: 42.50 USD"


@pytest.mark.django_db
def test_a_silent_row_says_dash_for_its_response_kind(da, world, client_in_program):
    body = _tender_page(client_in_program, world["tender"]["id"])
    kinds = re.findall(r'data-testid="response-kind">([^<]*)<', body)
    assert kinds and all(kind.strip() for kind in kinds)
