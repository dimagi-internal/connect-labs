"""Every reader of network stock costs the same whatever the number of workers.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

A visit makes every worker a supply point, so a caller that plans one point at
a time grows with the roster. These pin each caller of `network_stock` -- the
operation (the map, the contract and item pages, the portfolio), the checks
(and so the hourly alert), and the chain summary -- to a query count that does
not move as workers are added, and pin their figures to what a per-point
`resupply.plan` and `soh.stock_on_hand` give, which is what they read before.

The world: a central store receives RUTF and a second, differently packed
product; it hands RUTF on to workers, who dispense on visits and count. The
store also holds stock recorded against the product alone (no trade item), so
every kind of point the network has -- one item, several items, no item -- is
in it.
"""

from datetime import timedelta
from decimal import Decimal

import pytest
from django.db import connection
from django.db.models import Q
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain import checks
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, Movement, StockCount, SupplyPoint
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.portfolio.map_data import _cover
from connect_labs.supply_chain.stock.services import network, posting, resupply, soh

pytestmark = pytest.mark.django_db

PROGRAM = 10531
OPP = 10531
TODAY = timezone.localdate()


def ago(days):
    return TODAY - timedelta(days=days)


def _move(item, kind, quantity, on, frm=None, to=None, unit="sachet", commodity=None):
    return Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind=kind,
        occurred_on=on,
        from_supply_point=frm,
        to_supply_point=to,
        item=item,
        commodity=commodity or item.commodity,
        quantity=Decimal(quantity),
        quantity_unit=unit,
        source="we_recorded",
    )


def _count(item, pt, on, quantity, kind="self_reported"):
    return StockCount.objects.create(
        program_id=PROGRAM,
        supply_point=pt,
        item=item,
        commodity=item.commodity,
        kind=kind,
        counted_on=on,
        quantity=Decimal(quantity),
        quantity_unit="sachet",
        source="commcare_form",
    )


@pytest.fixture
def access():
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


@pytest.fixture
def world():
    scope = f"prog:{PROGRAM}"
    rutf_product = Commodity.objects.create(
        scope_key=scope, slug="rutf", name="RUTF", base_unit="sachet", pack_unit="carton", base_per_pack=150
    )
    rutf = Item.objects.create(
        scope_key=scope,
        sku="rutf-150",
        name="RUTF 150",
        commodity=rutf_product,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    ors_product = Commodity.objects.create(scope_key=scope, slug="ors", name="ORS", base_unit="sachet")
    ors = Item.objects.create(scope_key=scope, sku="ors-1", name="ORS", commodity=ors_product, base_unit="sachet")
    central = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="central",
        name="Central store",
        kind="central_store",
        source="we_recorded",
        min_months_of_stock=Decimal("2"),
        max_months_of_stock=Decimal("6"),
    )
    # Holds one item and nothing else.
    partner = SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug="partner",
        name="Partner store",
        kind="regional_store",
        parent=central,
        source="we_recorded",
        min_months_of_stock=Decimal("1"),
        max_months_of_stock=Decimal("3"),
    )
    # Holds stock recorded against the product only.
    depot = SupplyPoint.objects.create(
        program_id=PROGRAM, slug="depot", name="Depot", kind="regional_store", parent=central, source="we_recorded"
    )
    _move(rutf, "receipt", 6000, ago(85), to=central)
    _move(ors, "receipt", 900, ago(85), to=central)
    _move(rutf, "transfer", 3000, ago(75), frm=central, to=partner)
    _move(ors, "transfer", 300, ago(70), frm=central, to=depot)
    Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind="receipt",
        occurred_on=ago(80),
        to_supply_point=depot,
        item=None,
        commodity=rutf_product,
        quantity=Decimal("400"),
        quantity_unit="sachet",
        source="we_recorded",
    )
    Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind="consumption",
        occurred_on=ago(40),
        from_supply_point=depot,
        item=None,
        commodity=rutf_product,
        quantity=Decimal("90"),
        quantity_unit="sachet",
        source="we_recorded",
    )
    _move(ors, "consumption", 60, ago(50), frm=central)
    _move(ors, "consumption", 45, ago(20), frm=central)
    _count(rutf, central, ago(4), 2900)
    world = {"rutf": rutf, "ors": ors, "central": central, "partner": partner, "depot": depot, "n": 0}
    add_workers(world, 3)
    return world


def add_workers(world, n):
    """n more workers on the roster: issued, dispensing on visits, most of them counting."""
    rutf, partner = world["rutf"], world["partner"]
    for i in range(world["n"], world["n"] + n):
        worker = SupplyPoint.objects.create(
            program_id=PROGRAM,
            opportunity_id=OPP,
            slug=f"worker-{i}",
            name=f"worker-{i:03d}",
            kind="user_held",
            connect_username=f"worker-{i}",
            parent=partner,
            source="connect_visit",
            min_months_of_stock=Decimal("1"),
            max_months_of_stock=Decimal("2"),
        )
        _move(rutf, "distribution", 100 + i if i % 2 == 0 else 50, ago(60), frm=partner, to=worker)
        for v, days in enumerate((50, 35, 12)):
            posting.post_visit_consumption(
                program_id=PROGRAM,
                opportunity_id=OPP,
                point=worker,
                item=rutf,
                quantity=Decimal(25 if i % 2 == 0 else 10),
                unit="sachet",
                occurred_on=ago(days),
                visit_id=f"v-{i}-{v}",
                estimated=bool(v % 2),
            )
        if i % 3 == 0:
            continue  # never counted: the never-reported check
        _count(rutf, worker, ago(3 + i % 5), 80 + i % 7)
        if i % 3 == 1:
            _move(rutf, "distribution", 10, ago(1), frm=partner, to=worker)  # after the count
    world["n"] += n


def _queries(fn):
    with CaptureQueriesContext(connection) as captured:
        fn()
    return len(captured.captured_queries)


def _constant_in_workers(world, fn):
    fn()  # warm any per-process caches
    few = _queries(fn)
    add_workers(world, 12)
    many = _queries(fn)
    return few, many


# ---- figures: what a per-point plan gave, unchanged -------------------------


@pytest.mark.parametrize("which", [None, "rutf", "ors"])
def test_network_rows_are_a_plan_per_point(world, which):
    """Every row the grouped pass gives is the one a resupply.plan per point gave."""
    item = world[which] if which else None
    grouped = network.network_stock(PROGRAM, item=item)
    planned = network.network_stock(PROGRAM, item=item, per_point=True)

    assert [r["name"] for r in grouped] == [r["name"] for r in planned]
    for g, p in zip(grouped, planned):
        for key in g:
            assert g[key] == p[key], (g["name"], key, g[key], p[key])
    # The world is not vacuous: rates, months and every kind of point are in it.
    assert any(isinstance(r["amc"], resupply.Quantity) for r in grouped)
    if which != "ors":
        assert {"below_min", "ok"} <= {r["status"] for r in grouped}


def test_a_point_holding_several_items_keeps_its_summed_rate(world):
    """The checks, map and summary were given a rate across a point's items; they still are."""
    central = next(r for r in network.network_stock(PROGRAM) if r["name"] == "Central store")
    plan = resupply.plan(PROGRAM, world["central"])

    assert central["item"] is None
    assert central["amc"] == plan["amc"] and isinstance(central["amc"], resupply.Quantity)
    assert (central["months_of_stock"], central["status"]) == (plan["months_of_stock"], plan["status"])


def test_the_stock_page_still_refuses_a_rate_across_items(world):
    central = next(r for r in network.network_stock(PROGRAM, several_items="refuse") if r["name"] == "Central store")
    assert central["amc"].reasons == (network.NOT_ONE_ITEM,)


def test_a_window_too_short_says_so_as_a_plan_did(world):
    grouped = network.network_stock(PROGRAM, item=world["rutf"], window_days=14)
    planned = network.network_stock(PROGRAM, item=world["rutf"], window_days=14, per_point=True)
    assert [r["amc"] for r in grouped] == [r["amc"] for r in planned]
    assert "too short" in grouped[-1]["amc"].reasons[0]


def test_counts_against_the_ledger_are_stock_on_hand_per_point(world):
    rows = network.network_stock(PROGRAM)
    found = soh.counted_against_ledger(PROGRAM, [(row["supply_point_id"], row["item"]) for row in rows])
    for row in rows:
        point = SupplyPoint.objects.get(pk=row["supply_point_id"])
        one = soh.stock_on_hand(PROGRAM, point)
        mine = found.get(point.pk)
        if one["reported"] is None:
            assert mine is None
            continue
        assert (mine["ledger_on_count_day"], mine["variance"], mine["as_of"]) == (
            one["ledger_on_count_day"],
            one["variance"],
            one["as_of"],
        ), row["name"]
    assert sum(1 for v in found.values() if v["variance"].amount != 0) >= 2


def test_the_stock_checks_are_unchanged(world, access, monkeypatch):
    """The stock checks, run grouped, are the ones per-point reads gave."""
    grouped = checks._stock(access, TODAY)

    # The reads as they were: a plan, a stock_on_hand and an exists() per point.
    def per_point_rows(program_id, **kwargs):
        return network_stock(program_id, per_point=True, **kwargs)

    def per_point_variance(program_id, pairs):
        found = {}
        for point_id, _ in pairs:
            one = soh.stock_on_hand(program_id, SupplyPoint.objects.get(pk=point_id))
            if one["reported"] is not None:
                found[point_id] = one
        return found

    def per_point_movements(program_id, ids):
        return {
            pk for pk in ids if Movement.objects.filter(Q(to_supply_point_id=pk) | Q(from_supply_point_id=pk)).exists()
        }

    network_stock = network.network_stock
    monkeypatch.setattr(checks.network, "network_stock", per_point_rows)
    monkeypatch.setattr(checks.soh, "counted_against_ledger", per_point_variance)
    monkeypatch.setattr(checks, "_points_with_movements", per_point_movements)
    planned = checks._stock(access, TODAY)

    assert grouped == planned
    kinds = {c["kind"] for c in grouped}
    assert {"stock_variance", "stock_never_reported", "stock_below_minimum"} <= kinds


# ---- cost: constant in the number of workers --------------------------------


def test_the_network_stock_operation_costs_the_same_whatever_the_workers(world, access):
    """The portfolio map, the portfolio row and the MCP tool read this."""
    few, many = _constant_in_workers(world, lambda: call_operation("network_stock", access, {}))
    assert many == few


def test_the_network_stock_operation_for_one_item_costs_the_same(world, access):
    """The item page, a durable order's whereabouts panel, and the map's cover read this."""
    item_id = world["rutf"].pk
    few, many = _constant_in_workers(world, lambda: call_operation("network_stock", access, {"item_id": item_id}))
    assert many == few


def test_the_maps_cover_costs_the_same(world, access):
    few, many = _constant_in_workers(world, lambda: _cover(access, "rutf"))
    assert many == few


def test_the_checks_cost_the_same(world, access):
    """The checks page, and the hourly alert that runs them."""
    few, many = _constant_in_workers(world, lambda: call_operation("checks_list", access, {}))
    assert many == few


def test_the_chain_summary_costs_the_same(world, access):
    """The overview and the alert summary."""
    few, many = _constant_in_workers(
        world, lambda: call_operation("chain_summary", access, {"commodity_slug": "rutf"})
    )
    assert many == few
