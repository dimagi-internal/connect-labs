"""The Stock table's cells stay short enough to read as a table.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

An order still to arrive used to be one sentence inside the balance cell --
"expected: 100 cartons of E2E co-pack from Field Page E2E Distributor — no
date given · not counted as cover" -- which wrapped to eight lines in a 10rem
column, and the "was N days before GRN-..." note under days of stock did the
same. Cutting words did not fix it; the layout had to change. The order now
has its own "On order" column (how much, when, from whom: one short line
each) and the before-receipt note sits behind a disclosure whose summary is
just "was N days".

The numbers do not move: the balance cell holds the same figure it did, and
the order's figure is the one the network read gives.
"""

import re

import pytest

from connect_labs.supply_chain.tests import test_stock_page_honesty as honesty

da = honesty.da
world = honesty.world
scoped = honesty.scoped
_late_donation = honesty._late_donation
_stock_page = honesty._stock_page
_text = honesty._text
_row = honesty._row

pytestmark = pytest.mark.django_db


def _cell(html, label):
    """The text of the first body cell carrying data-label=<label>."""
    match = re.search(rf'<td[^>]*data-label="{re.escape(label)}"[^>]*>(.*?)</td>', html, re.S)
    assert match, f"no {label!r} cell"
    return _text(match.group(1)).strip()


class TestAnOrderHasItsOwnColumn:
    def test_the_balance_cell_holds_only_the_balance(self, scoped, da, world):
        _late_donation(world)
        html = _stock_page(scoped)
        ledger = _cell(html, "Ledger says")
        assert "A water donor" not in ledger
        assert "overdue" not in ledger
        # Same figure as the network read, order or no order.
        on_hand = _row(da, world)["on_hand"]
        from connect_labs.supply_chain.templatetags.supply_chain_extras import figure_text

        assert figure_text(on_hand) in ledger

    def test_the_order_reads_as_three_short_lines(self, scoped, da, world):
        _late_donation(world)
        html = _stock_page(scoped)
        (expected,) = re.findall(r"<div[^>]*data-expected[^>]*>(.*?)</div>", html, re.S)
        lines = [
            _text(s).strip()
            for s in re.findall(r'<(?:a|span) class="block[^"]*"[^>]*>(.*?)</(?:a|span)>', expected, re.S)
        ]
        assert len(lines) == 3, lines
        assert lines[0] == "400 jerry cans"
        assert lines[1].startswith("overdue since")
        assert lines[2] == "from A water donor"
        # "not counted as cover" is said once, in the column header, not per row.
        assert "not counted as cover" not in _text(expected)

    def test_a_point_with_nothing_on_order_shows_a_dash(self, scoped, world):
        assert _cell(_stock_page(scoped), "On order") == "—"
