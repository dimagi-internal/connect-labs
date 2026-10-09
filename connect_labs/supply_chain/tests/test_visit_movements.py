"""Consumption posted from a visit, and the reversal that cancels it.

THIS REPOSITORY IS PUBLIC. Every name and figure here is invented.

A reversal is a `consumption` INTO the worker's point naming the row it
cancels (design §3.2). These tests pin that the ledger, the consumption
total and the monthly rate all read the pair as nothing having happened.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.db import IntegrityError, transaction

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.models import Commodity, Item, Movement, SupplyPoint
from connect_labs.supply_chain.operations import call_operation, record
from connect_labs.supply_chain.stock.services import ledger, posting, resupply
from connect_labs.supply_chain.values import Quantity

pytestmark = pytest.mark.django_db

PROGRAM = 10511
OPP = 10511
TODAY = date(2026, 9, 28)


@pytest.fixture
def item():
    rutf = Commodity.objects.create(
        scope_key=f"prog:{PROGRAM}",
        slug="rutf",
        name="RUTF",
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )
    return Item.objects.create(
        scope_key=f"prog:{PROGRAM}",
        sku="rutf-150",
        name="RUTF 150",
        commodity=rutf,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
    )


@pytest.fixture
def store():
    return SupplyPoint.objects.create(
        program_id=PROGRAM, slug="partner-store", name="Partner store", kind="regional_store", source="we_recorded"
    )


@pytest.fixture
def worker(store):
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        slug="user-10511-worker-acacia",
        name="worker-acacia",
        kind="user_held",
        connect_username="worker-acacia",
        parent=store,
        source="connect_visit",
    )


def _issue(item, store, worker, sachets, on):
    return Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind="distribution",
        occurred_on=on,
        from_supply_point=store,
        to_supply_point=worker,
        item=item,
        commodity=item.commodity,
        quantity=Decimal(sachets),
        quantity_unit="sachet",
        source="we_recorded",
    )


def _dispense(item, worker, sachets, on, visit_id, estimated=False):
    return posting.post_visit_consumption(
        program_id=PROGRAM,
        opportunity_id=OPP,
        point=worker,
        item=item,
        quantity=Decimal(str(sachets)),
        unit="sachet",
        occurred_on=on,
        visit_id=visit_id,
        estimated=estimated,
    )


def test_a_visit_posts_consumption_out_of_the_worker(item, store, worker):
    _issue(item, store, worker, 100, TODAY - timedelta(days=10))
    movement = _dispense(item, worker, 14, TODAY - timedelta(days=2), "9001")

    assert (movement.kind, movement.source, movement.visit_id) == ("consumption", "connect_visit", "9001")
    assert (movement.from_supply_point_id, movement.to_supply_point_id) == (worker.pk, None)
    assert ledger.balance(PROGRAM, worker, item=item) == Quantity(Decimal("86"), "sachet")


def test_a_reversal_puts_the_stock_back_and_names_what_it_cancels(item, store, worker):
    _issue(item, store, worker, 100, TODAY - timedelta(days=10))
    original = _dispense(item, worker, 14, TODAY - timedelta(days=2), "9001")

    reversal = posting.post_visit_reversal(original, reason="visit rejected")

    assert reversal.reverses_id == original.pk
    assert (reversal.from_supply_point_id, reversal.to_supply_point_id) == (None, worker.pk)
    assert reversal.kind == "consumption" and reversal.visit_id == "9001"
    # Dated with the visit it cancels, so a monthly rate over any window nets exactly.
    assert reversal.occurred_on == original.occurred_on
    assert ledger.balance(PROGRAM, worker, item=item) == Quantity(Decimal("100"), "sachet")


def test_consumption_by_unit_nets_reversals_out(item, store, worker):
    rejected = _dispense(item, worker, 14, TODAY, "9001")
    _dispense(item, worker, 10, TODAY, "9002")
    posting.post_visit_reversal(rejected, reason="visit rejected")

    assert Movement.objects.for_program(PROGRAM).consumption_by_unit() == {"sachet": Decimal("10")}


def test_average_monthly_consumption_nets_reversals_out(item, store, worker):
    _issue(item, store, worker, 500, TODAY - timedelta(days=40))
    _dispense(item, worker, 30, TODAY - timedelta(days=35), "9001")
    rejected = _dispense(item, worker, 60, TODAY - timedelta(days=5), "9002")
    posting.post_visit_reversal(rejected, reason="visit rejected")

    amc = resupply.average_monthly_consumption(PROGRAM, worker, item=item, as_of=TODAY, window_days=90)

    # 30 sachets over the 36 days since the first dispensing that stands, per 30 days.
    assert amc == Quantity(Decimal("25.0000"), "sachet")


def test_one_consumption_per_visit_and_item(item, store, worker):
    _dispense(item, worker, 14, TODAY, "9001")
    with pytest.raises(IntegrityError), transaction.atomic():
        _dispense(item, worker, 5, TODAY, "9001")


def test_one_reversal_per_consumption(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001")
    posting.post_visit_reversal(original, reason="visit rejected")
    with pytest.raises(IntegrityError), transaction.atomic():
        posting.post_visit_reversal(original, reason="again")


def test_a_reversal_must_come_back_into_a_point(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001")
    with pytest.raises(IntegrityError), transaction.atomic():
        Movement.objects.create(
            program_id=PROGRAM,
            kind="consumption",
            occurred_on=TODAY,
            from_supply_point=worker,
            item=item,
            commodity=item.commodity,
            quantity=Decimal("14"),
            quantity_unit="sachet",
            visit_id="9001",
            reverses=original,
            source="connect_visit",
        )


def test_negative_consumption_is_still_refused(item, worker):
    with pytest.raises(IntegrityError), transaction.atomic():
        Movement.objects.create(
            program_id=PROGRAM,
            kind="consumption",
            occurred_on=TODAY,
            from_supply_point=worker,
            item=item,
            commodity=item.commodity,
            quantity=Decimal("-3"),
            quantity_unit="sachet",
            source="connect_visit",
        )


def test_only_a_visits_consumption_can_be_reversed(item, store, worker):
    issue = _issue(item, store, worker, 100, TODAY)
    with pytest.raises(ValueError, match="only a visit's consumption"):
        posting.post_visit_reversal(issue, reason="nope")


def test_movement_record_cannot_forge_a_visit_or_a_reversal(item, store, worker):
    """Refused outright now, rather than quietly dropped: an undeclared field is
    a refusal naming it (docs/superpowers/specs/2026-10-02-supply-tracking-reality.md,
    ruling 1), and nothing is written."""
    import jsonschema

    original = _dispense(item, worker, 14, TODAY, "9001")
    da = SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)
    before = Movement.objects.count()

    with pytest.raises(jsonschema.ValidationError, match="'estimated', 'reverses_id', 'visit_id'"):
        call_operation(
            "movement_record",
            da,
            {
                "data": {
                    "kind": "loss",
                    "occurred_on": TODAY.isoformat(),
                    "from_supply_point_id": worker.pk,
                    "commodity_slug": "rutf",
                    "item_id": item.pk,
                    "quantity": "1",
                    "quantity_unit": "sachet",
                    "source": "we_recorded",
                    "visit_id": "9001",
                    "reverses_id": original.pk,
                    "estimated": True,
                }
            },
        )

    assert Movement.objects.count() == before


def test_the_wire_says_where_a_movement_came_from(item, store, worker):
    original = _dispense(item, worker, 14, TODAY, "9001", estimated=True)
    reversal = posting.post_visit_reversal(original, reason="visit rejected")

    wire = record(reversal)

    assert wire["visit_id"] == "9001"
    assert wire["estimated"] is True
    assert wire["reverses_movement_id"] == original.pk
