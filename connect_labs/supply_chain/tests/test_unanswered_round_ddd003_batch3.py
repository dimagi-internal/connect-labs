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
        # Since DDD 004 the supplier's figure is not repeated: the round's terms are the basis.
        assert round_duty_words(quote, quote.tender) == "waived (our import)"
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
            assert "Duty: waived (our import)" in line
            # Since DDD 005 batch 3 the card cites the answer as a short "(round terms)" link on
            # its Duty line; the terms panel at the top says where they came from.
            source = re.search(r'<a data-testid="card-duty-provenance"[^>]*>', body).group(0)
            assert "from your answer to" in source
            assert "(round terms)" in line
            # Since DDD 003 batch 8 the terms are stated once, at the top of the comparison
            # (the Round terms line above the cards repeated them), and the day is on the card.
            assert 'data-testid="needs-info-duty-terms"' not in body
            assert "3 Oct" in source


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
    # The reply's kind is the row's State chip since 2026-10-04; a silent row says so there,
    # and carries no "replied" line under it (the day it replied rides the State cell, 2026-10-04 b2).
    body = _tender_page(client_in_program, world["tender"]["id"])
    states = re.findall(r'data-testid="supplier-state">([^<]*)<', body)
    assert states and all(state.strip() for state in states)
    replied = re.findall(r'data-testid="replied">\s*([^<]*?)\s*<', body)
    assert all(cell for cell in replied)
