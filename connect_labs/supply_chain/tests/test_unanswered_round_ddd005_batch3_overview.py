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
from connect_labs.supply_chain.standing import WAITING_ON_US, Flag, Row, our_moves, standing_rows
from connect_labs.supply_chain.tests import test_tracking_reality as reality
from connect_labs.supply_chain.tests.test_tracking_reality import PROGRAM, TODAY, _held_on_our_form_m

da = reality.da
world = reality.world
client_in_program = reality.client_in_program


def _text(html):
    return " ".join(re.sub(r"<[^>]+>", " ", html).split())


def _row(waiting_on="", waiting_lines=()):
    return Row(
        kind="order",
        title="t",
        url="/",
        stage="s",
        waiting_on=waiting_on,
        last_change_at=None,
        last_change_by="",
        last_change_is_ai=False,
        waiting_lines=waiting_lines,
    )


class TestOurMoves:
    def test_counts_every_item_under_an_us_line(self):
        us = Flag("us: a; b; c", heading=WAITING_ON_US.capitalize(), lines=[("a", ""), ("b", ""), ("c", "")])
        silent = Flag("No reply: x", heading="No reply", lines=[("x", "")])
        rows = [_row("us: a; b; c", (us, silent)), _row("us: provide the import permit; provide the PAAR")]
        assert our_moves(rows) == 5

    def test_a_row_waiting_on_someone_else_counts_nothing(self):
        assert our_moves([_row("arrival (ETA 3 Oct)"), _row("—")]) == 0

    def test_the_overview_heading_carries_the_count_its_lines_give(self, da, world, client_in_program):
        _held_on_our_form_m(da, world)
        expected = our_moves(standing_rows(PROGRAM, TODAY))
        assert expected >= 1
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        count = _text(re.search(r'data-testid="standing-our-moves">(.*?)</span>', body, re.S).group(1))
        assert count.startswith(f"{expected} move")
        assert count.endswith("ours, listed under Us")


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
        heading = _text(re.search(r'<h3 id="owed" data-testid="owed-heading".*?</h3>', body, re.S).group(0))
        assert heading.startswith("What we owe — to clear the shipment (via Crescent Rehearsal Freight)")
        assert "What we owe them" not in heading
