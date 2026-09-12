"""The stock ledger, stock on hand, and resupply -- over real tables.

These exercise the arithmetic that a wrong answer would quietly corrupt: the
balance sign convention, the refusal to add cartons to sachets, and the
refusal to project a monthly rate from a fortnight of data.
"""

from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction

from connect_labs.supply_chain.models import (
    Commodity,
    Distribution,
    DistributionLine,
    Item,
    Movement,
    StockCount,
    SupplyPoint,
    scope_key,
)
from connect_labs.supply_chain.stock.services import ledger, resupply, soh
from connect_labs.supply_chain.values import Quantity, Unconfirmed

PROGRAM = 10501
OPP = 10501
TODAY = date(2026, 9, 12)

pytestmark = pytest.mark.django_db


@pytest.fixture
def rutf():
    return Commodity.objects.create(
        scope_key=scope_key(program_id=PROGRAM),
        slug="rutf",
        name="Ready-to-use therapeutic food",
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=150,
        base_unit_grams=92,
    )


@pytest.fixture
def specified_item(rutf):
    """A trade item that states its pack configuration."""
    return Item.objects.create(
        scope_key=scope_key(program_id=PROGRAM),
        sku="harmattan-rutf-92g",
        name="Harmattan RUTF 92 g",
        commodity=rutf,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=144,
        base_unit_grams=92,
    )


@pytest.fixture
def unspecified_item(rutf):
    """A trade item whose supplier never stated sachets per carton.

    This is not a hypothetical: it is the shape of the real quote that
    blocked the first comparison, and it is why balances can be Unconfirmed.
    """
    return Item.objects.create(
        scope_key=scope_key(program_id=PROGRAM),
        sku="dabs-rutf",
        name="DABS RUTF",
        commodity=rutf,
        base_unit="sachet",
        pack_unit="carton",
        base_per_pack=None,
    )


def _point(slug, kind="central_store", **kwargs):
    return SupplyPoint.objects.create(
        program_id=PROGRAM,
        slug=slug,
        name=slug.replace("-", " ").title(),
        kind=kind,
        source="we_recorded",
        **kwargs,
    )


@pytest.fixture
def store():
    return _point("central-store")


@pytest.fixture
def worker():
    return _point(
        "flw-amina",
        kind="user_held",
        opportunity_id=OPP,
        connect_username="flw-amina",
        min_months_of_stock=Decimal("1"),
        max_months_of_stock=Decimal("2"),
    )


def _move(kind, commodity, quantity, unit, item=None, frm=None, to=None, on=TODAY):
    return Movement.objects.create(
        program_id=PROGRAM,
        opportunity_id=OPP,
        kind=kind,
        occurred_on=on,
        from_supply_point=frm,
        to_supply_point=to,
        item=item,
        commodity=commodity,
        quantity=Decimal(str(quantity)),
        quantity_unit=unit,
        source="we_recorded",
    )


class TestBalance:
    def test_receipt_then_issue_nets_out(self, rutf, specified_item, store, worker):
        _move("receipt", rutf, 300, "carton", item=specified_item, to=store)
        _move("distribution", rutf, 45, "carton", item=specified_item, frm=store, to=worker)

        assert ledger.balance(PROGRAM, store, item=specified_item) == Quantity(Decimal("255.0000"), "carton")
        assert ledger.balance(PROGRAM, worker, item=specified_item) == Quantity(Decimal("45.0000"), "carton")

    def test_a_point_with_no_movements_is_zero_not_unconfirmed(self, specified_item, store):
        balance = ledger.balance(PROGRAM, store, item=specified_item)
        assert balance == Quantity(Decimal("0"), "carton")

    def test_consumption_leaves_the_network(self, rutf, specified_item, store, worker):
        _move("receipt", rutf, 10, "carton", item=specified_item, to=store)
        _move("distribution", rutf, 10, "carton", item=specified_item, frm=store, to=worker)
        _move("consumption", rutf, 144, "sachet", item=specified_item, frm=worker)

        # 10 cartons in, 144 sachets (= 1 carton at 144/carton) dispensed.
        assert ledger.balance(PROGRAM, worker, item=specified_item) == Quantity(Decimal("9"), "carton")

    def test_adjustment_may_be_negative(self, rutf, specified_item, store):
        _move("receipt", rutf, 100, "carton", item=specified_item, to=store)
        _move("adjustment", rutf, -7, "carton", item=specified_item, to=store)
        assert ledger.balance(PROGRAM, store, item=specified_item) == Quantity(Decimal("93.0000"), "carton")

    def test_a_receipt_may_not_be_negative(self, rutf, specified_item, store):
        """Only adjustments may be negative. A negative receipt is a sign
        error, and accepting one corrupts every balance downstream."""
        with pytest.raises(IntegrityError, match="movement_positive_unless_adjustment"):
            with transaction.atomic():
                _move("receipt", rutf, -5, "carton", item=specified_item, to=store)

    def test_mixed_units_without_a_pack_spec_are_unconfirmed(self, rutf, unspecified_item, store, worker):
        """The real finding: the store counts cartons, the field counts sachets,
        and DABS never said how many sachets are in a carton."""
        _move("receipt", rutf, 300, "carton", item=unspecified_item, to=store)
        _move("distribution", rutf, 45, "carton", item=unspecified_item, frm=store, to=worker)
        _move("consumption", rutf, 5700, "sachet", item=unspecified_item, frm=worker)

        balance = ledger.balance(PROGRAM, worker, item=unspecified_item)
        assert isinstance(balance, Unconfirmed)
        assert any("sachet" in reason and "carton" in reason for reason in balance.reasons)

    def test_movements_are_append_only(self, rutf, specified_item, store):
        movement = _move("receipt", rutf, 100, "carton", item=specified_item, to=store)
        movement.quantity = Decimal("1")
        with pytest.raises(ValueError, match="append-only"):
            movement.save()

    def test_a_movement_must_touch_a_point(self, rutf, specified_item):
        """A movement with neither side set changes no balance and would sit
        in the ledger as an untraceable quantity."""
        with pytest.raises(IntegrityError, match="movement_touches_a_point"):
            with transaction.atomic():
                _move("receipt", rutf, 5, "carton", item=specified_item)

    def test_as_of_replays_the_ledger(self, rutf, specified_item, store):
        _move("receipt", rutf, 100, "carton", item=specified_item, to=store, on=date(2026, 6, 1))
        _move("issue", rutf, 40, "carton", item=specified_item, frm=store, on=date(2026, 8, 1))
        assert ledger.balance(PROGRAM, store, item=specified_item, on_date=date(2026, 7, 1)) == Quantity(
            Decimal("100.0000"), "carton"
        )


class TestBatchesAndFefo:
    def test_batches_sort_by_soonest_expiry(self, rutf, specified_item, store):
        _move("receipt", rutf, 100, "carton", item=specified_item, to=store)
        Movement.objects.filter(batch="").update(batch="LATER", expiry=date(2028, 6, 30))
        _move("receipt", rutf, 50, "carton", item=specified_item, to=store)
        Movement.objects.filter(batch="").update(batch="SOONER", expiry=date(2028, 5, 31))

        rows = ledger.balance_by_batch(PROGRAM, store, item=specified_item)
        assert [row["batch"] for row in rows] == ["SOONER", "LATER"]
        assert rows[0]["quantity"] == Decimal("50.0000")


class TestCommittedAndAvailable:
    def test_an_unexecuted_distribution_line_is_committed_not_gone(self, rutf, specified_item, store, worker):
        _move("receipt", rutf, 100, "carton", item=specified_item, to=store)
        distribution = Distribution.objects.create(
            program_id=PROGRAM,
            opportunity_id=OPP,
            supply_point=store,
            distributed_on=TODAY,
            source="partner_reported",
        )
        DistributionLine.objects.create(
            distribution=distribution,
            to_supply_point=worker,
            item=specified_item,
            quantity=Decimal("15"),
            quantity_unit="carton",
        )

        position = ledger.position(PROGRAM, store, item=specified_item)
        assert position["on_hand"] == Quantity(Decimal("100.0000"), "carton")
        assert position["committed"] == Quantity(Decimal("15.0000"), "carton")
        assert position["available"] == Quantity(Decimal("85.0000"), "carton")


class TestStockOnHand:
    def test_ledger_and_count_are_both_reported(self, rutf, specified_item, store):
        _move("receipt", rutf, 100, "carton", item=specified_item, to=store)
        StockCount.objects.create(
            program_id=PROGRAM,
            supply_point=store,
            item=specified_item,
            commodity=rutf,
            kind="physical_count",
            counted_on=TODAY,
            quantity=Decimal("96"),
            quantity_unit="carton",
            source="we_recorded",
        )
        result = soh.stock_on_hand(PROGRAM, store, item=specified_item)
        assert result["ledger"] == Quantity(Decimal("100.0000"), "carton")
        assert result["reported"] == Quantity(Decimal("96.0000"), "carton")
        assert result["variance"] == Quantity(Decimal("-4.0000"), "carton")
        assert result["basis"] == "disagreement"

    def test_no_count_means_the_ledger_stands_alone(self, rutf, specified_item, store):
        _move("receipt", rutf, 100, "carton", item=specified_item, to=store)
        result = soh.stock_on_hand(PROGRAM, store, item=specified_item)
        assert result["reported"] is None
        assert result["basis"] == "ledger"

    def test_a_worker_count_in_sachets_against_a_carton_ledger_is_unconfirmed(
        self, rutf, unspecified_item, store, worker
    ):
        _move("distribution", rutf, 10, "carton", item=unspecified_item, frm=store, to=worker)
        StockCount.objects.create(
            program_id=PROGRAM,
            supply_point=worker,
            item=unspecified_item,
            commodity=rutf,
            kind="self_reported",
            counted_on=TODAY,
            quantity=Decimal("900"),
            quantity_unit="sachet",
            source="commcare_form",
            connect_username="flw-amina",
        )
        result = soh.stock_on_hand(PROGRAM, worker, item=unspecified_item)
        assert isinstance(result["variance"], Unconfirmed)
        assert result["basis"] == "disagreement"

    def test_an_override_needs_a_reason(self, rutf, specified_item, store):
        count = StockCount(
            program_id=PROGRAM,
            supply_point=store,
            item=specified_item,
            commodity=rutf,
            kind="override",
            counted_on=TODAY,
            quantity=Decimal("5"),
            quantity_unit="carton",
            source="we_recorded",
        )
        with pytest.raises(ValidationError, match="say why"):
            count.full_clean()


class TestResupply:
    def _dispense_daily(self, rutf, item, worker, days, per_day, end=TODAY):
        for offset in range(days):
            _move(
                "consumption",
                rutf,
                per_day,
                "sachet",
                item=item,
                frm=worker,
                on=end - timedelta(days=offset),
            )

    def test_a_short_history_refuses_to_project_a_monthly_rate(self, rutf, specified_item, store, worker):
        _move("distribution", rutf, 50, "carton", item=specified_item, frm=store, to=worker)
        self._dispense_daily(rutf, specified_item, worker, days=14, per_day=20)

        amc = resupply.average_monthly_consumption(PROGRAM, worker, item=specified_item, as_of=TODAY)
        assert isinstance(amc, Unconfirmed)
        assert "14 days" in amc.reasons[0]

    def test_ninety_days_of_dispensing_gives_a_rate_and_a_plan(self, rutf, specified_item, store, worker):
        _move(
            "distribution",
            rutf,
            50,
            "carton",
            item=specified_item,
            frm=store,
            to=worker,
            on=TODAY - timedelta(days=95),
        )
        self._dispense_daily(rutf, specified_item, worker, days=90, per_day=48)

        plan = resupply.plan(PROGRAM, worker, item=specified_item, as_of=TODAY)
        # 48 sachets/day over 90 days = 1440 sachets/month = 10 cartons at 144.
        assert plan["amc"].unit == "sachet"
        assert plan["amc"].amount == pytest.approx(Decimal("1440"), rel=Decimal("0.01"))
        # 50 cartons in, 4320 sachets (30 cartons) out -> 20 cartons left.
        assert plan["on_hand"] == Quantity(Decimal("20"), "carton")
        assert plan["months_of_stock"] == pytest.approx(Decimal("2"), rel=Decimal("0.01"))
        assert plan["status"] == "ok"

    def test_below_the_minimum_band_is_flagged(self, rutf, specified_item, store, worker):
        _move(
            "distribution",
            rutf,
            31,
            "carton",
            item=specified_item,
            frm=store,
            to=worker,
            on=TODAY - timedelta(days=95),
        )
        self._dispense_daily(rutf, specified_item, worker, days=90, per_day=48)

        plan = resupply.plan(PROGRAM, worker, item=specified_item, as_of=TODAY)
        assert plan["on_hand"] == Quantity(Decimal("1"), "carton")
        assert plan["status"] == "below_min"
        # Top of the band is 2 months = 20 cartons; 1 is here, so send 19.
        assert plan["resupply_quantity"] == Quantity(Decimal("19"), "carton")
        assert plan["reorder_point"] == Quantity(Decimal("10"), "carton")

    def test_nothing_dispensed_yet_is_unknown_not_zero(self, rutf, specified_item, store, worker):
        _move("distribution", rutf, 20, "carton", item=specified_item, frm=store, to=worker)
        plan = resupply.plan(PROGRAM, worker, item=specified_item, as_of=TODAY)
        assert plan["status"] == "unknown"
        assert isinstance(plan["months_of_stock"], Unconfirmed)
        assert isinstance(plan["amc"], Unconfirmed)


class TestSupplyPoint:
    def test_a_user_held_point_must_name_its_user(self):
        point = SupplyPoint(program_id=PROGRAM, slug="nobody", name="Nobody", kind="user_held", source="we_recorded")
        with pytest.raises(ValidationError, match="Connect user"):
            point.full_clean()

    def test_a_store_needs_no_user(self, store):
        store.full_clean()
