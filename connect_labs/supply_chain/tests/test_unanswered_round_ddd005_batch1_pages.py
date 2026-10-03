"""Unanswered round, DDD 005 batch 1: the recorded quote's terms as labelled chips, its
stated pack, one reply label, a "just recorded" tag, named required documents, and the
invoice variance read unit price, freight, then Total -- each above agreed tagged."""

import re

from django.template.loader import render_to_string

from connect_labs.supply_chain.history.labels import _required_documents_clause
from connect_labs.supply_chain.templatetags.supply_chain_extras import record_kind_lead


def _text(html):
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", str(html))).strip()


def test_a_recorded_quote_leads_with_its_price_then_lists_its_terms():
    line = (
        "Quote · Sahel Co · recorded: EUR 0.31 per sachet (EXW Niamey: freight and duty excluded)"
        " — you asked CPT Kano · 150 x 92 g sachets per carton · valid to 2 Nov 2026"
        " · lead time 5 weeks · minimum order 500 cartons · shelf life 24 months"
    )
    html = str(record_kind_lead(line, "A Person, Sahel Co"))
    assert "recorded:" not in html
    price = re.search(r'<span data-testid="quote-price"[^>]*>(.*?)</span>', html, re.S).group(1)
    assert price == "EUR 0.31 per sachet"
    terms = [_text(c) for c in re.findall(r'<span data-testid="quote-term".*?</span></span>', html, re.S)]
    assert terms == [
        "Incoterm EXW Niamey: freight and duty excluded — you asked CPT Kano",
        "Pack 150 x 92 g sachets per carton",
        "Valid to 2 Nov 2026",
        "Lead time 5 weeks",
        "Minimum order 500 cartons",
        "Shelf life 24 months",
    ]


def test_an_owed_line_does_not_stack_colons():
    html = str(record_kind_lead("Owed · recorded: they asked: Who imports?", "X, Sahel Co"))
    assert _text(html) == "Owed · they asked: Who imports?"


def test_other_record_lines_are_not_chipped():
    html = str(record_kind_lead("Outreach · Sahel Co · Replied with a quote on 3 Oct 2026", "X, Sahel Co"))
    assert 'data-testid="quote-term"' not in html


def test_required_documents_are_named():
    new = [
        {"kind": "import_permit", "name": "Form M"},
        {"kind": "customs_declaration", "name": "PAAR"},
    ]
    assert (
        _required_documents_clause([], new)
        == "Required documents now: import permit (Form M), customs declaration (PAAR)"
    )
    assert _required_documents_clause(new, []) == "Required documents cleared"


def test_void_is_styled_as_a_link_like_correct():
    html = render_to_string(
        "supply_chain/_timeline_line.html",
        {
            "entry": {"line": "Quote recorded", "correct_url": "/c/", "void_url": "/v/"},
            "in_event": True,
            "event_sender": "",
        },
    )
    assert 'data-testid="quote-void" class="text-brand-indigo hover:underline"' in html


def test_the_changed_tag_says_just_recorded():
    html = render_to_string("supply_chain/procurement/_new_tag.html")
    assert ">just recorded</span>" in html


# ---- the invoice variance: unit price, freight, then Total, each tagged

import pytest  # noqa: E402
from django.urls import reverse  # noqa: E402

from connect_labs.supply_chain.tests import test_tracking_reality as reality  # noqa: E402
from connect_labs.supply_chain.tests.test_unanswered_round_ddd003_batch2 import _overbilled  # noqa: E402

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


@pytest.mark.django_db
def test_variance_rows_read_in_order_and_the_total_is_tagged(da, world, client_in_program):
    contract, _ = _overbilled(da, world)
    body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
    table = re.search(r'<table data-testid="invoice-variance-table".*?</table>', body, re.S).group(0)
    rows = [
        _text(r)
        for r in re.findall(r"<tr data-testid=\"invoice-(?:above-agreed-row|against-agreed)\".*?</tr>", table, re.S)
    ]
    assert [r.split()[0] for r in rows] == ["Unit", "Freight", "Total"]
    assert all(r.endswith("above agreed") for r in rows)
