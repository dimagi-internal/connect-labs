"""A worker's stock by day, and the chart drawn from it.

THIS REPOSITORY IS PUBLIC. Every figure here is invented.
"""

from datetime import date
from decimal import Decimal

import pytest

from connect_labs.supply_chain.models import Commodity, Item, Movement, StockCount, SupplyPoint
from connect_labs.supply_chain.stock.services import posting
from connect_labs.supply_chain.stock.services.timeline import timeline_svg, worker_timeline

PROGRAM = 10518


@pytest.fixture
def world(db):
    commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}",
        slug="rutf",
        name="RUTF",
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    item = Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku="rutf",
        name="RUTF",
        commodity=commodity,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    store = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="store", name="store", kind="regional_store", source="we_recorded"
    )
    worker = SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=PROGRAM,
        slug="w",
        name="w",
        kind="user_held",
        connect_username="w",
        parent=store,
        source="we_recorded",
    )
    Movement.objects.create(
        program_id=PROGRAM,
        kind="distribution",
        occurred_on=date(2026, 9, 1),
        from_supply_point=store,
        to_supply_point=worker,
        item=item,
        commodity=commodity,
        quantity=Decimal("1"),
        quantity_unit="carton",
        source="we_recorded",
    )
    for day, sachets, vid in ((3, 14, "a"), (5, 14, "b")):
        posting.post_visit_consumption(
            program_id=PROGRAM,
            opportunity_id=PROGRAM,
            point=worker,
            item=item,
            quantity=Decimal(sachets),
            unit="sachet",
            occurred_on=date(2026, 9, day),
            visit_id=vid,
            estimated=False,
        )
    rejected = Movement.objects.get(visit_id="b")
    posting.post_visit_reversal(rejected, reason="visit rejected")
    StockCount.objects.create(
        program_id=PROGRAM,
        supply_point=worker,
        item=item,
        commodity=commodity,
        kind="self_reported",
        counted_on=date(2026, 9, 6),
        quantity=Decimal("136"),
        quantity_unit="sachet",
        source="commcare_form",
    )
    StockCount.objects.create(
        program_id=PROGRAM,
        supply_point=worker,
        item=item,
        commodity=commodity,
        kind="reported_receipt",
        counted_on=date(2026, 9, 1),
        quantity=Decimal("150"),
        quantity_unit="sachet",
        source="commcare_form",
    )
    return {"item": item, "worker": worker}


def test_the_timeline_steps_up_for_issues_and_down_for_dispensing(world):
    line = worker_timeline(PROGRAM, world["worker"], world["item"])

    assert line["unit"] == "sachet"
    assert [
        (d["on"], Decimal(d["issued"]), Decimal(d["dispensed"]), Decimal(d["reversed"]), Decimal(d["balance"]))
        for d in line["days"]
    ] == [
        ("2026-09-01", Decimal("150"), Decimal("0"), Decimal("0"), Decimal("150")),
        ("2026-09-03", Decimal("0"), Decimal("14"), Decimal("0"), Decimal("136")),
        # The rejected visit and its reversal land on one day: the ledger nets
        # to nothing, but each is kept, so the step back up can be drawn.
        ("2026-09-05", Decimal("0"), Decimal("14"), Decimal("14"), Decimal("136")),
    ]
    # Counts only: a reported receipt is not a statement of what is on hand.
    assert line["counts"] == [{"on": "2026-09-06", "quantity": "136.0000", "kind": "self_reported"}]


def test_a_reversal_is_never_an_issue(world):
    line = worker_timeline(PROGRAM, world["worker"], world["item"])
    fifth = next(d for d in line["days"] if d["on"] == "2026-09-05")
    assert Decimal(fifth["issued"]) == 0
    assert Decimal(fifth["other"]) == 0


def test_as_of_stops_the_timeline_on_that_day(world):
    line = worker_timeline(PROGRAM, world["worker"], world["item"], on_date=date(2026, 9, 2))
    assert [d["on"] for d in line["days"]] == ["2026-09-01"]
    assert line["counts"] == []


def test_the_chart_draws_each_step_and_each_count(world):
    svg = timeline_svg(worker_timeline(PROGRAM, world["worker"], world["item"]))

    assert svg.startswith("<svg") and svg.endswith("</svg>")
    assert svg.count('data-kind="issued"') == 1
    assert svg.count('data-kind="dispensed"') == 2
    assert svg.count('data-kind="count"') == 1
    assert "counted 136 sachets" in svg


def test_the_chart_is_described_for_a_screen_reader(world):
    svg = timeline_svg(worker_timeline(PROGRAM, world["worker"], world["item"]))

    assert 'role="img"' in svg
    assert 'aria-labelledby="' in svg
    assert "<title " in svg and "<desc " in svg
    # The description says the whole story in words, not only "a chart".
    assert "150 sachets issued" in svg
    assert "14 sachets dispensed" in svg
    assert "ends at 136 sachets" in svg


def test_the_chart_takes_its_text_colour_from_the_page(world):
    """Axis text follows the card's own text colour, so it reads on a light or a dark card."""
    svg = timeline_svg(worker_timeline(PROGRAM, world["worker"], world["item"]))
    assert 'fill="currentColor"' in svg
    assert "#4b5563" not in svg  # no fixed grey text that vanishes on a dark card


def test_nothing_to_draw_is_no_chart():
    assert timeline_svg({"unit": "sachet", "days": [], "counts": []}) == ""


def test_the_unit_is_escaped():
    svg = timeline_svg(
        {
            "unit": "<b>",
            "days": [
                {"on": "2026-09-01", "before": "0", "issued": "1", "dispensed": "0", "other": "0", "balance": "1"}
            ],
            "counts": [],
        }
    )
    assert "<b>" not in svg and "&lt;b&gt;" in svg


def test_a_reversal_is_drawn_as_its_own_step_back_up(world):
    svg = timeline_svg(worker_timeline(PROGRAM, world["worker"], world["item"]))

    assert svg.count('data-kind="reversed"') == 1
    assert svg.count('data-kind="reversal-mark"') == 1
    assert "5 Sep 2026: 14 sachets put back (a visit rejected after it was counted)" in svg
    assert "of which 14 sachets was put back when visits were rejected" in svg


def test_a_day_without_a_reversal_draws_no_reversal_mark(world):
    svg = timeline_svg(worker_timeline(PROGRAM, world["worker"], world["item"], on_date=date(2026, 9, 4)))
    assert 'data-kind="reversed"' not in svg
    assert "put back" not in svg
