"""Delivered performance — the loop from execution back to procurement.

Every assertion here is a claim the bid-comparison screen makes to a
procurement officer who may have to defend declining a cheaper bid.
"""
from datetime import timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from connect_labs.supply.models import Discrepancy, Milestone, Shipment, SupplyNode
from connect_labs.supply.services import performance

from .factories import ContractFactory, SupplierOrgFactory, SupplyNodeFactory

pytestmark = pytest.mark.django_db


def _leg(org, *, days_late, reference, contract=None, arrived=True):
    """One consignment that arrived (or is still out), with a plan to judge it against."""
    contract = contract or ContractFactory(org=org)
    origin = SupplyNode.objects.filter(name="Perf origin").first() or SupplyNodeFactory(
        name="Perf origin", kind=SupplyNode.Kind.WAREHOUSE
    )
    dest = SupplyNode.objects.filter(name="Perf dest").first() or SupplyNodeFactory(
        name="Perf dest", kind=SupplyNode.Kind.DELIVERY_POINT
    )
    planned = timezone.now() - timedelta(days=20)
    shipment = Shipment.objects.create(
        reference=reference,
        contract=contract,
        origin=origin,
        destination=dest,
        quantity=1000,
        unit="cartons",
        eta=planned,
    )
    Milestone.objects.create(
        shipment=shipment,
        node=dest,
        kind=Milestone.Kind.ARRIVE,
        sequence=1,
        planned_at=planned,
        estimated_at=planned + timedelta(days=days_late),
        actual_at=(planned + timedelta(days=days_late)) if arrived else None,
    )
    return shipment


def test_a_supplier_with_no_deliveries_is_reported_as_having_no_record():
    """A first-time bidder must not read as a supplier with a perfect record."""
    org = SupplierOrgFactory()
    perf = performance.supplier_performance(org)

    assert perf["has_record"] is False
    assert perf["arrivals"] == 0
    assert perf["on_time_rate"] is None
    assert "No delivery on record" in perf["basis"]


def test_on_time_rate_counts_arrivals_against_their_plan():
    org = SupplierOrgFactory()
    _leg(org, days_late=0, reference="PERF-1")
    _leg(org, days_late=0, reference="PERF-2")
    _leg(org, days_late=9, reference="PERF-3")
    _leg(org, days_late=4, reference="PERF-4")

    perf = performance.supplier_performance(org)
    assert perf["arrivals"] == 4
    assert perf["late"] == 2
    assert perf["on_time"] == 2
    assert perf["on_time_rate"] == 50.0
    assert perf["mean_days_late"] == pytest.approx(6.5, abs=0.1)
    assert perf["worst_days_late"] == pytest.approx(9.0, abs=0.1)


def test_a_consignment_still_in_transit_is_not_held_against_the_supplier():
    """Only MEASURED lateness counts.

    A leg with an estimate and no actual has not arrived. Counting a pessimistic
    ETA against a supplier would damage the record of one who then arrives on
    time — and the milestone rail already distinguishes the two.
    """
    org = SupplierOrgFactory()
    _leg(org, days_late=0, reference="PERF-A")
    _leg(org, days_late=0, reference="PERF-B")
    _leg(org, days_late=0, reference="PERF-C")
    _leg(org, days_late=30, reference="PERF-LATE-FORECAST", arrived=False)

    perf = performance.supplier_performance(org)
    assert perf["arrivals"] == 3
    assert perf["late"] == 0
    assert perf["on_time_rate"] == 100.0


def test_a_day_of_grace_is_allowed():
    """A truck arriving the following morning is not a late delivery."""
    org = SupplierOrgFactory()
    _leg(org, days_late=1, reference="PERF-G1")
    _leg(org, days_late=1, reference="PERF-G2")
    _leg(org, days_late=1, reference="PERF-G3")

    perf = performance.supplier_performance(org)
    assert perf["late"] == 0
    assert perf["on_time_rate"] == 100.0


def test_a_rate_is_withheld_below_the_evidence_floor():
    """One late delivery out of one is 100% late — true, and useless.

    The counts are still reported; it is the RATE that is withheld, because a
    rate a reader cannot rely on is worse than no rate at all.
    """
    org = SupplierOrgFactory()
    _leg(org, days_late=6, reference="PERF-THIN")

    perf = performance.supplier_performance(org)
    assert perf["has_record"] is True
    assert perf["arrivals"] == 1
    assert perf["late"] == 1
    assert perf["on_time_rate"] is None
    assert perf["short_receipt_rate"] is None


def test_short_receipts_are_counted_against_the_consignments_moved():
    org = SupplierOrgFactory()
    s1 = _leg(org, days_late=0, reference="PERF-S1")
    _leg(org, days_late=0, reference="PERF-S2")
    _leg(org, days_late=0, reference="PERF-S3")
    _leg(org, days_late=0, reference="PERF-S4")
    Discrepancy.objects.create(
        shipment=s1,
        expected_quantity=Decimal("900"),
        received_quantity=Decimal("840"),
        status=Discrepancy.Status.OPEN,
    )

    perf = performance.supplier_performance(org)
    assert perf["short_receipts"] == 1
    assert perf["cartons_short"] == pytest.approx(60.0)
    assert perf["short_receipt_rate"] == 25.0


def test_performance_by_org_matches_the_single_supplier_computation():
    """The bid comparison's batched path must not drift from the single one."""
    a = SupplierOrgFactory()
    b = SupplierOrgFactory()
    _leg(a, days_late=0, reference="PERF-BA1")
    _leg(a, days_late=7, reference="PERF-BA2")
    _leg(a, days_late=0, reference="PERF-BA3")
    _leg(b, days_late=0, reference="PERF-BB1")

    batched = performance.performance_by_org([a.id, b.id])
    for org in (a, b):
        single = performance.supplier_performance(org)
        for key in ("arrivals", "on_time", "late", "on_time_rate", "short_receipts", "has_record"):
            assert batched[org.id][key] == single[key], f"{org.legal_name} disagreed on {key}"


def test_delivery_history_is_the_evidence_behind_the_rate():
    """A rate nobody can drill into is a rate nobody will act against."""
    org = SupplierOrgFactory()
    _leg(org, days_late=0, reference="PERF-H1")
    _leg(org, days_late=11, reference="PERF-H2")

    rows = performance.delivery_history(org)
    assert {r["reference"] for r in rows} == {"PERF-H1", "PERF-H2"}
    late = [r for r in rows if not r["on_time"]][0]
    assert late["reference"] == "PERF-H2"
    assert late["days_late"] == pytest.approx(11.0, abs=0.1)
    assert late["planned_at"] and late["actual_at"]


def test_the_award_screen_receives_every_bidder_s_record(admin_client):
    """The loop is only closed if the figure reaches the screen that decides.

    A service nobody's award view calls is a service that changes no decision,
    which is precisely the state this feature exists to end.
    """
    import json as _json

    from django.core.management import call_command

    from connect_labs.supply.models import RFP

    client, _user = admin_client
    call_command("seed_supply_demo")
    rfp = RFP.objects.filter(status=RFP.Status.PUBLISHED, lots__lot_bids__isnull=False).distinct().first()
    assert rfp is not None, "the seeded world must carry a published tender with bids"

    body = _json.loads(client.get(f"/supply/api/rfps/{rfp.id}/comparison/").content)
    rows = [row for lot in body["lots"] for row in lot["lot_bids"]]
    assert rows, "the tender must carry bids to compare"
    for row in rows:
        assert "performance" in row, f"{row['org_name']} reached the award screen with no delivered record"
        perf = row["performance"]
        assert perf is not None
        assert {"has_record", "arrivals", "on_time_rate", "short_receipts", "basis"} <= set(perf)
    # And at least one bidder in the seeded world has actually delivered, so the
    # column is demonstrating the mechanism rather than rendering "—" everywhere.
    assert any(r["performance"]["has_record"] for r in rows)
