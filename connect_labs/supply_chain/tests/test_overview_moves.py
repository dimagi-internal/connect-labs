"""The overview: whose move each row waits on, counts that add up, and the chain's captions.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on rehearsal_world.
"""

import datetime
import datetime as dt
import re

from django.template import Context, Template
from django.urls import reverse

from connect_labs.supply_chain import records
from connect_labs.supply_chain.moves import SUPPLIERS, US, Move
from connect_labs.supply_chain.standing import Row, move_counts, standing_rows
from connect_labs.supply_chain.templatetags.supply_chain_extras import (
    count_back,
    deliver_stages,
    evaluation_value,
    evaluation_words,
    lead_in,
    quotation_words,
    short_day,
)
from connect_labs.supply_chain.tests import rehearsal_world
from connect_labs.supply_chain.tests.rehearsal_world import PROGRAM, TODAY, _contract, _held_on_our_form_m, _text, op

# The world's fixtures, shared rather than copied.
da = rehearsal_world.da
world = rehearsal_world.world
client_in_program = rehearsal_world.client_in_program
web = rehearsal_world.web


def test_lead_in_bolds_the_kind_and_keeps_the_text():
    rendered = Template("{% load supply_chain_extras %}{{ line|lead_in }}|{{ plain|lead_in }}").render(
        Context({"line": "No reply: Plateau & Sons", "plain": "Nothing to chase"})
    )
    assert rendered == "<strong>No reply</strong>: Plateau &amp; Sons|Nothing to chase"


def test_waiting_on_labels_read_alike():
    assert str(lead_in("us: import permit")) == "<strong>Us</strong>: import permit"


def test_a_held_document_makes_the_orders_move_ours(da, world, client_in_program):
    _held_on_our_form_m(da, world)
    body = client_in_program.get(reverse("supply_chain:home")).content.decode()
    assert 'data-testid="whose-us" data-count="1">To do 1<' in body
    assert "Provide 1 document to " in body


class TestTheOverview:
    def test_an_open_tender_past_its_deadline_says_so_once_in_its_decide_move(self, da, world):
        # Since the unanswered-tender batch 3 judge: the deadline is said once on the
        # overview, in the decide move's tag line -- not again in the stage.
        op(da, "tender_update", tender_id=world["tender"]["id"], data={"response_deadline": "2026-09-29"})
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "tender")
        assert "deadline passed" not in row.stage
        deadline = [m for m in row.ours if m.rule == "deadline"]
        assert [m.text for m in deadline] == ["Decide: extend, close or award"]
        assert deadline[0].detail == "deadline passed 29 Sep"
        assert row.stage_name == "Collecting quotes"
        before = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 9, 28)) if r.kind == "tender")
        assert "deadline passed" not in before.stage
        assert not [m for m in before.ours if m.rule == "deadline"]

    def test_an_order_billed_above_agreed_is_flagged(self, da, world):
        contract = _contract(da, world)
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "110350.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "order")
        assert [(m.rule, m.text) for m in row.ours if m.rule == "invoice check"] == [
            ("invoice check", "Review invoice INV-REH-1")
        ]
        assert row.whose == "us"

    def test_an_order_billed_at_the_agreed_price_is_not(self, da, world):
        contract = _contract(da, world)
        op(
            da,
            "invoice_record",
            data={
                "contract_id": contract["id"],
                "reference": "INV-REH-1",
                "issued_on": "2026-09-21",
                "currency": "USD",
                "amount": "106800.00",
                "quantity_billed": 2000,
                "quantity_unit": "carton",
                "source": "supplier_reported",
            },
        )
        row = next(r for r in standing_rows(PROGRAM, datetime.date(2026, 10, 2)) if r.kind == "order")
        assert not [m for m in row.ours if m.rule == "invoice check"]


class TestWhereaboutsIsOneHelper:
    def test_the_newest_moving_shipment_and_its_words(self):
        shipments = [
            {"id": 1, "status": "delivered", "dispatched_on": "2026-07-01"},
            {"id": 2, "status": "at_customs", "dispatched_on": "2026-08-26"},
        ]
        assert records.latest_moving_shipment(shipments)["id"] == 2
        assert records.latest_moving_shipment([{"id": 1, "status": "planned"}]) is None
        assert records.shipment_whereabouts("at_customs", True) == "at customs — held, waiting on us"
        assert records.shipment_whereabouts("at_customs") == "at customs"


class TestTheOverviewAgreesWithTheOrderPage:
    def test_the_order_row_says_held_at_customs_on_us(self, da, world):
        _held_on_our_form_m(da, world)
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.kind == "order")
        # The stage is the fact; Waiting on says the move is ours, so the stage does not repeat it.
        assert row.stage.endswith("at customs, held")
        assert "waiting on us" not in row.stage
        assert "in transit" not in row.stage

    def test_the_chain_s_dispatched_tile_says_so_too(self, da, world, client_in_program):
        _held_on_our_form_m(da, world)
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        assert "1 at customs — held, waiting on us · not yet stock on hand" in _text(body)
        assert "in transit — not yet stock on hand" not in _text(body)


def _standing():
    return standing_rows(PROGRAM, TODAY)


class TestTheOrderRow:
    def test_it_says_the_round_it_came_from_and_the_stage_only(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        row = next(r for r in _standing() if r.contract_id == contract["id"])
        assert row.origin == "RUTF round"
        assert row.stage.endswith("at customs, held")
        assert "waiting on us" not in row.stage


class TestTheEvaluationCaption:
    """Finding #3/#19 (DDD unanswered-round 2026-10-03): the headline counts open rounds only,
    says what it counts, and lists awarded rounds apart. Since 2026-10-07 it counts quotes with a
    landed price -- data -- not the comparison's verdict on which are comparable: each round below
    carries a different `comparable`, so reading it instead would change every figure."""

    def test_one_open_round_reads_as_its_own_comparison_page(self):
        evaluation = {
            "provisional": True,
            "by_tender": [{"label": "R", "comparable": 0, "priced": 2, "of": 3, "awarded": False}],
        }
        assert evaluation_value(evaluation) == "2 of 3"
        assert evaluation_words(evaluation) == "quotes with a landed price on R"

    def test_an_awarded_round_is_listed_apart_not_added_in(self):
        evaluation = {
            "provisional": True,
            "by_tender": [
                {"label": "RUTF round 2", "comparable": 0, "priced": 2, "of": 3, "awarded": False},
                {"label": "RUTF round 1", "comparable": 1, "priced": 1, "of": 1, "awarded": True},
            ],
        }
        assert evaluation_value(evaluation) == "2 of 3"
        words = evaluation_words(evaluation)
        assert words == "quotes with a landed price on RUTF round 2 · awarded: RUTF round 1"
        assert "provisional" not in words

    def test_several_open_rounds_read_one_by_one(self):
        evaluation = {
            "by_tender": [
                {"label": "R3", "comparable": 0, "priced": 1, "of": 2, "awarded": False},
                {"label": "R2", "comparable": 0, "priced": 3, "of": 3, "awarded": False},
            ]
        }
        assert evaluation_value(evaluation) == "4 of 5"
        assert evaluation_words(evaluation) == "R3: 1 of 2 · R2: 3 of 3 quotes with a landed price"

    def test_no_round_yet_reads_the_program_totals(self):
        assert evaluation_value({"comparable": 0, "priced": 2, "of": 4}) == "2 of 4"
        assert evaluation_words({"comparable": 0, "priced": 2, "of": 4}) == "quotes with a landed price"


class TestTheQuotationCaption:
    def test_live_quotes_break_down_by_tender(self):
        words = quotation_words({"by_tender": [{"label": "R2", "live": 3}, {"label": "R1", "live": 1}]})
        assert words == "live: 3 on R2 · 1 on R1"

    def test_one_tender_names_it(self):
        assert quotation_words({"by_tender": [{"label": "R2", "live": 3}]}) == "live on R2"


class TestTheChainLabels:
    def test_plain_labels_and_units(self):
        cells = deliver_stages(
            {
                "network": {"supply_points": 0, "user_held": 0, "never_reported": 0},
                "on_hand": None,
                "in_transit": None,
                "distributions": {"runs": 0},
                "consumed": None,
                "cover": {},
            }
        )
        labels = [c["label"] for c in cells]
        assert "Below reorder level" in labels and "Under its band" not in labels
        assert cells[0]["sub"].startswith("supply points")


def _standing_row(ours=(), theirs=()):
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
            _standing_row([_move(US, "a"), _move(US, "b")], [_move(SUPPLIERS, "x")]),
            _standing_row([_move(US, "c")]),
        ]
        assert move_counts(rows) == {"ours": 3, "theirs": 1}

    def test_a_row_with_no_moves_counts_nothing(self):
        assert move_counts([_standing_row(), _standing_row()]) == {"ours": 0, "theirs": 0}

    def test_the_overview_heading_carries_the_count_its_rows_give(self, da, world, client_in_program):
        _held_on_our_form_m(da, world)
        expected = move_counts(standing_rows(PROGRAM, TODAY))["ours"]
        assert expected >= 1
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        count = _text(re.search(r'data-testid="standing-our-moves">(.*?)</div>', body, re.S).group(1))
        assert count.startswith(f"{expected} to do")
        assert "on you" not in count

    def test_each_rows_whose_chips_sum_to_the_heading(self, da, world, client_in_program):
        _held_on_our_form_m(da, world)
        totals = move_counts(standing_rows(PROGRAM, TODAY))
        body = client_in_program.get(reverse("supply_chain:home")).content.decode()
        ours = sum(int(n) for n in re.findall(r'data-testid="whose-us" data-count="(\d+)"', body))
        theirs = sum(int(n) for n in re.findall(r'data-testid="whose-suppliers" data-count="(\d+)"', body))
        assert (ours, theirs) == (totals["ours"], totals["theirs"])


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


class TestAnOrderRowReadsOnItsOwnSteps:
    def test_an_order_held_at_customs_is_on_its_transit_step_not_delivered(self, da, world):
        """The overview's order bar is the order page's six steps: held at customs is step 4, not Delivery."""
        from connect_labs.supply_chain.standing import ORDER_BAR

        contract, _ = _held_on_our_form_m(da, world)
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.contract_id == contract["id"])
        assert row.bar_index == ORDER_BAR.index("In transit")
        assert row.bars == ["done", "done", "done", "now", "todo", "todo"]


class TestAnOrderRowCarriesItsExpectedDay:
    def test_goods_under_way_say_when_they_are_expected(self, da, world):
        contract, _ = _held_on_our_form_m(da, world)
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.contract_id == contract["id"])
        assert row.label == "At customs — held · expected 19 Sep"
        assert (row.stage_name, row.stage_detail) == ("At customs — held", "expected 19 Sep")

    def test_no_expected_day_means_no_date(self, da, world):
        from connect_labs.supply_chain.models import Shipment

        contract, shipment = _held_on_our_form_m(da, world)
        Shipment.objects.filter(pk=shipment["id"]).update(expected_on=None)
        row = next(r for r in standing_rows(PROGRAM, TODAY) if r.contract_id == contract["id"])
        assert row.label == "At customs — held"


NOW = dt.datetime(2026, 10, 3, 12, tzinfo=dt.UTC)


def test_last_change_is_always_a_count_then_the_day():
    assert count_back(dt.datetime(2026, 10, 3, 9, tzinfo=dt.UTC), NOW) == "today"
    assert count_back(dt.datetime(2026, 9, 27, 9, tzinfo=dt.UTC), NOW) == "6 days ago"
    assert count_back(dt.datetime(2026, 7, 29, 9, tzinfo=dt.UTC), NOW) == "2 months ago"
    assert short_day(dt.datetime(2026, 7, 29, 9, tzinfo=dt.UTC), NOW) == "29 Jul"
    assert short_day(dt.datetime(2025, 12, 1, 9, tzinfo=dt.UTC), NOW) == "1 Dec 2025"
