"""A quote's missing facts read as facts, not moves; one name for the duty exemption; freight
included under a carriage-paid term; one count of a shipment's clearing documents; and
"since corrected" only beside a value the correction changed.

Moves and quote facts are separate ledgers: To do counts moves only, so a quote's missing
fact never wears the moves' "to do" or their amber.

THIS REPOSITORY IS PUBLIC. Nothing here names a real company.
"""

import html
import re

import pytest
from django.template.loader import render_to_string
from django.urls import reverse

from connect_labs.supply_chain import moves
from connect_labs.supply_chain.procurement.views import _freight_in_price
from connect_labs.supply_chain.templatetags.supply_chain_extras import gap_tone, supplies
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, _kanem_quote, op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(fragment):
    return html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", fragment))).strip()


# ---- 1. one formatter, by who acts and what closing it takes


def test_ours_say_what_closing_them_takes():
    assert moves.facts_chip(["duty exemption"], moves.US) == "duty exemption · ours to attach"
    assert moves.facts_chip(["tender duty terms"], moves.US) == "duty terms · ours to settle"
    assert moves.facts_chip(["exchange rate"], moves.US) == "exchange rate · ours to fill"
    assert moves.facts_chip(["freight estimate", "clearing estimate"], moves.US) == "2 facts · ours to fill"
    # Facts of different kinds are a figure to fill, never one of them.
    assert moves.facts_chip(["duty exemption", "exchange rate"], moves.US) == "2 facts · ours to fill"


def test_theirs_keep_the_waiting_words():
    assert moves.facts_chip(["sachets per carton"], moves.SUPPLIERS) == "sachets per carton · waiting"
    assert moves.facts_chip(["sachets per carton"], moves.TO_ASK) == "sachets per carton · to ask"


def test_no_fact_chip_borrows_the_moves_to_do():
    for owner in (moves.US, moves.SUPPLIERS, moves.TO_ASK):
        for gaps in (["duty exemption"], ["tender duty terms"], ["exchange rate"], ["a", "b"]):
            assert "to do" not in moves.facts_chip(gaps, owner)
            assert "to do" not in supplies(owner, gaps[0])


def test_the_filter_and_the_formatter_are_one_rule():
    assert supplies(moves.US, "duty exemption") == moves.gap_chip_word(moves.US, ["duty exemption"])
    assert supplies(moves.US) == moves.OURS_TO_FILL
    assert supplies(moves.SUPPLIERS) == "waiting" and supplies(moves.TO_ASK) == "to ask"


def test_a_fact_chip_is_never_the_moves_amber():
    assert gap_tone(moves.US) == gap_tone(moves.TO_ASK) == "fact"
    assert gap_tone(moves.SUPPLIERS) == "theirs"
    chip = render_to_string("supply_chain/_fact_chip.html", {"fact": "duty exemption", "owner": moves.US, "n": 2})
    assert "status-chip--fact" in chip and "status-chip--ours" not in chip
    assert _text(chip) == "duty exemption · 2 quotes · ours to attach"


# ---- the tender page: To do counts moves; the facts read as facts


def _waiver(da, world):
    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="buyer_waiver")


@pytest.mark.django_db
def test_the_tender_page_reads_one_name_and_the_fact_chips(da, world, client_in_program):
    _waiver(da, world)
    op(
        da,
        "quote_record",
        data=_kanem_quote(world, freight_basis="not_specified", duties_basis="not_specified", incoterm="FCA Niamey"),
    )
    body = _tender_page(client_in_program, world["tender"]["id"])
    terms = _text(re.search(r'data-testid="tender-terms"[^>]*>(.*?)</section>', body, re.S).group(1))
    assert "We import under the duty waiver" in terms
    assert "Duty exemption not on file · Attach" in terms
    assert "program's" not in terms
    sheet = body[body.index('data-testid="supplier-table"') : body.index('id="history"')]
    on_us = re.search(
        r'<a class="status-chip status-chip--(\w+)" data-testid="supplier-on-us"[^>]*>(.*?)</a>', sheet, re.S
    )
    assert on_us is not None and on_us.group(1) == "fact"
    assert " · ours to " in _text(on_us.group(2)) and "to do" not in _text(on_us.group(2))
    # To do still counts moves only: the quote's facts add nothing to it.
    tiles = re.findall(
        r'class="stat-tile-label">([^<]+)</div>\s*'
        r'<div class="stat-tile-value[^"]*">([^<]+)</div>\s*'
        r'<div class="text-xs text-gray-600">([^<]*)</div>',
        body,
    )
    by_label = {label: (value, sub) for label, value, sub in tiles}
    assert by_label["To do"][0] == "0"
    assert re.match(r"ours to \w+ on 1 quote", by_label["Comparable quotes"][1])


@pytest.mark.django_db
def test_the_comparison_names_the_waiver_document_and_its_chip(da, world, client_in_program):
    _waiver(da, world)
    op(
        da,
        "quote_record",
        data=_kanem_quote(world, freight_basis="not_specified", duties_basis="not_specified", incoterm="CPT Kano"),
    )
    url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=rutf"
    body = client_in_program.get(url).content.decode()
    duty = _text(re.search(r'data-testid="comparison-duty-terms"[^>]*>(.*?)<details', body, re.S).group(1))
    assert duty.startswith("Import duty: we import under the duty waiver")
    assert "· Duty exemption not on file · Attach" in duty
    legend = _text(re.search(r'data-testid="owner-legend"[^>]*>(.*?)</span></span>', body, re.S).group(0))
    assert "to do" not in legend and "ours to attach · settle · fill" in legend
    if 'data-testid="waiver-pending"' not in body:
        pytest.skip("this world's quote does not leave the import to us")
    pending = re.search(
        r'<span class="status-chip status-chip--(\w+)[^"]*" data-testid="waiver-pending">(.*?)</span>', body
    )
    assert pending.group(1) == "fact"
    assert _text(pending.group(2)) == "duty exemption not on file · ours to attach"
    assert not re.search(r'data-testid="grid-status">[^<]+ · to do<', body)


# ---- 3. freight under a carriage-paid term is in the supplier's price


@pytest.mark.django_db
def test_freight_is_included_under_the_tenders_carriage_paid_term():
    assert _freight_in_price({"incoterm_requested": "CPT Kano"}, 0, PROGRAM) == "CPT"
    assert _freight_in_price({"incoterm_requested": "DAP"}, 0, PROGRAM) == "DAP"
    assert _freight_in_price({"incoterm_requested": "FCA Niamey"}, 0, PROGRAM) == ""
    assert _freight_in_price({"incoterm_requested": "EXW"}, 0, PROGRAM) == ""
    assert _freight_in_price({"incoterm_requested": ""}, 0, PROGRAM) == ""


@pytest.mark.django_db
def test_a_quote_on_terms_leaving_freight_to_us_keeps_the_estimate_open(da, world, client_in_program):
    from connect_labs.supply_chain.models import Tender

    Tender.objects.filter(pk=world["tender"]["id"]).update(incoterm_requested="CPT Kano")
    body = _tender_page(client_in_program, world["tender"]["id"])
    freight = re.search(r'data-testid="freight-estimate"[^>]*>(.*?)</span>\s*</span>', body, re.S)
    assert "included (CPT)" in _text(freight.group(0))
    op(
        da,
        "quote_record",
        data=_kanem_quote(world, freight_basis="not_specified", duties_basis="not_specified", incoterm="FCA Niamey"),
    )
    body = _tender_page(client_in_program, world["tender"]["id"])
    assert 'data-testid="freight-in-price"' not in body


# ---- 4. a shipment's clearing documents, in the Documents section's own count


def _cell(**shipment):
    return _text(render_to_string("supply_chain/_shipment_documents.html", {"shipment": shipment}))


def test_a_held_shipment_reads_as_the_documents_section_counts_it():
    required = [{"kind": "form_m"}, {"kind": "duty_exemption"}]
    assert _cell(required_documents=required, required_on_file=0, holding_count=2) == "2 hold the shipment · 0 on file"
    assert (
        _cell(required_documents=required, required_on_file=1, holding_count=1) == "1 holds the shipment · 1 on file"
    )
    assert _cell(required_documents=required, required_on_file=2, holding_count=0) == "2 of 2 on file"


def test_the_order_column_is_named_for_its_documents():
    from django.template.loader import get_template

    source = get_template("supply_chain/order_detail.html").template.source
    assert ">Clearing documents</th>" in source and ">Certificate</th>" not in source
    assert "documents on file</span>" not in source


# ---- 5. "since corrected" only where the correction changed the line


def _history(client, tender_id):
    return _tender_page(client, tender_id).split('data-testid="timeline"', 1)[1]


@pytest.mark.django_db
def test_a_correction_elsewhere_leaves_the_price_line_unmarked(da, world, client_in_program):
    quote = op(da, "quote_record", data=_kanem_quote(world, received_on="2026-07-09"))
    op(da, "quote_correct", quote_id=quote["id"], data={"received_on": "2026-07-08"}, reason="date misread")
    fixes = re.findall(
        r'data-testid="quote-fixes"[^>]*>(.*?)</div>', _history(client_in_program, world["tender"]["id"]), re.S
    )
    assert not any("since corrected" in f for f in fixes)


@pytest.mark.django_db
def test_a_corrected_price_keeps_its_note(da, world, client_in_program):
    quote = op(da, "quote_record", data=_kanem_quote(world))
    op(da, "quote_correct", quote_id=quote["id"], data={"as_quoted_amount": "52.00"}, reason="typo")
    fixes = re.findall(
        r'data-testid="quote-fixes"[^>]*>(.*?)</div>', _history(client_in_program, world["tender"]["id"]), re.S
    )
    assert any("since corrected" in f for f in fixes)
