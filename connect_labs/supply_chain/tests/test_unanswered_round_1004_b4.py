"""DDD unanswered round 2026-10-04, batch 4.

D: one document, one name. The duty exemption a waived duty rests on is named from the
Document kind (records.DOCUMENT_KIND_LABELS) on every page -- the comparison's chips and
actions, the tender's terms, an order's landed table and what-we-owe, the overview's gap
tags -- and the order's nil duty takes the comparison's cell shape: the figure and a chip.

G (superseded in the sheets round): a quote's open facts are the Suppliers sheet's, not
repeated on its History line.
"""

import html
import re

import pytest
from django.template.loader import get_template
from django.urls import reverse

from connect_labs.supply_chain import records
from connect_labs.supply_chain.models import Document
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
    # Attached once, on the tender's duty line; each quote's column points there.
    assert 'data-testid="duty-exemption-attach"' in body and 'data-testid="grid-terms-link"' in body


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


# ---- G. a recorded quote's open facts: on the Suppliers sheet, not again in History ----
# (Superseded in the sheets round: the Suppliers sheet's Missing column now carries the same
# chips, so History stopped repeating them.)


@pytest.mark.django_db
def test_a_quote_s_open_facts_are_the_suppliers_sheet_s_not_history_s(da, world, client_in_program):
    _waiver(da, world)
    body = _tender_page(client_in_program, world["tender"]["id"])
    history = body[body.index('id="history"') :]
    assert 'data-fact="' not in history
    sheet = body[body.index('data-testid="supplier-table"') : body.index('id="history"')]
    # Ours are the sheet's "to do" chip, linking to the comparison that names each one.
    on_us = _text(re.search(r'data-testid="supplier-on-us"[^>]*>(.*?)</a>', sheet, re.S).group(1))
    assert on_us.endswith("· to do")
    assert on_us == "duty exemption · to do" or re.match(r"\d+ facts · to do$", on_us)


def test_the_overview_and_the_suppliers_sheet_share_one_chip():
    home = get_template("supply_chain/home.html").template.source
    sheet = get_template("supply_chain/procurement/tender_detail.html").template.source
    assert '"supply_chain/_fact_chip.html"' in home and '"supply_chain/_fact_chip.html"' in sheet
