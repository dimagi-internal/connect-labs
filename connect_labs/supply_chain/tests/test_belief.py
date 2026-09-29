"""What we believe each worker holds, and every store above them (design 2026-09-28 §5).

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

The world: a central store feeds a partner store, which feeds two workers.
Worker A dispensed 100 sachets net (one visit rejected and reversed), 20 of
them on a visit still pending and 20 estimated from a protocol, and last
counted 190 against a ledger of 200. Worker B has dispensed for 16 days only.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, Movement, StockCount, SupplyPoint, WorkerVisit
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.stock.services import belief, posting, resupply
from connect_labs.supply_chain.stock.services.visit_reader import outcome_key
from connect_labs.supply_chain.values import Quantity, Unconfirmed

pytestmark = pytest.mark.django_db

PROGRAM = 10517
OPP = 10517
TODAY = date(2026, 9, 28)


def ago(days):
    return TODAY - timedelta(days=days)


@pytest.fixture
def item():
    commodity = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}", slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150
    )
    Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku="rutf",
        name="RUTF",
        commodity=commodity,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    return Item.objects.select_related("commodity").get(sku="rutf")


def point(slug, kind, parent=None, **extra):
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug=slug, name=slug, kind=kind, parent=parent, source="we_recorded", **extra
    )


def move(item, kind, sachets, on, frm=None, to=None):
    return Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind=kind,
        occurred_on=on,
        from_supply_point=frm,
        to_supply_point=to,
        item=item,
        commodity=item.commodity,
        quantity=Decimal(sachets),
        quantity_unit="sachet",
        source="we_recorded",
    )


def dispense(item, worker, sachets, on, visit_id, status="approved", estimated=False):
    movement = posting.post_visit_consumption(
        program_id=PROGRAM,
        opportunity_id=OPP,
        point=worker,
        item=item,
        quantity=Decimal(sachets),
        unit="sachet",
        occurred_on=on,
        visit_id=visit_id,
        estimated=estimated,
    )
    WorkerVisit.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        visit_id=visit_id,
        supply_point=worker,
        visit_date=on,
        status=status,
        outcomes={outcome_key(item.pk): "dispensed"},
    )
    return movement


def count(item, pt, kind, on, sachets):
    return StockCount.objects.create(
        program_id=PROGRAM,
        supply_point=pt,
        item=item,
        commodity=item.commodity,
        kind=kind,
        counted_on=on,
        quantity=Decimal(sachets),
        quantity_unit="sachet",
        source="commcare_form",
    )


@pytest.fixture
def world(item):
    central = point("central", "central_store", min_months_of_stock=Decimal("2"), max_months_of_stock=Decimal("6"))
    partner = point(
        "partner", "regional_store", parent=central, min_months_of_stock=Decimal("1"), max_months_of_stock=Decimal("3")
    )
    a = point(
        "worker-a",
        "user_held",
        parent=partner,
        opportunity_id=OPP,
        connect_username="worker-a",
        min_months_of_stock=Decimal("4"),
        max_months_of_stock=Decimal("8"),
    )
    b = point("worker-b", "user_held", parent=partner, opportunity_id=OPP, connect_username="worker-b")
    move(item, "receipt", 1000, ago(80), to=central)
    move(item, "transfer", 600, ago(70), frm=central, to=partner)
    move(item, "distribution", 300, ago(60), frm=partner, to=a)
    move(item, "distribution", 100, ago(60), frm=partner, to=b)
    dispense(item, a, 30, ago(50), "v1")
    dispense(item, a, 30, ago(40), "v2")
    dispense(item, a, 20, ago(30), "v3", status="pending")
    dispense(item, a, 20, ago(20), "v4", estimated=True)
    rejected = dispense(item, a, 20, ago(10), "v5", status="rejected")
    posting.post_visit_reversal(rejected, reason="visit rejected")
    dispense(item, b, 10, ago(15), "v6")
    WorkerVisit.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        visit_id="v7",
        supply_point=a,
        visit_date=ago(3),
        status="pending",
        outcomes={outcome_key(item.pk): "no_answer"},
    )
    count(item, a, "self_reported", ago(5), 190)
    return {"central": central, "partner": partner, "a": a, "b": b}


def sachets(n):
    return Quantity(Decimal(n), "sachet")


def test_a_workers_figures(item, world):
    a, b = belief.worker_beliefs(PROGRAM, item, on_date=TODAY)

    assert a.point.pk == world["a"].pk
    assert (a.issued, a.dispensed, a.unapproved, a.estimated) == (sachets(300), sachets(100), sachets(20), sachets(20))
    assert (a.on_hand, a.reported, a.reported_on, a.days_since_checked) == (sachets(200), sachets(190), ago(5), 5)
    assert (a.ledger_on_count_day, a.variance) == (sachets(200), sachets(-10))
    assert a.no_answer_visits == 1
    assert a.amc == Quantity(Decimal("58.8235"), "sachet")
    assert a.status == "below_min"
    assert a.unmatched_receipts == []
    assert b.on_hand == sachets(90)
    assert isinstance(b.amc, Unconfirmed) and b.status == "unknown"
    assert (b.reported, b.variance, b.days_since_checked) == (None, None, None)


def test_variance_is_against_the_ledger_on_the_count_day_not_today(item, world):
    """Stock that arrived after the count is not a discrepancy in the count."""
    move(item, "distribution", 50, ago(2), frm=world["partner"], to=world["a"])

    a = belief.point_belief(PROGRAM, world["a"], item, on_date=TODAY)

    assert a.on_hand == sachets(250)
    assert a.ledger_on_count_day == sachets(200)
    assert a.variance == sachets(-10)  # reported - ledger on the count day


def test_a_movement_on_the_count_day_is_in_the_ledger_it_is_compared_with(item, world):
    move(item, "distribution", 50, ago(5), frm=world["partner"], to=world["a"])

    a = belief.point_belief(PROGRAM, world["a"], item, on_date=TODAY)

    assert (a.ledger_on_count_day, a.variance) == (sachets(250), sachets(-60))


def test_a_reported_receipt_is_never_the_last_count(item, world):
    count(item, world["a"], "reported_receipt", ago(1), 999)

    a = belief.point_belief(PROGRAM, world["a"], item, on_date=TODAY)

    assert (a.reported, a.reported_on, a.reported_kind) == (sachets(190), ago(5), "self_reported")


def test_every_points_cover_is_the_resupply_plans(item, world):
    found = belief.beliefs_for(PROGRAM, item, list(world.values()), on_date=TODAY)
    for name, pt in world.items():
        plan = resupply.plan(PROGRAM, pt, item=item, as_of=TODAY)
        mine = found[pt.pk]
        assert (mine.on_hand, mine.amc, mine.months_of_stock, mine.days_to_stockout, mine.status) == (
            plan["on_hand"],
            plan["amc"],
            plan["months_of_stock"],
            plan["days_to_stockout"],
            plan["status"],
        ), name


def test_on_a_past_day_cover_is_still_the_resupply_plans(item, world):
    """Neither may know, on a past day, about dispensing that had not happened yet."""
    for day in (ago(65), ago(55), ago(35)):
        found = belief.beliefs_for(PROGRAM, item, list(world.values()), on_date=day)
        for name, pt in world.items():
            plan = resupply.plan(PROGRAM, pt, item=item, as_of=day)
            mine = found[pt.pk]
            # An empty point is 0 sachets here and 0 in the plan's pack unit: the same nothing.
            empty = mine.on_hand.amount == 0 and plan["on_hand"].amount == 0
            assert empty or mine.on_hand == plan["on_hand"], (name, day)
            assert (mine.amc, mine.status) == (plan["amc"], plan["status"]), (name, day)


def test_a_stores_subtree_is_its_own_stock_plus_its_childrens(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children

    assert partner.on_hand == sachets(200)
    assert partner.subtree["on_hand"] == sachets(490)
    assert central.subtree["on_hand"] == sachets(890)
    assert partner.subtree["dispensed"] == sachets(110)
    assert (partner.subtree["unapproved"], partner.subtree["estimated"]) == (sachets(20), sachets(20))
    assert partner.subtree["no_answer_visits"] == 1
    assert sorted(c.point.slug for c in partner.children) == ["worker-a", "worker-b"]


def test_issued_sums_every_hop_and_received_from_outside_counts_each_sachet_once(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children

    assert partner.subtree["issued"] == sachets(1000)  # 600 into the partner + 400 into its workers
    assert partner.subtree["received_from_outside"] == sachets(600)
    assert central.subtree["issued"] == sachets(2000)
    assert central.subtree["received_from_outside"] == sachets(1000)


def test_subtree_cover_is_recomputed_from_its_consumption_not_summed(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children

    # 110 sachets dispensed below the partner since the first of them, 51 days ago.
    assert partner.subtree["amc"] == Quantity(Decimal("64.7059"), "sachet")
    assert partner.subtree["months_of_stock"] == Decimal("490") / Decimal("64.7059")
    assert partner.subtree["days_to_stockout"] == Decimal("490") / Decimal("64.7059") * 30


def test_workers_below_their_minimum_are_counted_up_the_tree(item, world):
    (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)
    (partner,) = central.children
    assert (partner.workers, partner.workers_below_min) == (2, 1)
    assert (central.workers, central.workers_below_min) == (2, 1)


def test_a_parent_loop_is_still_shown_rather_than_dropped(item, world):
    loop_a = point("loop-a", "facility")
    loop_b = point("loop-b", "facility", parent=loop_a)
    SupplyPoint.objects.filter(pk=loop_a.pk).update(parent=loop_b)

    roots = belief.network_tree(PROGRAM, item, on_date=TODAY)

    def slugs(nodes):
        for node in nodes:
            yield node.point.slug
            yield from slugs(node.children)

    shown = list(slugs(roots))
    assert {"loop-a", "loop-b"} <= set(shown)
    assert len(shown) == len(set(shown))


def test_as_of_reproduces_a_past_day(item, world):
    a = belief.point_belief(PROGRAM, world["a"], item, on_date=ago(35))
    assert (a.on_hand, a.dispensed, a.reported, a.no_answer_visits) == (sachets(240), sachets(60), None, 0)
    assert (a.unapproved, a.estimated) == (sachets(0), sachets(0))


def _add_workers(item, world, start, n):
    for i in range(start, start + n):
        w = point(
            f"worker-extra-{i}",
            "user_held",
            parent=world["partner"],
            opportunity_id=OPP,
            connect_username=f"worker-extra-{i}",
        )
        move(item, "distribution", 50, ago(40), frm=world["partner"], to=w)
        dispense(item, w, 5, ago(30), f"extra-{i}", status="pending" if i % 2 else "approved")
        count(item, w, "self_reported", ago(2), 45)
        count(item, w, "reported_receipt", ago(20), 50)


def test_the_query_count_does_not_grow_with_the_workers(item, world):
    _add_workers(item, world, 0, 1)
    with CaptureQueriesContext(connection) as three:
        assert len(belief.worker_beliefs(PROGRAM, item, on_date=TODAY)) == 3
    _add_workers(item, world, 1, 27)
    with CaptureQueriesContext(connection) as thirty:
        rows = belief.worker_beliefs(PROGRAM, item, on_date=TODAY)

    assert len(rows) == 30
    assert len(thirty.captured_queries) == len(three.captured_queries)


def test_the_tree_query_count_does_not_grow_with_the_workers(item, world):
    _add_workers(item, world, 0, 1)
    with CaptureQueriesContext(connection) as three:
        belief.network_tree(PROGRAM, item, on_date=TODAY)
    _add_workers(item, world, 1, 27)
    with CaptureQueriesContext(connection) as thirty:
        (central,) = belief.network_tree(PROGRAM, item, on_date=TODAY)

    assert central.workers == 30
    assert len(thirty.captured_queries) == len(three.captured_queries)


def test_a_reported_receipt_with_no_distribution_is_a_fact_on_the_workers_row(item, world):
    for day, quantity in ((ago(58), "300"), (ago(3), "100")):
        count(item, world["a"], "reported_receipt", day, quantity)

    a = belief.point_belief(PROGRAM, world["a"], item, on_date=TODAY)

    assert a.unmatched_receipts == [
        {
            "reported_on": ago(3).isoformat(),
            "quantity": {"amount": "100", "unit": "sachet"},
            "form_submission_id": "",
            "recorded_from": ago(17).isoformat(),
            "recorded_to": ago(0).isoformat(),
        }
    ]
    assert belief.unmatched_receipts(PROGRAM, world["a"], item, on_date=TODAY) == a.unmatched_receipts


def test_a_reversal_is_not_a_recorded_arrival(item, world):
    """The rejected visit's sachets went back into the bag; nobody delivered them."""
    count(item, world["a"], "reported_receipt", ago(10), "20")

    a = belief.point_belief(PROGRAM, world["a"], item, on_date=TODAY)

    assert [r["reported_on"] for r in a.unmatched_receipts] == [ago(10).isoformat()]
    assert a.issued == sachets(300)


def test_an_unmatched_receipt_after_the_as_of_day_is_not_known_yet(item, world):
    count(item, world["a"], "reported_receipt", ago(3), "100")

    assert belief.point_belief(PROGRAM, world["a"], item, on_date=ago(4)).unmatched_receipts == []


def test_the_operations_put_it_on_the_wire(item, world):
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)

    workers = call_operation("worker_stock", da, {"item_id": item.pk, "as_of": TODAY.isoformat()})
    tree = call_operation("network_tree", da, {"item_id": item.pk, "as_of": TODAY.isoformat()})

    first = workers["workers"][0]
    assert (workers["unit"], workers["as_of"]) == ("sachet", TODAY.isoformat())
    assert first["on_hand"] == {"amount": "200", "unit": "sachet"}
    assert first["unapproved"] == {"amount": "20", "unit": "sachet"}
    assert first["reported_on"] == ago(5).isoformat()
    assert first["variance_reported_minus_ledger"] == {"amount": "-10", "unit": "sachet"}
    assert first["ledger_on_count_day"] == {"amount": "200", "unit": "sachet"}
    assert first["months_of_stock"] == "3.40"
    assert first["unmatched_receipts"] == []
    assert first["subtree"] is None
    assert tree["roots"][0]["subtree"]["on_hand"] == {"amount": "890", "unit": "sachet"}
    assert tree["roots"][0]["subtree"]["status"] == "overstocked"
    assert tree["roots"][0]["children"][0]["workers_below_min"] == 1


def test_worker_stock_narrows_to_one_opportunity(item, world):
    other = point("worker-c", "user_held", parent=world["partner"], opportunity_id=OPP + 1, connect_username="c")
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)

    found = call_operation("worker_stock", da, {"item_id": item.pk, "opportunity_id": OPP})

    assert other.pk not in {row["supply_point_id"] for row in found["workers"]}
    assert len(found["workers"]) == 2


def test_the_chain_summarys_network_balance_nets_a_reversal_with_its_visit(item, world):
    """summary._network_balance reads inbound movements of every kind. A reversal
    is one, and it must only undo its visit's consumption -- never add stock."""
    from connect_labs.supply_chain.summary import _network_balance

    points = SupplyPoint.objects.filter(program_id=PROGRAM)
    moves = Movement.objects.for_program(PROGRAM).filter(item=item)

    # 1000 received; 110 dispensed and standing; the rejected visit's 20 out and back in.
    assert _network_balance(moves, points) == {"sachet": Decimal("890")}
