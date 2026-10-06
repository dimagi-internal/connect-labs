"""DDD supply-sophie-sheets, batch 2: fewer words on the tender page, as structure.

- History: identical bookkeeping lines on one day fold into ONE line that counts its records
  ("Outreach · recorded: sent 19 Sep · 6 suppliers"), the suppliers listed under the count as
  chips -- not run together into a 22-word line. A line about the page's own record leaves its
  name off. On the tender page a recorded quote is named by its price: the sheets above carry
  its terms and the facts it lacks.
- Waiting on suppliers leaves asked/chased dates to the Suppliers sheet beside it.
- The overview's comparable chip is the count; the names wrap beneath it, inside the column.
- The Suppliers sheet's actions column is pinned to the right edge, so it is never clipped.
"""

import html
import re
from datetime import datetime

import pytest
from django.template.loader import get_template, render_to_string
from django.urls import reverse

from connect_labs.supply_chain.history.timeline import Entry, _fold_bookkeeping, _unname_own
from connect_labs.supply_chain.models import Tender
from connect_labs.supply_chain.templatetags.supply_chain_extras import record_kind_lead, record_kind_lead_price
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import op
from connect_labs.supply_chain.tests.test_unanswered_round_batch1 import _tender_page
from connect_labs.supply_chain.tests.test_unanswered_round_v11_duty_terms import _quote

da = reality.da
world = reality.world
client_in_program = reality.client_in_program

WHEN = datetime(2026, 9, 19, 10, 0)


def _text(fragment):
    return html.unescape(" ".join(re.sub(r"<[^>]+>", " ", str(fragment)).split()))


def _sent(name, actor="Sophie", when=WHEN):
    return Entry(
        when=when,
        sentence=f"Outreach · {name} · recorded: sent 19 Sep",
        actor=actor,
        is_ai=False,
        excerpt="",
        source_ref="",
        entity="Outreach",
        identity=name,
        what="recorded: sent 19 Sep",
        bookkeeping=True,
    )


# ---- History: a fold counts, and lists its records as chips --------------------------


def test_same_day_invitations_fold_into_one_counted_line():
    names = ["Savanna Ready Foods", "Lagoon Nutripharm", "Sahel Nutrition Industries"]
    folded = _fold_bookkeeping([_sent(n) for n in names])
    assert len(folded) == 1
    assert folded[0].identity == "3 suppliers"
    assert folded[0].members == names
    assert folded[0].line == "Outreach · 3 suppliers · recorded: sent 19 Sep"


def test_a_lone_line_and_another_person_s_line_do_not_fold():
    folded = _fold_bookkeeping([_sent("Kanem Foods Ltd"), _sent("Lagoon Nutripharm", actor="Grace")])
    assert [e.identity for e in folded] == ["Kanem Foods Ltd", "Lagoon Nutripharm"]
    assert [e.members for e in folded] == [[], []]


def test_a_folded_line_renders_short_with_each_supplier_a_chip():
    names = ["Savanna Ready Foods", "Lagoon Nutripharm", "Sahel Nutrition Industries", "Northgate Commodities"]
    entry = _fold_bookkeeping([_sent(n) for n in names])[0]
    out = render_to_string("supply_chain/_timeline_line.html", {"entry": entry, "in_event": False})
    text = re.search(r'data-testid="revision-text"[^>]*>(.*?)</div>', out, re.S).group(1)
    # The line itself, without the list the count opens: a few words, not one per supplier.
    line = _text(re.sub(r"<ul.*?</ul>", "", text, flags=re.S))
    assert line == "Outreach · recorded: sent 19 Sep · 4 suppliers"
    chips = [_text(c) for c in re.findall(r'data-testid="folded-member"[^>]*>(.*?)</li>', out, re.S)]
    assert chips == names


def test_a_line_about_the_page_s_own_record_leaves_its_name_off():
    entry = Entry(
        when=WHEN,
        sentence="Status draft → open",
        actor="Sophie",
        is_ai=False,
        excerpt="",
        source_ref="",
        entity="Tender",
        identity="RUTF tender 2: 2,000 cartons to Kano",
        what="Status draft → open",
        object_key=(Tender, "16"),
    )
    _unname_own(entry, (Tender, 17))
    assert entry.identity == "RUTF tender 2: 2,000 cartons to Kano"
    _unname_own(entry, (Tender, 16))
    assert entry.line == "Tender · Status draft → open"


# ---- History on the tender page: a quote by its price ---------------------------------

QUOTE_LINE = (
    "Quote · Sahel Co · recorded: EUR 0.31 per sachet (EXW Niamey: freight and duty excluded)"
    " — you asked CPT Kano · 150 x 92 g sachets per carton · valid to 2 Nov 2026 · lead time 5 weeks"
)


def test_the_price_only_lead_keeps_the_price_and_drops_the_terms():
    full = str(record_kind_lead(QUOTE_LINE, "A Person, Sahel Co"))
    brief = str(record_kind_lead_price(QUOTE_LINE, "A Person, Sahel Co"))
    assert 'data-testid="quote-term"' in full
    assert 'data-testid="quote-term"' not in brief
    assert _text(brief) == "Quote · EUR 0.31 per sachet"


@pytest.mark.django_db
def test_the_tender_page_history_names_a_quote_by_its_price_and_no_fact_chips(da, world, client_in_program):
    source = get_template("supply_chain/procurement/tender_detail.html").template.source
    assert "history_quote_price_only=True" in source
    _quote(da, world)
    body = _tender_page(client_in_program, world["tender"]["id"])
    history = body[body.index('id="history"') :]
    assert 'data-testid="quote-term"' not in history
    assert 'data-fact="' not in history


@pytest.mark.django_db
def test_the_tender_page_history_does_not_name_the_tender_on_its_lines(da, world, client_in_program):
    tender = Tender.objects.get(pk=world["tender"]["id"])
    op(da, "tender_set_duty_terms", tender_id=tender.pk, duty_terms="supplier_ddp")
    body = _tender_page(client_in_program, tender.pk)
    history = body[body.index('id="history"') :]
    lines = [_text(t) for t in re.findall(r'data-testid="revision-text"[^>]*>(.*?)</div>', history, re.S)]
    tender_lines = [line for line in lines if line.startswith("Tender ·")]
    assert tender_lines, lines
    assert all(tender.label not in line for line in tender_lines)


# ---- Waiting on suppliers: dates are the sheet's ---------------------------------------


@pytest.mark.django_db
def test_waiting_on_suppliers_leaves_the_dates_to_the_suppliers_sheet(da, world, client_in_program):
    body = _tender_page(client_in_program, world["tender"]["id"])
    waiting = re.search(r'data-testid="on-suppliers".*?</section>', body, re.S).group(0)
    assert 'data-testid="move"' in waiting
    assert 'data-testid="move-detail"' not in waiting
    assert "asked " not in _text(waiting)
    # The To do list beside it keeps its detail.
    source = get_template("supply_chain/_moves.html").template.source
    assert "{% if not no_detail %}" in source


# ---- The overview: the count is the chip, the names wrap under it ----------------------


@pytest.mark.django_db
def test_the_overview_comparable_chip_is_the_count_and_the_names_wrap_beneath(da, world, client_in_program):
    from connect_labs.supply_chain.standing import _tender_rows

    op(da, "tender_set_duty_terms", tender_id=world["tender"]["id"], duty_terms="supplier_ddp")
    _quote(da, world, duties_basis="included")
    row = next(r for r in _tender_rows(reality.PROGRAM, None, None) if r.tender_id == world["tender"]["id"])
    assert row.comparable_chip
    body = client_in_program.get(reverse("supply_chain:home")).content.decode()
    chip = _text(re.search(r'data-testid="row-comparable"[^>]*>(.*?)</span>', body, re.S).group(1))
    count, _, names = row.comparable_chip.partition(" · ")
    assert chip == count and "·" not in chip
    if names:
        shown = _text(re.search(r'data-testid="row-comparable-names"[^>]*>(.*?)</span>', body, re.S).group(1))
        assert shown == names


# ---- The Suppliers sheet: actions pinned to the right ----------------------------------


def test_the_suppliers_sheet_pins_its_actions_column_right():
    source = get_template("supply_chain/procurement/tender_detail.html").template.source
    assert '<th scope="col" class="sheet-pin-end' in source
    assert '<td class="sheet-pin-end" data-testid="supplier-actions">' in source
    css = open("tailwind/tailwind.css").read()
    rule = re.search(r"\.sheet \.sheet-pin-end \{(.*?)\}", css, re.S).group(1)
    assert "sticky" in rule and "right-0" in rule and "whitespace-nowrap" in rule
