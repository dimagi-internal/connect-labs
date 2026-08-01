"""Shelf life — the batch behind an expiry warning, and where it should go.

Detecting a loss and reporting a number is not managing it: a store officer
cannot pick stock off a shelf without a batch number, and cannot move it
without a destination that can actually consume it in time.
"""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from connect_labs.supply.models import Shipment, ShipmentLine, SupplyEvent, SupplyNode
from connect_labs.supply.services import cover, exceptions

from .factories import CaseloadEstimateFactory, ContractFactory, SupplierOrgFactory, SupplyNodeFactory

pytestmark = pytest.mark.django_db

BORNO = "NGA-2839"


def _site(name, children, adm1=BORNO):
    CaseloadEstimateFactory(adm1_code=adm1, children_sam=children)
    return SupplyNodeFactory(name=name, kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=adm1)


def _delivered_batch(node, cartons, *, batch, expires_in_days, reference):
    org = SupplierOrgFactory()
    origin = SupplyNode.objects.filter(name="Shelf origin").first() or SupplyNodeFactory(
        name="Shelf origin", kind=SupplyNode.Kind.WAREHOUSE
    )
    shipment = Shipment.objects.create(
        reference=reference,
        contract=ContractFactory(org=org),
        origin=origin,
        destination=node,
        quantity=cartons,
        unit="cartons",
        eta=timezone.now(),
        status=Shipment.Status.DELIVERED,
    )
    ShipmentLine.objects.create(
        shipment=shipment,
        batch_lot=batch,
        gtin="1",
        quantity=cartons,
        unit="cartons",
        expiry_date=date.today() + timedelta(days=expires_in_days),
    )
    SupplyEvent.objects.create(
        shipment=shipment,
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": cartons, "uom": "CT"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )
    return shipment


def test_the_shelf_life_profile_is_in_fefo_order():
    """First expired, first out — the sequence a store should despatch in."""
    site = _site("Shelf FEFO", 4330)
    _delivered_batch(site, 500, batch="LOT-LATE", expires_in_days=200, reference="SHELF-1")
    _delivered_batch(site, 500, batch="LOT-SOON", expires_in_days=40, reference="SHELF-2")
    _delivered_batch(site, 500, batch="LOT-MID", expires_in_days=120, reference="SHELF-3")

    profile = cover.shelf_life_profile(site)
    assert [r["batch_lot"] for r in profile] == ["LOT-SOON", "LOT-MID", "LOT-LATE"]
    assert profile[0]["days_left"] == 40
    assert profile[0]["expired"] is False


def test_a_destination_must_be_able_to_consume_the_stock_in_the_time_left():
    """A suggestion that moves stock somewhere it will expire anyway is worse
    than none — it launders the loss into somebody else's store."""
    holder = _site("Shelf holder", 100)  # tiny caseload, cannot consume
    # A big site that CAN consume 2,000 cartons inside 60 days.
    big = _site("Shelf big", 20000, adm1="NGA-2873")
    # A site far too small to consume it in time.
    _site("Shelf tiny", 40, adm1="SDN-881")

    dest = cover.fefo_destination(holder, 2000, date.today() + timedelta(days=60))
    assert dest is not None
    assert dest["node_name"] == big.name
    assert dest["can_consume"] >= 2000


def test_no_destination_is_offered_when_nobody_can_consume_it_in_time():
    holder = _site("Shelf stuck", 100)
    _site("Shelf also tiny", 50, adm1="NGA-2873")

    assert cover.fefo_destination(holder, 500000, date.today() + timedelta(days=5)) is None


def test_an_already_expired_batch_gets_no_destination():
    holder = _site("Shelf expired", 100)
    _site("Shelf receiver", 20000, adm1="NGA-2873")

    assert cover.fefo_destination(holder, 100, date.today() - timedelta(days=1)) is None


def test_the_expiry_exception_names_the_batch_and_where_it_should_go():
    """The row used to report a quantity and a date and stop.

    Neither is actionable: a store officer needs the batch to pick it, and a
    destination to move it to.
    """
    holder = _site("Expiry holder", 60)
    _delivered_batch(holder, 4000, batch="LOT-AT-RISK", expires_in_days=45, reference="SHELF-X1")
    _site("Expiry receiver", 30000, adm1="NGA-2873")

    rows = exceptions.expiry_exceptions()
    row = [r for r in rows if r["node_name"] == holder.name][0]

    assert row["batch_lot"] == "LOT-AT-RISK"
    assert row["batch_days_left"] == 45
    assert row["fefo_destination"] is not None
    assert row["fefo_destination"]["node_name"] == "Expiry receiver"
    # The advice names the batch and the destination rather than describing them.
    assert "LOT-AT-RISK" in row["action"]
    assert "Expiry receiver" in row["action"]
    assert "LOT-AT-RISK" in row["why"]


def test_an_unmovable_loss_says_so_rather_than_advising_the_impossible():
    holder = _site("Expiry stuck", 60)
    _delivered_batch(holder, 900000, batch="LOT-DOOMED", expires_in_days=3, reference="SHELF-X2")

    rows = exceptions.expiry_exceptions()
    row = [r for r in rows if r["node_name"] == holder.name][0]
    assert row["fefo_destination"] is None
    assert "loss is already committed" in row["action"]
