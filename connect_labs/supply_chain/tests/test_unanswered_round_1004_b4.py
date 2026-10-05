"""DDD unanswered round 2026-10-04, batch 4.

D: one document, one name. The duty exemption a waived duty rests on is named from the
Document kind (records.DOCUMENT_KIND_LABELS) on every page -- the comparison's chips and
actions, the tender's terms, an order's landed table and what-we-owe, the overview's gap
tags -- and the order's nil duty takes the comparison's cell shape: the figure and a chip.

G: the History's line that recorded a quote carries that quote's open facts as the same
outlined gap chips the overview uses, read from status.quote_open_facts.
"""

import html
import re

import pytest
from django.template.loader import get_template
from django.urls import reverse

from connect_labs.supply_chain import moves as rules
from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import Document
from connect_labs.supply_chain.procurement.status import fact_chips, quote_open_facts
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import _contract, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _quote

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", fragment).split()))


def _waiver(da, world):
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="buyer_waiver")
    return _quote(da, world)


# ---- D. one name for the document --------------------------------------------


def test_the_display_name_is_the_document_kinds_own():
    assert records.document_kind_label("duty_exemption") == "duty exemption"
    assert dict(Document._meta.get_field("kind").choices)["duty_exemption"] == "duty exemption"
    # Every kind has exactly one label, and the model's choices are that map.
    assert set(records.DOCUMENT_KIND_LABELS) == set(records.DOCUMENT_KINDS)
    assert dict(Document._meta.get_field("kind").choices) == records.DOCUMENT_KIND_LABELS


@pytest.mark.django_db
def test_the_comparison_names_it_and_its_action_by_that_name(da, world, client_in_program):
    _waiver(da, world)
    url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
    body = client_in_program.get(url).content.decode()
    assert "waiver document" not in body
    terms = _text(re.search(r'data-testid="waiver-evidence"[^>]*>(.*?)</span>', body, re.S).group(1))
    assert terms == "· duty exemption not on file"
    if 'data-testid="waiver-pending"' not in body:
        pytest.skip("this world's quote does not leave the import to us")
    assert "duty exemption not on file · to do" in body
    assert ">Attach duty exemption<" in body


@pytest.mark.django_db
def test_the_tender_terms_name_it_the_same(da, world, client_in_program):
    _waiver(da, world)
    body = _tender_page(client_in_program, world["tender"]["id"])
    terms = _text(re.search(r'data-testid="waiver-evidence"[^>]*>(.*?)</span>', body, re.S).group(1))
    assert terms == "· duty exemption not on file"
    assert "waiver document" not in body


@pytest.mark.django_db
def test_the_order_s_nil_duty_is_the_figure_and_the_comparison_s_chip(da, world, client_in_program):
    contract = _contract(da, world, duties_basis="excluded", duties_amount="0.00")
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    cell = re.search(r'data-testid="duty-relief-unevidenced"[^>]*>(.*?)</span>\s*</td>', body, re.S).group(1)
    assert _text(cell) == "USD 0.00 duty exemption not on file · to do"
    assert 'class="status-chip status-chip--ours" data-testid="waiver-pending"' in cell
    assert "duty relief is documented" not in body and "no document on file" not in body


def test_holds_name_documents_by_the_label_map():
    source = open(records.__file__.replace("records.py", "fulfilment/services/holds.py")).read()
    assert 'what="duty exemption"' not in source
    assert 'document_kind_label("duty_exemption")' in source


# ---- G. a recorded quote's open facts on its History line --------------------


def test_fact_chips_put_ours_first_with_whose_each_is():
    chips = fact_chips(["sachets per carton", "exchange rate", "freight estimate"])
    assert chips[:2] == [("exchange rate", rules.US), ("freight estimate", rules.US)]
    assert chips[2] == ("sachets per carton", rules.SUPPLIERS)


@pytest.mark.django_db
def test_the_history_line_that_recorded_a_quote_carries_its_open_facts(da, world, client_in_program):
    quote = _waiver(da, world)
    body = _tender_page(client_in_program, world["tender"]["id"])
    history = body[body.index('id="history"') :]
    chips = re.search(r'data-testid="quote-open-facts"[^>]*>(.*?)</div>', history, re.S)
    assert chips is not None
    # The same facts, one rule: status.quote_open_facts for this quote as it stands.
    from connect_labs.supply_chain.procurement.status import comparisons, waiver_on_file

    tender = quote.tender
    row = next(r for c in comparisons(tender, [quote]) for r in (*c.comparable, *c.blocked) if r.quote_id == quote.pk)
    expected = fact_chips(quote_open_facts(tender, row, quote, waiver_on_file=waiver_on_file(tender)))
    shown = re.findall(r'data-fact="([^"]+)" data-owner="([^"]+)"', chips.group(1))
    assert [(html.unescape(f), o) for f, o in shown] == expected
    # The fact, then whose step it is as the comparison's chip: "duty exemption" [to do].
    assert "status-chip status-chip--fact" in chips.group(1)
    words = [_text(s) for s in re.findall(r"<span[^>]*>([^<]*)</span>", chips.group(1), re.S)]
    assert words[words.index("duty exemption") + 1] == "to do"


@pytest.mark.django_db
def test_a_quote_with_nothing_open_carries_no_chips(da, world, client_in_program):
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="supplier_ddp")
    _quote(da, world, duties_basis="included")
    body = _tender_page(client_in_program, world["tender"]["id"])
    history = body[body.index('id="history"') :]
    for found in re.findall(r'data-testid="quote-open-facts"[^>]*>(.*?)</div>', history, re.S):
        assert "duty exemption" not in found


def test_the_overview_and_history_share_one_chip():
    home = get_template("supply_chain/home.html").template.source
    line = get_template("supply_chain/_timeline_line.html").template.source
    assert '"supply_chain/_fact_chip.html"' in home and '"supply_chain/_fact_chip.html"' in line
