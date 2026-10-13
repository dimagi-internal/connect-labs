"""The supply chain seeded behind a synthetic clone of the real RUTF opportunity (demo/clone_supply.py).

THIS REPOSITORY IS PUBLIC. The visits here are the invented stock-from-visits
world, which uses the released app's form paths -- the same shape a clone has.
"""

from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.demo import clone_supply
from connect_labs.supply_chain.demo.stock_from_visits import WORKERS, build_world
from connect_labs.supply_chain.models import DispensingRule, Distribution, Movement, SupplyPoint

START = date(2026, 8, 3)
TODAY = START + timedelta(weeks=8, days=-1)
FETCH = "connect_labs.supply_chain.stock.services.visit_source.fetch_visits"


# ---- planning the invented deliveries (no database) -----------------------------

FIRST = date(2026, 8, 3)


def _caseloads(first, until):
    """{username: {day: sachets}}: 18 steady workers at 5-30 a day, and two with the enrolment burst.

    The burst -- every child's first ration in the first fortnight, then a far lower
    pace -- is the shape a clone's visits open with, and the shape that left the
    seeded clone at 21-109 days of cover.
    """
    out = {}
    days = (until - first).days + 1
    for n in range(18):
        rate = Decimal(5 + (n * 25) // 17)
        out[f"flw_{n:03d}"] = {first + timedelta(days=d): rate for d in range(days) if (d + n) % 7 != 6}
    for n in (18, 19):
        out[f"flw_{n:03d}"] = {
            first + timedelta(days=d): Decimal(60 if d < 14 else 8) for d in range(days) if d % 7 != 6
        }
    return out


def _simulate(by_day, issues, start, end):
    from connect_labs.supply_chain.demo.clone_supply import _balances

    return _balances(by_day, issues, start, end)


def test_deliveries_fall_every_fortnight_the_last_a_week_before_the_seed():
    today = FIRST + timedelta(days=60)
    days = clone_supply.delivery_days(FIRST, today)
    assert days[-1] == today - timedelta(days=clone_supply.LAST_DELIVERY_DAYS_AGO)
    assert days[0] <= FIRST < days[0] + timedelta(days=clone_supply.CADENCE_DAYS)
    assert all((b - a).days == clone_supply.CADENCE_DAYS for a, b in zip(days, days[1:]))


def test_a_fixed_few_workers_are_short_and_the_choice_survives_a_new_worker():
    names = [f"flw_{n:03d}" for n in range(20)]
    short = clone_supply.short_workers(names)
    assert len(short) == 3
    assert clone_supply.short_workers(names + ["flw_new"]) & short
    targets = clone_supply.target_days(names)
    assert set(targets) == set(names) - short
    assert targets == clone_supply.target_days(names)  # seeded


@pytest.mark.parametrize("offset", range(7))  # whatever weekday it is seeded on
def test_on_the_seed_day_most_workers_hold_their_target_and_the_short_few_are_under_minimum(offset):
    """The design the clone read 21-109 days against: most at 24-34 days, the short few under 15."""
    today = FIRST + timedelta(days=56 + offset)
    dispensed = _caseloads(FIRST, today + timedelta(days=30))  # the clone's visits run on past today
    issues = clone_supply.plan_distributions(
        dispensed, first=FIRST, today=today, known_until=today + timedelta(days=30)
    )
    short = clone_supply.short_workers(dispensed)
    targets = clone_supply.target_days(dispensed)

    for username, by_day in dispensed.items():
        mine = [(i["on"], i["lines"][username]) for i in issues if username in i["lines"]]
        held = _simulate(by_day, mine, issues[0]["on"], today + timedelta(days=6))
        pace = clone_supply.pace(by_day, today)
        cover = held[today] / pace
        if username in short:
            assert cover < 15, (username, cover)
        else:
            low, high = targets[username][1], targets[username][1] + 10 / pace + 1
            assert low - 1 <= cover <= high, (username, cover, targets[username])
        # Nobody gives out stock they were never given, up to the next delivery.
        assert min(held.values()) >= 0, (username, min(held.values()))


def test_a_worker_with_nothing_to_give_out_is_sent_nothing():
    today = FIRST + timedelta(days=40)
    dispensed = {"busy": {FIRST + timedelta(days=d): Decimal(10) for d in range(41)}, "idle": {}}
    issues = clone_supply.plan_distributions(dispensed, first=FIRST, today=today)
    assert issues and all("idle" not in i["lines"] for i in issues)


def test_a_short_worker_is_sent_the_fortnight_ahead_and_a_full_one_their_target():
    held = Decimal(0)
    by_day = {FIRST + timedelta(days=d): Decimal(10) for d in range(60)}
    on = FIRST + timedelta(days=28)
    known = FIRST + timedelta(days=59)
    assert clone_supply.size_delivery(by_day, on=on, held=held, known_until=known, days=None) == 140
    assert clone_supply.size_delivery(by_day, on=on, held=held, known_until=known, days=35) == 350
    assert clone_supply.size_delivery(by_day, on=on, held=Decimal(100), known_until=known, days=35) == 250


@pytest.fixture
def clone(db):
    """A registered labs-only opportunity, its own programme, serving the invented world's visits."""
    opp = SyntheticOpportunity.objects.create(
        opportunity_id=SyntheticOpportunity.next_labs_only_opp_id(),
        labs_only=True,
        enabled=True,
        label="Clone of the RUTF opportunity (test)",
        org_name="Test org",
        program_name="Test programme",
        gdrive_folder_id="",
    )
    registry.invalidate_cache()
    world = build_world(START)
    with patch(FETCH, return_value=world.visits):
        yield {"opp": opp.opportunity_id, "world": world}
    registry.invalidate_cache()


def test_the_clone_is_seeded_end_to_end_and_every_invented_figure_says_so(clone):
    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        summary = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    assert summary["seeded"] is True
    assert DispensingRule.objects.filter(program_id=opp, opportunity_id=opp).count() == 1
    workers = SupplyPoint.objects.filter(program_id=opp, kind="user_held")
    assert {p.connect_username for p in workers} == set(WORKERS)

    runs = Distribution.objects.filter(program_id=opp)
    assert runs.exists()
    assert all(r.note.startswith("Invented for the demo") for r in runs)
    issued = sum(m.quantity for m in Movement.objects.filter(program_id=opp, kind="distribution"))
    assert issued == Decimal(summary["sachets_issued"])

    consumed = sum(
        m.quantity for m in Movement.objects.filter(program_id=opp, kind="consumption", reverses__isnull=True)
    )
    # What the reader posted is what the seeder sized the issues from (bar reversals of rejected visits).
    assert consumed >= Decimal(summary["sachets_dispensed"]) - Decimal(200)
    assert consumed > 0

    # Nobody gave out stock they were never given, and the stores sit inside their bands.
    from connect_labs.supply_chain.models import Item
    from connect_labs.supply_chain.stock.services import belief

    item = Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)
    on_hand = {b.point.connect_username: b for b in belief.worker_beliefs(opp, item, on_date=TODAY)}
    assert on_hand and all(b.on_hand.amount >= 0 for b in on_hand.values()), {u: b.on_hand for u, b in on_hand.items()}


def test_the_clones_rule_says_what_a_case_is_and_its_visits_remember_their_child(clone):
    from connect_labs.supply_chain.models import WorkerVisit
    from connect_labs.supply_chain.stock.services.dispensing import validate_cases

    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    rule = DispensingRule.objects.get(program_id=opp, opportunity_id=opp)
    assert rule.cases == validate_cases(clone_supply.rule_cases())
    visits = WorkerVisit.objects.filter(program_id=opp)
    assert visits.exists() and not visits.filter(entity_id="").exists()


def test_a_second_run_changes_nothing_without_reset(clone):
    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)
        before = Movement.objects.filter(program_id=opp).count()
        again = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    assert again["seeded"] is False
    assert Movement.objects.filter(program_id=opp).count() == before


def test_a_real_programme_is_refused_before_anything_is_read(db):
    with patch(FETCH) as fetch, pytest.raises(ValueError, match="refusing"):
        clone_supply.seed(program_id=263, opportunity_id=2230)
    fetch.assert_not_called()


def test_a_weekly_top_up_records_only_the_deliveries_due_since(clone):
    """Seeded part-way through the clone's visits; four weeks later the fortnightly issues since are recorded once."""
    from connect_labs.supply_chain.models import Distribution

    opp = clone["opp"]
    midway = START + timedelta(weeks=4, days=-1)
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=midway)
        before = set(Distribution.objects.filter(program_id=opp).values_list("distributed_on", flat=True))
        first = clone_supply.top_up(program_id=opp, opportunity_id=opp, today=TODAY)
        again = clone_supply.top_up(program_id=opp, opportunity_id=opp, today=TODAY)

    after = set(Distribution.objects.filter(program_id=opp).values_list("distributed_on", flat=True))
    new = sorted(after - before)
    assert new and all(day > midway for day in new)
    assert [d["on"] for d in first["deliveries_recorded"]] == [d.isoformat() for d in new]
    assert again["deliveries_recorded"] == []
    assert all(r.note.startswith("Invented for the demo") for r in Distribution.objects.filter(program_id=opp))
    assert clone_supply.seeded_clones() == [(opp, opp)]
    # Every fortnight after the last delivery the seed recorded, a week before it ran.
    assert all((day - (midway - timedelta(days=7))).days % clone_supply.CADENCE_DAYS == 0 for day in new)


def test_in_the_days_after_the_seed_the_daily_top_up_keeps_it_plausible(clone):
    """Seeded part-way; then each day the reader posts that day's visits and the top-up runs, as on beat.

    Nobody goes negative, and a few -- never most -- read below minimum on every day.
    """
    from connect_labs.labs.access.scopes import SYSTEM
    from connect_labs.supply_chain.data_access import SupplyDataAccess
    from connect_labs.supply_chain.models import Item
    from connect_labs.supply_chain.operations import call_operation
    from connect_labs.supply_chain.stock.services import belief, visit_source

    opp = clone["opp"]
    midway = START + timedelta(weeks=4, days=-1)
    access = SupplyDataAccess(
        access_token=visit_source.SYNTHETIC_TOKEN, program_id=opp, opportunity_id=opp, caller=SYSTEM
    )
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=midway)
        item = Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)
        for n in range(1, 15):
            day = midway + timedelta(days=n)
            clone_supply.top_up(program_id=opp, opportunity_id=opp, today=day)
            call_operation("visit_consumption_ingest", access, {"opportunity_id": opp, "until": day.isoformat()})
            statuses = [b.status for b in belief.worker_beliefs(opp, item, on_date=day)]
            below = sum(status in belief.BELOW for status in statuses)
            assert "negative" not in statuses and below <= len(statuses) // 4, (day, statuses)
            assert below > 0 or (day - (midway - timedelta(days=7))).days % clone_supply.CADENCE_DAYS < 3, (
                day,
                statuses,
            )


def test_a_top_up_before_any_seed_says_so(clone):
    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        assert clone_supply.top_up(program_id=opp, opportunity_id=opp, today=TODAY)["topped_up"] is False


# ---- the weekly count, when the clone's visits carry no balance ----------------


def _without_balances(visits):
    """The world's visits with every stock answer taken out, as a clone's arrive."""
    import copy

    from connect_labs.supply_chain.demo.stock_from_visits import PATHS

    stripped = []
    for visit in copy.deepcopy(visits):
        form = (visit.get("form_json") or {}).get("form") or {}
        if "stock" in str(form.get("@name", "")).lower():
            continue  # a Stock Management form is not a deliver visit; a clone has none
        for key in ("balance", "remaining", "received", "received_on"):
            node, parts = visit.get("form_json") or {}, PATHS[key].split(".")
            for part in parts[:-1]:
                node = node.get(part) if isinstance(node, dict) else None
            if isinstance(node, dict):
                node.pop(parts[-1], None)
        stripped.append(visit)
    return stripped


def test_with_no_balance_in_the_visits_each_worker_counts_weekly_and_it_says_so(clone):
    from connect_labs.supply_chain.models import Item, StockCount
    from connect_labs.supply_chain.stock.services import belief, ledger

    opp = clone["opp"]
    with patch(FETCH, return_value=_without_balances(clone["world"].visits)):
        summary = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    counts = StockCount.objects.filter(program_id=opp)
    assert counts.exists() and all(c.note == clone_supply.COUNTED for c in counts)
    assert {c.kind for c in counts} == {"self_reported"}
    assert all(w["counts"] for w in summary["weeks"])
    # Each count is the ledger's balance that day, off by no more than a real count is.
    item = Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)
    offsets = clone_supply.count_offsets(set(WORKERS))
    # Somebody's count is well under the ledger; most match it, so only real gaps are flagged.
    assert any(offset <= -5 for offset in offsets.values())
    assert sum(1 for offset in offsets.values() if offset == 0) > len(offsets) / 2
    for count in counts.select_related("supply_point")[:40]:
        held = ledger.balance(opp, count.supply_point, item=item, unit="sachet", on_date=count.counted_on)
        expected = max(Decimal(0), held.amount + offsets[count.supply_point.connect_username])
        assert count.quantity == expected
    # The workers now read as checked, with a variance against the ledger.
    beliefs = belief.worker_beliefs(opp, item, on_date=TODAY)
    assert all(b.reported is not None for b in beliefs)


def test_a_top_up_counts_each_sunday_since_once(clone):
    from connect_labs.supply_chain.models import StockCount

    opp = clone["opp"]
    midway = START + timedelta(weeks=4, days=-1)
    with patch(FETCH, return_value=_without_balances(clone["world"].visits)):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=midway)
        before = set(StockCount.objects.filter(program_id=opp).values_list("counted_on", flat=True))
        first = clone_supply.top_up(program_id=opp, opportunity_id=opp, today=TODAY)
        again = clone_supply.top_up(program_id=opp, opportunity_id=opp, today=TODAY)

    days = set(StockCount.objects.filter(program_id=opp).values_list("counted_on", flat=True))
    assert days - before and all(day > midway for day in days - before)
    assert again["counts_recorded"] == []
    assert len(first["counts_recorded"]) == len(days - before)


def test_when_the_visits_carry_balances_nothing_is_invented(clone):
    from connect_labs.supply_chain.models import StockCount

    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    assert StockCount.objects.filter(program_id=opp).exists()
    assert not StockCount.objects.filter(program_id=opp, note=clone_supply.COUNTED).exists()


# ---- upstream: where the clone's stock came from --------------------------------


def test_the_clones_stock_arrives_on_an_order_from_a_tender(clone):
    from connect_labs.supply_chain.demo import clone_upstream
    from connect_labs.supply_chain.models import Contract, Item, Quote, Shipment, Tender
    from connect_labs.supply_chain.stock.services.flow import flow

    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        summary = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    up = summary["upstream"]
    assert up["suppliers"] == 4 and len(up["tenders"]) == 2
    tenders = Tender.objects.filter(pk__in=up["tenders"]).order_by("pk")
    assert Quote.objects.filter(tender=tenders[0]).count() == 3  # one supplier never answered
    assert Quote.objects.filter(tender=tenders[1]).count() == 2  # two silent on the open tender
    order1 = Contract.objects.get(pk=up["orders"][0])
    assert order1.award_id is not None and order1.note == clone_upstream.NOTE
    # Every receipt into the network came on order 1's shipment: nothing arrives unlinked.
    receipts = Movement.objects.filter(program_id=opp, kind="receipt")
    assert receipts.exists()
    assert all(
        m.receipt_id and m.receipt.shipment_id == up["shipments"][0] for m in receipts.select_related("receipt")
    )
    # An order on the road now, not yet received.
    if len(up["shipments"]) > 1:
        assert Shipment.objects.get(pk=up["shipments"][1]).status == "in_transit"
    item = Item.objects.get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)
    sources = [n["name"] for n in flow(opp, item, on_date=TODAY)["nodes"] if n["kind"] == "source"]
    assert sources == ["Order from Sahel Nutrition Works (PO-RUTF-0001)"]


def test_the_complete_quotes_can_be_landed(clone):
    """A first-use review found every quote reading 'needs freight' / 'duty exemption, ours to attach'.

    CPT means the supplier pays carriage (freight included), and the programme's duty exemption is
    on file, so the complete quotes carry no freight or duty gap.
    """
    from connect_labs.supply_chain.models import Document, Quote
    from connect_labs.supply_chain.procurement.services.pricing import basis_gaps

    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    assert Document.objects.filter(program_id=opp, kind="duty_exemption").exists()
    cpt = Quote.objects.filter(tender__program_id=opp, incoterm="CPT Maiduguri")
    assert cpt.count() == 2 and all(basis_gaps(q) == [] for q in cpt)


def test_the_clones_rutf_carries_the_programmes_protocol_for_the_forecast(clone):
    """2 a day for up to 12 weekly visits: the forecast's course and its ration past what visits measure."""
    from connect_labs.supply_chain.models import Item
    from connect_labs.supply_chain.stock.services import forecast

    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)

    item = Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)
    course = item.commodity.course_definition
    assert course["base_units_per_course"] == 168
    assert Decimal(str(course["base_units_per_day"])) * 7 == 14
    assert forecast.course_size([], [], item, None) == {
        "size": Decimal(168),
        "basis": "protocol",
        "completed_cases": 0,
    }


def test_on_the_seed_day_the_short_few_are_below_minimum_and_the_rest_inside_their_band(clone):
    """What the Stock review reads the day it is seeded, at each worker's own 14-day pace.

    The seeded clone read 21-109 days of cover with none below minimum: deliveries sized
    to the busiest fortnight never ran down. Now the short few are under their minimum,
    everyone else is inside their band, and nobody is negative.
    """
    from connect_labs.supply_chain.models import Item
    from connect_labs.supply_chain.stock.services import belief

    opp = clone["opp"]
    with patch(FETCH, return_value=clone["world"].visits):
        clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)
    item = Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)
    beliefs = {b.point.connect_username: b for b in belief.worker_beliefs(opp, item, on_date=TODAY)}
    paced = {u: b for u, b in beliefs.items() if b.status in belief.BELOW + ("ok", "overstocked")}
    short = clone_supply.short_workers(paced)

    below = {u for u, b in paced.items() if b.status in belief.BELOW}
    assert below and below <= short, {u: (b.status, b.months_of_stock) for u, b in paced.items()}
    assert "negative" not in {b.status for b in beliefs.values()}
    assert all(paced[u].status == "ok" for u in set(paced) - short), {
        u: (b.status, b.months_of_stock) for u, b in paced.items()
    }
    summary = belief.workers_below_minimum(list(beliefs.values()))
    assert summary["line"] == f"{summary['below']} of {len(beliefs)} workers below minimum at their own 14-day pace"
    assert 0 < summary["below"] <= len(beliefs) // 4
