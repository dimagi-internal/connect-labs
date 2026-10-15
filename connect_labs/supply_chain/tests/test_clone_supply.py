"""The supply chain seeded behind a synthetic clone of the real RUTF opportunity (demo/clone_supply.py).

Two kinds of test, on purpose. The planning helpers (when deliveries fall, how big
they are, who is short) are reusable logic and are tested directly, with no
database. The seeder itself gets a smoke test: seed one small world ONCE and check
it is coherent -- a rule, a worker point per worker, invented deliveries that say
so, consumption read in, nobody negative, the weekly counts. Its invented story
(which worker reads below minimum, the supplier names, the exact copy) is not
pinned here: these tests exist to catch the seeder breaking, not to fix its narrative.

THIS REPOSITORY IS PUBLIC. The visits below are invented, in the released app's
form shape (`clone_supply.PATHS`) with no stock answers -- the shape a clone has.
"""

import uuid
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from django.db import transaction

from connect_labs.labs.synthetic import registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.supply_chain.demo import clone_supply
from connect_labs.supply_chain.models import (
    DispensingRule,
    Distribution,
    Item,
    Movement,
    StockCount,
    SupplyPoint,
    WorkerVisit,
)

START = date(2026, 8, 3)  # a Monday
TODAY = START + timedelta(weeks=8, days=-1)
MIDWAY = START + timedelta(weeks=4, days=-1)
FETCH = "connect_labs.supply_chain.stock.services.visit_source.fetch_visits"
WORKERS = tuple(f"worker-{name}" for name in ("acacia", "baobab", "cassia", "doum", "ebony", "ficus"))


# ---- planning the invented deliveries (no database) -----------------------------


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


def test_deliveries_fall_every_fortnight_the_last_a_week_before_the_seed():
    today = START + timedelta(days=60)
    days = clone_supply.delivery_days(START, today)
    assert days[-1] == today - timedelta(days=clone_supply.LAST_DELIVERY_DAYS_AGO)
    assert days[0] <= START < days[0] + timedelta(days=clone_supply.CADENCE_DAYS)
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
    today = START + timedelta(days=56 + offset)
    dispensed = _caseloads(START, today + timedelta(days=30))  # the clone's visits run on past today
    issues = clone_supply.plan_distributions(
        dispensed, first=START, today=today, known_until=today + timedelta(days=30)
    )
    short = clone_supply.short_workers(dispensed)
    targets = clone_supply.target_days(dispensed)

    for username, by_day in dispensed.items():
        mine = [(i["on"], i["lines"][username]) for i in issues if username in i["lines"]]
        held = clone_supply._balances(by_day, mine, issues[0]["on"], today + timedelta(days=6))
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
    today = START + timedelta(days=40)
    dispensed = {"busy": {START + timedelta(days=d): Decimal(10) for d in range(41)}, "idle": {}}
    issues = clone_supply.plan_distributions(dispensed, first=START, today=today)
    assert issues and all("idle" not in i["lines"] for i in issues)


def test_a_short_worker_is_sent_the_fortnight_ahead_and_a_full_one_their_target():
    by_day = {START + timedelta(days=d): Decimal(10) for d in range(60)}
    on = START + timedelta(days=28)
    known = START + timedelta(days=59)
    assert clone_supply.size_delivery(by_day, on=on, held=Decimal(0), known_until=known, days=None) == 140
    assert clone_supply.size_delivery(by_day, on=on, held=Decimal(0), known_until=known, days=35) == 350
    assert clone_supply.size_delivery(by_day, on=on, held=Decimal(100), known_until=known, days=35) == 250


# ---- a small clone-shaped world -------------------------------------------------


def _visit(visit_id, username, on, form_name, answers):
    """One row shaped as Connect's user_visits export holds it."""
    xform = str(uuid.UUID(int=visit_id))
    form = {"@name": form_name}
    for path, value in answers.items():
        node = form
        *parents, leaf = path.split(".")[1:]
        for part in parents:
            node = node.setdefault(part, {})
        node[leaf] = value
    return {
        "id": visit_id,
        "xform_id": xform,
        "username": username,
        "user_id": str(uuid.uuid5(uuid.NAMESPACE_URL, username)),
        "deliver_unit": "",
        "deliver_unit_id": None,
        "entity_id": f"case-{username}-{on.isoformat()}",
        "entity_name": "",
        "visit_date": on.isoformat(),
        "status": "approved",
        "reason": None,
        "location": "",
        "flagged": False,
        "flag_reason": {},
        "form_json": {"id": xform, "form": form},
        "completed_work": "",
        "status_modified_date": f"{on.isoformat()}T12:00:00Z",
        "review_status": "",
        "review_created_on": None,
        "justification": None,
        "date_created": f"{on.isoformat()}T09:00:00Z",
        "completed_work_id": None,
        "images": [],
    }


def clone_visits(weeks=8):
    """Three visits a worker a week, the first a Screening; 14 sachets each. No stock answers, as a clone's."""
    paths, visits, next_id = clone_supply.PATHS, [], 700_000_001
    for n, username in enumerate(WORKERS):
        for week in range(weeks):
            for k in range(3):
                on = START + timedelta(weeks=week, days=1 + 2 * k)
                if k == 0:
                    form, answers = clone_supply.FORM_SCREENING, {paths["rutf_screening_total"]: "14"}
                else:
                    form, answers = clone_supply.FORM_VISIT, {paths["rutf_visit"]: "14", paths["appetite_visit"]: "0"}
                visits.append(_visit(next_id, username, on, form, answers))
                next_id += 1
    return visits


def _register_clone():
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
    return opp.opportunity_id


@pytest.fixture
def clone(db):
    """A registered labs-only opportunity, its own programme, serving the small world's visits."""
    opp = _register_clone()
    with patch(FETCH, return_value=clone_visits()):
        yield opp
    registry.invalidate_cache()


@pytest.fixture(scope="class")
def _seeded_once(django_db_setup, django_db_blocker):
    """Seed the clone ONCE for TestTheSeededClone, inside an outer transaction rolled back at class end.

    Each test still runs in its own savepoint (the `db` fixture nests inside this), so a
    test's writes roll back as before.
    """
    with django_db_blocker.unblock():
        outer = transaction.atomic()
        outer.__enter__()
        try:
            opp = _register_clone()
            with patch(FETCH, return_value=clone_visits()):
                summary = clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)
            yield {"opp": opp, "summary": summary}
        finally:
            transaction.set_rollback(True)
            outer.__exit__(None, None, None)
            registry.invalidate_cache()


@pytest.fixture
def seeded(_seeded_once, db):
    registry.invalidate_cache()
    with patch(FETCH, return_value=clone_visits()):
        yield _seeded_once
    registry.invalidate_cache()


def _item(opp):
    return Item.objects.select_related("commodity").get(scope_key=f"prog:{opp}", sku=clone_supply.SKU)


class TestTheSeededClone:
    def test_the_seed_builds_a_coherent_world_and_every_invented_figure_says_so(self, seeded):
        from connect_labs.supply_chain.stock.services import belief
        from connect_labs.supply_chain.stock.services.dispensing import validate_cases

        opp, summary = seeded["opp"], seeded["summary"]
        assert summary["seeded"] is True

        rule = DispensingRule.objects.get(program_id=opp, opportunity_id=opp)
        assert rule.cases == validate_cases(clone_supply.rule_cases())
        workers = SupplyPoint.objects.filter(program_id=opp, kind="user_held")
        assert {p.connect_username for p in workers} == set(WORKERS)

        runs = Distribution.objects.filter(program_id=opp)
        assert runs.exists() and all(r.note.startswith(clone_supply.INVENTED_PREFIX) for r in runs)
        issued = sum(m.quantity for m in Movement.objects.filter(program_id=opp, kind="distribution"))
        assert issued == Decimal(summary["sachets_issued"])
        consumed = sum(
            m.quantity for m in Movement.objects.filter(program_id=opp, kind="consumption", reverses__isnull=True)
        )
        assert consumed == Decimal(summary["sachets_dispensed"]) > 0
        visits = WorkerVisit.objects.filter(program_id=opp)
        assert visits.exists() and not visits.filter(entity_id="").exists()

        # The opening stock came in on an order: every receipt hangs off the first shipment.
        receipts = Movement.objects.filter(program_id=opp, kind="receipt").select_related("receipt")
        assert receipts.exists()
        assert all(m.receipt_id and m.receipt.shipment_id == summary["upstream"]["shipments"][0] for m in receipts)

        beliefs = belief.worker_beliefs(opp, _item(opp), on_date=TODAY)
        assert beliefs and "negative" not in {b.status for b in beliefs}

    def test_with_no_balance_in_the_visits_each_worker_counts_weekly_and_it_says_so(self, seeded):
        from connect_labs.supply_chain.stock.services import ledger

        opp, summary = seeded["opp"], seeded["summary"]
        counts = StockCount.objects.filter(program_id=opp).select_related("supply_point")
        assert counts.exists() and all(c.note == clone_supply.COUNTED and c.kind == "self_reported" for c in counts)
        assert all(w["counts"] for w in summary["weeks"])
        # Each count is the ledger's balance that day, moved by that worker's seeded offset.
        item, offsets = _item(opp), clone_supply.count_offsets(set(WORKERS))
        for count in counts:
            held = ledger.balance(opp, count.supply_point, item=item, unit="sachet", on_date=count.counted_on)
            assert count.quantity == max(Decimal(0), held.amount + offsets[count.supply_point.connect_username])

    def test_a_second_run_changes_nothing_without_reset(self, seeded):
        opp = seeded["opp"]
        before = Movement.objects.filter(program_id=opp).count()
        assert clone_supply.seed(program_id=opp, opportunity_id=opp, today=TODAY)["seeded"] is False
        assert Movement.objects.filter(program_id=opp).count() == before


def test_a_real_programme_is_refused_before_anything_is_read(db):
    with patch(FETCH) as fetch, pytest.raises(ValueError, match="refusing"):
        clone_supply.seed(program_id=263, opportunity_id=2230)
    fetch.assert_not_called()


def test_a_top_up_before_any_seed_says_so(clone):
    assert clone_supply.top_up(program_id=clone, opportunity_id=clone, today=TODAY)["topped_up"] is False


def test_a_top_up_records_the_deliveries_and_counts_due_since_once(clone):
    """What the daily beat task (`supply_chain_clone_top_up`) runs: seeded part-way, topped up four weeks on."""
    clone_supply.seed(program_id=clone, opportunity_id=clone, today=MIDWAY)
    delivered = set(Distribution.objects.filter(program_id=clone).values_list("distributed_on", flat=True))
    counted = set(StockCount.objects.filter(program_id=clone).values_list("counted_on", flat=True))

    first = clone_supply.top_up(program_id=clone, opportunity_id=clone, today=TODAY)
    again = clone_supply.top_up(program_id=clone, opportunity_id=clone, today=TODAY)

    new = sorted(
        set(Distribution.objects.filter(program_id=clone).values_list("distributed_on", flat=True)) - delivered
    )
    assert new and all(day > MIDWAY for day in new)
    assert [d["on"] for d in first["deliveries_recorded"]] == [d.isoformat() for d in new]
    # Every fortnight after the last delivery the seed recorded, a week before it ran.
    assert all((day - (MIDWAY - timedelta(days=7))).days % clone_supply.CADENCE_DAYS == 0 for day in new)
    new_counts = set(StockCount.objects.filter(program_id=clone).values_list("counted_on", flat=True)) - counted
    assert new_counts and len(first["counts_recorded"]) == len(new_counts)
    assert again["deliveries_recorded"] == [] and again["counts_recorded"] == []
    assert clone_supply.seeded_clones() == [(clone, clone)]
