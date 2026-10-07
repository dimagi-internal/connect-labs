"""Sheets b3: the comparison says who is cheapest, the award is one block, the overview dates a tender.

1. The comparison's quotes read cheapest landed price first, the lowest marked and
   each other quote's difference from it in a column beside Landed; the quote's
   specification check and next moves ride in its own cell, not three columns of
   their own; ONE award block states the tender's open state once, and each
   comparable quote's Award link opens it with that quote chosen.
2. The procurements overview gives a tender row its deadline under the stage.
3. The tender page's Missing column has room for its chips, which wrap.
"""

import re
from datetime import date, timedelta

import pytest
from django.urls import reverse

from connect_labs.supply_chain.models import Award
from connect_labs.supply_chain.standing import deadline_note
from connect_labs.supply_chain.tests import test_screens_polish as polish

account = polish.account
client_in_programme = polish.client_in_programme
da = polish.da
world = polish.world
op = polish.op
_compare_url = polish._compare_url

pytestmark = pytest.mark.django_db

TODAY = date.today()


def _quote(da, world, name, price, **extra):
    supplier = op(da, "supplier_create", data={"name": name})
    return op(
        da,
        "quote_record",
        data={
            "tender_id": world["tender"]["id"],
            "commodity_slug": "chlorine",
            "supplier_id": supplier["id"],
            "as_quoted_amount": price,
            "as_quoted_unit": "per_pack",
            "quantity_basis": "600",
            "quantity_basis_unit": "jerry_can",
            "pack_spec_source": "stated_on_quote",
            "base_per_pack_stated": 20,
            "freight_basis": "included",
            "duties_basis": "included",
            **extra,
        },
    )


@pytest.fixture
def three(da, world):
    """Three quotes: two comparable, the cheaper one alphabetically last, and one not comparable."""
    op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
    return {
        "alpha": _quote(da, world, "Alpha chemicals", "4.60", incoterm="DDP Kano"),
        "zulu": _quote(da, world, "Zulu chemicals", "4.10", incoterm="DDP Kano"),
        "mid": _quote(da, world, "Mid chemicals", "3.00", freight_basis="not_specified", incoterm=""),
        "world": world,
    }


def _grid(body):
    return re.search(r'<table [^>]*data-testid="comparison-grid".*?</table>', body, re.S).group(0)


def _row_ids(grid):
    return [int(i) for i in re.findall(r'<tr data-quote-id="(\d+)" data-testid="grid-quote-row"', grid)]


def _cell(grid, quote_id, fact):
    return re.search(rf'<td [^>]*data-fact="{fact}" data-quote-id="{quote_id}"[^>]*>(.*?)</td>', grid, re.S).group(0)


class TestCheapestFirst:
    def test_comparable_quotes_lead_lowest_landed_first_then_the_rest(self, client_in_programme, three):
        grid = _grid(client_in_programme.get(_compare_url(three["world"])).content.decode())
        # Zulu (4.10) before Alpha (4.60) though Alpha comes first by name; Mid is not comparable, last.
        assert _row_ids(grid) == [three["zulu"]["id"], three["alpha"]["id"], three["mid"]["id"]]

    def test_the_lowest_is_marked_and_the_others_say_how_far_above(self, client_in_programme, three):
        grid = _grid(client_in_programme.get(_compare_url(three["world"])).content.decode())
        assert 'data-testid="landed-lowest"' in _cell(grid, three["zulu"]["id"], "landed")
        assert "landed-lowest" not in _cell(grid, three["alpha"]["id"], "landed")
        assert re.search(r'<th scope="col" data-fact="vs_lowest"[^>]*>Above lowest<span[^>]*>per jerry can<', grid)
        assert re.search(
            r">\s*\+ USD 0\.50\s*<", _cell(grid, three["alpha"]["id"], "vs_lowest").replace("</span>", "")
        )
        assert "USD" not in _cell(grid, three["zulu"]["id"], "vs_lowest")
        assert "USD" not in _cell(grid, three["mid"]["id"], "vs_lowest")
        # Landed is the first figure, the difference straight after it.
        heads = re.findall(r'<th scope="col" data-fact="(\w+)"', grid)
        assert heads[:2] == ["landed", "vs_lowest"]

    def test_one_comparable_quote_is_not_a_ranking(self, client_in_programme, da, world):
        op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
        only = _quote(da, world, "Alpha chemicals", "4.60")
        grid = _grid(client_in_programme.get(_compare_url(world)).content.decode())
        assert 'data-fact="vs_lowest"' not in grid
        assert "landed-lowest" not in _cell(grid, only["id"], "landed")


class TestTheQuoteCellCarriesItsMoves:
    def test_no_specification_to_do_or_waiting_columns(self, client_in_programme, three):
        grid = _grid(client_in_programme.get(_compare_url(three["world"])).content.decode())
        heads = re.findall(r"<th scope=\"col\"[^>]*>([^<]+)", re.search(r"<thead>.*?</thead>", grid, re.S).group(0))
        assert "Specification" not in heads and "To do" not in heads and "Waiting on supplier" not in heads
        # The not-comparable quote's moves sit in its own (pinned) cell, as chips by whose they are.
        pinned = re.search(rf'<th scope="row" data-quote-id="{three["mid"]["id"]}".*?</th>', grid, re.S).group(0)
        assert 'data-testid="grid-action"' in pinned
        assert 'data-testid="grid-award"' not in pinned


class TestTheGridIsAsNarrowAsItsFigures:
    def test_who_imports_reads_under_the_term_and_the_price_unit_under_the_amount(self, client_in_programme, three):
        grid = _grid(client_in_programme.get(_compare_url(three["world"])).content.decode())
        heads = re.findall(r'<th scope="col" data-fact="(\w+)"', grid)
        assert "imports" not in heads
        term = _cell(grid, three["zulu"]["id"], "term")
        assert re.search(r'data-testid="grid-imports" data-fact="imports">supplier imports<', term)
        price = _cell(grid, three["zulu"]["id"], "price")
        assert "USD 4.10" in price and '<span class="sub">per jerry can</span>' in price
        assert " / " not in price
        # A term not stated says nothing of who imports beneath it: that follows from the term.
        assert "grid-imports" not in _cell(grid, three["mid"]["id"], "term")


class TestOneAwardBlock:
    def test_one_block_states_the_open_state_once(self, client_in_programme, three):
        body = client_in_programme.get(_compare_url(three["world"])).content.decode()
        assert body.count('data-testid="award-start"') == 1
        assert body.count('data-testid="award-open"') == 1
        assert body.count("data-anyway") == 1
        # It offers both comparable quotes, cheapest first, and starts on the cheapest.
        form = re.search(r'<form [^>]*data-testid="award-form".*?</form>', body, re.S).group(0)
        options = re.findall(r'<option value="(\d+)"( selected)?>([^<]*)</option>', form)
        assert [int(v) for v, _, _ in options] == [three["zulu"]["id"], three["alpha"]["id"]]
        assert options[0][1] == " selected" and "USD 4.10" in options[0][2]

    def test_each_comparable_row_links_to_the_block_with_its_quote(self, client_in_programme, three):
        grid = _grid(client_in_programme.get(_compare_url(three["world"])).content.decode())
        links = re.findall(r'data-testid="grid-award" data-quote-id="(\d+)" href="([^"]+)"', grid)
        assert [int(q) for q, _ in links] == [three["zulu"]["id"], three["alpha"]["id"]]
        assert all(href.endswith(f"award={q}#award") for q, href in links)

    def test_the_link_opens_the_block_on_that_quote(self, client_in_programme, three):
        url = _compare_url(three["world"]) + f"&award={three['alpha']['id']}"
        body = client_in_programme.get(url).content.decode()
        assert re.search(r'<details [^>]*data-testid="award-start" open>', body)
        assert f'<option value="{three["alpha"]["id"]}" selected>' in body
        assert f'<option value="{three["zulu"]["id"]}" selected>' not in body

    def test_a_quote_that_cannot_be_awarded_is_not_chosen(self, client_in_programme, three):
        url = _compare_url(three["world"]) + f"&award={three['mid']['id']}"
        body = client_in_programme.get(url).content.decode()
        assert f'<option value="{three["mid"]["id"]}"' not in body
        assert f'<option value="{three["zulu"]["id"]}" selected>' in body

    def test_the_chosen_quote_is_awarded(self, client_in_programme, three):
        client_in_programme.post(
            _compare_url(three["world"]),
            {"quote_id": three["alpha"]["id"], "rationale": "registered locally", "decided_on": TODAY.isoformat()},
        )
        assert Award.objects.filter(quote_id=three["alpha"]["id"]).exists()
        assert not Award.objects.filter(quote_id=three["zulu"]["id"]).exists()


# ---- 2. the overview dates a tender ---------------------------------------


class TestTheOverviewDatesATender:
    def test_the_words(self):
        day = TODAY + timedelta(days=4)
        assert deadline_note(day, TODAY) == f"deadline {day.day} {day.strftime('%b')} · in 4 days"
        assert deadline_note(TODAY, TODAY).endswith(" · today")
        assert deadline_note(TODAY - timedelta(days=1), TODAY).endswith(" · passed 1 day ago")
        assert deadline_note(None, TODAY) == ""

    def _row(self, client, tender_id):
        body = client.get(reverse("supply_chain:home")).content.decode()
        return re.search(
            rf'<tr data-testid="overview-row" data-kind="tender" data-tender-id="{tender_id}".*?</tr>', body, re.S
        ).group(0)

    def test_an_open_tender_shows_its_deadline_under_the_stage(self, client_in_programme, da, world):
        day = TODAY + timedelta(days=4)
        tender = op(
            da,
            "tender_create",
            data={
                "label": "Soap",
                "delivery_point": {"city": "Kano"},
                "response_deadline": day.isoformat(),
                "lines": [{"commodity_slug": "chlorine", "quantity": "10", "quantity_unit": "jerry_can"}],
            },
        )
        op(da, "tender_open", tender_id=tender["id"])
        stage = re.search(
            r'data-testid="row-stage">.*?</span></span>', self._row(client_in_programme, tender["id"]), re.S
        )
        assert f'data-testid="row-deadline">deadline {day.day} {day.strftime("%b")} · in 4 days<' in stage.group(0)

    def test_a_passed_deadline_on_an_open_tender_is_said_once_in_its_decide_move(self, client_in_programme, da, world):
        tender = op(
            da,
            "tender_create",
            data={
                "label": "Soap",
                "delivery_point": {"city": "Kano"},
                "response_deadline": (TODAY - timedelta(days=3)).isoformat(),
                "lines": [{"commodity_slug": "chlorine", "quantity": "10", "quantity_unit": "jerry_can"}],
            },
        )
        op(da, "tender_open", tender_id=tender["id"])
        row = self._row(client_in_programme, tender["id"])
        assert 'data-testid="row-deadline"' not in row
        assert "deadline passed" in row


# ---- 3. the Missing column has room ------------------------------------------


def test_the_missing_column_has_a_min_width_and_its_chips_wrap():
    """Chip by chip: each chip stays whole on one line (status-chip is nowrap), the row wraps."""
    from django.template.loader import get_template

    source = get_template("supply_chain/procurement/tender_detail.html").template.source
    assert '<th scope="col" class="min-w-[15rem]">Missing</th>' in source
    cell = re.search(
        r'<td class="min-w-\[15rem\]" data-testid="supplier-missing-cell">.*?data-testid="supplier-missing"',
        source,
        re.S,
    )
    assert cell and "flex-wrap" in cell.group(0) and "text-wrap" not in cell.group(0)


# ---- 4. the answer stays in view while the facts scroll --------------------------


def test_the_landed_price_and_its_difference_are_pinned_beside_the_supplier(client_in_programme, three):
    """Editing a fact far right scrolls the grid; the landed price must not slide under the supplier."""
    grid = _grid(client_in_programme.get(_compare_url(three["world"])).content.decode())
    head = re.search(r"<thead>.*?</thead>", grid, re.S).group(0)
    assert re.search(r'<th scope="col" class="sheet-pin">Supplier</th>', head)
    assert re.search(r'<th scope="col" data-fact="landed" class="[^"]*\bsheet-pin-2\b', head)
    assert re.search(r'<th scope="col" data-fact="vs_lowest" class="[^"]*\bsheet-pin-3\b', head)
    for quote in ("zulu", "alpha", "mid"):
        qid = three[quote]["id"]
        assert re.search(r'class="[^"]*\bsheet-pin-2\b', _cell(grid, qid, "landed"))
        assert re.search(r'class="[^"]*\bsheet-pin-3\b', _cell(grid, qid, "vs_lowest"))
        # Only the answer is held; the facts behind it scroll.
        assert "sheet-pin" not in _cell(grid, qid, "fx")
    css = open("tailwind/tailwind.css").read()
    first = "".join(re.findall(r"\.sheet \.sheet-pin \{(.*?)\}", css, re.S))
    second = "".join(re.findall(r"\.sheet \.sheet-pin-2 \{(.*?)\}", css, re.S))
    third = "".join(re.findall(r"\.sheet \.sheet-pin-3 \{(.*?)\}", css, re.S))
    # Each held column's offset is the widths before it, so the supplier's width is fixed.
    assert "width: var(--sheet-pin-1)" in first and "max-width: var(--sheet-pin-1)" in first
    assert "left: var(--sheet-pin-1)" in second
    assert "left: calc(var(--sheet-pin-1) + var(--sheet-pin-2))" in third
    shared = re.search(r"\.sheet \.sheet-pin,\s*\.sheet \.sheet-pin-2,\s*\.sheet \.sheet-pin-3 \{(.*?)\}", css, re.S)
    assert shared and "sticky" in shared.group(1) and "bg-white" in shared.group(1)


def test_without_a_ranking_only_the_landed_price_is_pinned(client_in_programme, da, world):
    grid = _grid(client_in_programme.get(_compare_url(world)).content.decode())
    assert re.search(r'data-fact="landed" class="[^"]*\bsheet-pin-2\b', grid)
    assert "sheet-pin-3" not in grid
