"""The unanswered round, DDD 005 batch 3: the overview's counts say what they count; owed names its counterparty.

THIS REPOSITORY IS PUBLIC. Every name, address and figure below is invented,
and every address uses `.example.invalid`.

Builds on test_tracking_reality's world.
"""

import re

from django.template import Context, Template
from django.urls import reverse

from connect_labs.supply_chain.fulfilment.services.holds import holds_on_us
from connect_labs.supply_chain.models import Contract
from connect_labs.supply_chain.moves import SUPPLIERS, US, Move
from connect_labs.supply_chain.standing import Row, move_counts, standing_rows
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, TODAY, _held_on_our_form_m

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _row(ours=(), theirs=()):
    return Row(
        kind="order",
        title="t",
        url="/",
        stage_index=5,
        stage="s",
        last_change_at=None,
        last_change_by="",
        last_change_is_ai=False,
        ours=list(ours),
        theirs=list(theirs),
    )


def _move(whose, text):
    return Move(whose, "owed" if whose == US else "no reply", text)


class TestMoveCounts:
    def test_counts_every_move_on_each_side(self):
        rows = [
            _row([_move(US, "a"), _move(US, "b")], [_move(SUPPLIERS, "x")]),
            _row([_move(US, "c")]),
        ]
        assert move_counts(rows) == {"ours": 3, "theirs": 1}

    def test_a_row_with_no_moves_counts_nothing(self):
        assert move_counts([_row(), _row()]) == {"ours": 0, "theirs": 0}

    def test_the_overview_heading_carries_the_count_its_rows_give(self, da, world, client_in_program):
        _held_on_our_form_m(da, world)
        expected = move_counts(standing_rows(PROGRAM, TODAY))["ours"]
        assert expected >= 1
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        count = _text(re.search(r'data-testid="standing-our-moves">(.*?)</div>', body, re.S).group(1))
        assert count.startswith(f"{expected} move")
        assert count.endswith("on you")


class TestMoneyStaysOnOneLine:
    def test_an_amount_cannot_break_between_currency_and_figure(self):
        html = Template("{% load supply_chain_extras %}{{ v|nowrap_money }}").render(
            Context({"v": "dispute the invoice above the agreed price (USD 3,550.00 above)"})
        )
        assert '<span class="whitespace-nowrap">USD 3,550.00</span>' in html
        assert re.sub(r"<[^>]+>", "", html) == "dispute the invoice above the agreed price (USD 3,550.00 above)"

    def test_text_is_still_escaped(self):
        html = Template("{% load supply_chain_extras %}{{ v|nowrap_money }}").render(Context({"v": "<b>x</b>"}))
        assert "<b>" not in html


class TestOwedNamesItsCounterparty:
    def test_a_held_document_says_who_asked_for_it(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        (hold,) = holds_on_us(Contract.objects.get(pk=contract["id"]))
        assert hold.asked_by == "Crescent Rehearsal Freight"
        # Who asked is not who it is owed to: the overview's words are unchanged.
        assert hold.words == "import permit"

    def test_the_order_page_heading_names_the_forwarder(self, da, world, client_in_program):
        contract, _ = _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:order_detail", args=[contract["id"]])).content.decode()
        heading = _text(re.search(r'<h2 id="owed" data-testid="owed-heading".*?</h2>', body, re.S).group(0))
        assert heading.startswith("What we owe — to clear the shipment (via Crescent Rehearsal Freight)")
        assert "What we owe them" not in heading
