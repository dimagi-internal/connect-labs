"""The cover derivation — stock, burn, weeks of cover, children at risk.

These are the figures the command centre ranks its queue on and the partner
surface plans against, and until now the equivalent arithmetic lived in
``tab_command.jsx`` where no test could reach it. Every assertion here is
against a hand-computed expectation, not against whatever the code happens to
return.
"""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from django.utils import timezone

from connect_labs.supply import gs1
from connect_labs.supply.models import WEEKS_PER_MONTH, Shipment, ShipmentLine, SupplyEvent, SupplyNode
from connect_labs.supply.services import cover

from .factories import CaseloadEstimateFactory, ContractFactory, SupplierOrgFactory, SupplyNodeFactory

pytestmark = pytest.mark.django_db

BORNO = "NGA-2839"


def _caseload(children=4330, adm1_code=BORNO):
    return CaseloadEstimateFactory(adm1_code=adm1_code, children_sam=children)


def _receive(node, cartons, shipment=None, when=None):
    return SupplyEvent.objects.create(
        shipment=shipment,
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=when or timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": cartons, "uom": "cartons"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )


def _despatch(node, cartons):
    return SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.DEPARTING,
        event_time=timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": cartons, "uom": "cartons"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )


def test_stock_on_hand_is_receipts_minus_despatches():
    node = SupplyNodeFactory(name="Hub", kind=SupplyNode.Kind.DISTRIBUTION_HUB, adm1_code=BORNO)
    _receive(node, 5000)
    _receive(node, 1200)
    _despatch(node, 900)
    assert cover.stock_on_hand(node) == Decimal("5300")


def test_a_ct_quantity_row_is_counted_as_cartons():
    """CT is what the EPCIS path and the hand-keyed webform actually write.

    Only "cartons" and "EA" were recognised, so a CT row summed to zero — and a
    zero total then fell back to the shipment's ADVISED quantity. A short
    receipt therefore banked the full advice: 840 counted against a 900 advice
    reported 900 on hand, contradicting the discrepancy raised from the same
    event.
    """
    node = SupplyNodeFactory(name="CT Hub", kind=SupplyNode.Kind.DISTRIBUTION_HUB, adm1_code=BORNO)
    SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": 840, "uom": "CT"}],
        source_tier=SupplyEvent.SourceTier.PORTAL,
    )
    assert cover.stock_on_hand(node) == Decimal("840")


def test_a_short_receipt_banks_what_was_counted_not_what_was_advised():
    org = SupplierOrgFactory()
    contract = ContractFactory(org=org)
    origin = SupplyNodeFactory(name="Origin hub", kind=SupplyNode.Kind.DISTRIBUTION_HUB)
    node = SupplyNodeFactory(name="Short site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    shipment = Shipment.objects.create(
        reference="SHP-TEST-SHORT",
        contract=contract,
        origin=origin,
        destination=node,
        quantity=900,
        unit="cartons",
        eta=timezone.now(),
    )
    SupplyEvent.objects.create(
        shipment=shipment,
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": 840, "uom": "CT"}],
        source_tier=SupplyEvent.SourceTier.PORTAL,
    )
    assert cover.stock_on_hand(node) == Decimal("840")


def test_an_explicit_zero_receipt_does_not_bank_the_whole_consignment():
    """A row saying zero is a measurement, not a missing value.

    The fallback fired on a summed zero rather than on the absence of any row,
    so a receipt recording that nothing came off the truck credited the site
    with the entire advised consignment.
    """
    org = SupplierOrgFactory()
    contract = ContractFactory(org=org)
    origin = SupplyNodeFactory(name="Origin hub 2", kind=SupplyNode.Kind.DISTRIBUTION_HUB)
    node = SupplyNodeFactory(name="Empty truck site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    shipment = Shipment.objects.create(
        reference="SHP-TEST-ZERO",
        contract=contract,
        origin=origin,
        destination=node,
        quantity=500,
        unit="cartons",
        eta=timezone.now(),
    )
    SupplyEvent.objects.create(
        shipment=shipment,
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": 0, "uom": "CT"}],
        source_tier=SupplyEvent.SourceTier.PORTAL,
    )
    assert cover.stock_on_hand(node) == Decimal("0")
    # ...while a check-in that carried no quantity row at all still falls back,
    # because the lowest tier is a reference and a place.
    bare_site = SupplyNodeFactory(name="Bare checkin site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    bare = Shipment.objects.create(
        reference="SHP-TEST-BARE",
        contract=contract,
        origin=origin,
        destination=bare_site,
        quantity=300,
        unit="cartons",
        eta=timezone.now(),
    )
    SupplyEvent.objects.create(
        shipment=bare,
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=bare_site,
        quantity_list=[],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )
    assert cover.stock_on_hand(bare_site) == Decimal("300")


def test_a_node_with_no_district_has_no_cover():
    """A port sits on the route but is answerable for no children."""
    port = SupplyNodeFactory(name="Port", kind=SupplyNode.Kind.PORT, adm1_code="")
    _receive(port, 20000)
    assert cover.cover_for_node(port) is None


def test_hub_carries_the_whole_district_and_sites_split_it():
    _caseload(children=4330)
    hub = SupplyNodeFactory(name="Hub", kind=SupplyNode.Kind.DISTRIBUTION_HUB, adm1_code=BORNO)
    site_a = SupplyNodeFactory(name="Site A", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    site_b = SupplyNodeFactory(name="Site B", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)

    # The hub supplies every site in the district, so it carries all of it.
    assert cover.served_children(hub) == 4330
    # The sites admit children themselves, so they share it.
    assert cover.served_children(site_a) == 4330 / 2
    assert cover.served_children(site_b) == 4330 / 2


def test_weeks_of_cover_matches_a_hand_computation():
    _caseload(children=4330)
    site = SupplyNodeFactory(name="Site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    _receive(site, 2000)

    # One site, so it carries the whole district: 4330 admissions a month.
    # Weekly burn = 4330 / 4.33 = 1000 cartons (one carton per full course).
    # Weeks of cover = 2000 / 1000 = 2.0.
    result = cover.cover_for_node(site)
    assert result["weekly_burn"] == pytest.approx(1000.0, abs=0.1)
    assert result["weeks_of_cover"] == pytest.approx(2.0, abs=0.05)
    assert result["stockout_on"] == (date.today() + timedelta(days=14)).isoformat()


def test_weekly_burn_uses_the_shared_carton_ladder():
    """Burn must go through gs1, not restate the conversion."""
    _caseload(children=4330)
    site = SupplyNodeFactory(name="Site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    expected = (4330 / WEEKS_PER_MONTH) * gs1.CARTONS_PER_CHILD_TREATED
    assert float(cover.weekly_burn(site)) == pytest.approx(expected, abs=0.1)


def test_the_partner_and_the_centre_read_the_same_number():
    """The two surfaces consume one derivation, so they cannot disagree.

    This is the narrative's own requirement — a partner told they have eleven
    days while the centre reads three weeks is worse than neither having the
    figure — and it holds structurally because there is only one implementation.
    """
    _caseload(children=4330)
    site = SupplyNodeFactory(name="Site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    _receive(site, 2000)

    centre_view = cover.cover_for_node(site)
    partner_view = next(r for r in cover.cover_by_node() if r["node_id"] == site.id)
    assert centre_view["weeks_of_cover"] == partner_view["weeks_of_cover"]
    assert centre_view["stockout_on"] == partner_view["stockout_on"]


def test_cover_by_node_ranks_worst_first():
    _caseload(children=4330)
    thin = SupplyNodeFactory(name="Thin", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    deep = SupplyNodeFactory(name="Deep", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    _receive(thin, 500)
    _receive(deep, 9000)
    rows = cover.cover_by_node()
    assert [r["node_name"] for r in rows] == ["Thin", "Deep"]


# --- children at risk -------------------------------------------------------


def test_a_delay_absorbed_by_stock_costs_nobody_a_course():
    _caseload(children=4330)
    site = SupplyNodeFactory(name="Site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    _receive(site, 2000)  # two weeks of cover
    assert cover.children_at_risk(site, delay_days=5) == 0


def test_children_at_risk_counts_only_the_days_after_the_store_runs_dry():
    _caseload(children=4330)
    site = SupplyNodeFactory(name="Site", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    _receive(site, 2000)  # 14 days of cover

    # 21 days late = 7 days dry. Daily admissions = 4330 / (4.33 * 7) ~= 142.9.
    # 7 * 142.9 ~= 1000 children who miss a full course.
    assert cover.children_at_risk(site, delay_days=21) == pytest.approx(1000, abs=5)


def test_equal_tonnage_ranks_by_caseload_not_by_lateness():
    """The claim the exception queue is built on.

    Two sites, identical stock and identical delay; the one serving the larger
    caseload costs more children. Tonnage x lateness cannot tell them apart —
    which is precisely why severity moved out of the browser.
    """
    _caseload(children=8660, adm1_code="NGA-2839")
    _caseload(children=866, adm1_code="NGA-2873")
    big = SupplyNodeFactory(name="Big caseload", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code="NGA-2839")
    small = SupplyNodeFactory(name="Small caseload", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code="NGA-2873")
    _receive(big, 1000)
    _receive(small, 1000)

    big_risk = cover.children_at_risk(big, delay_days=30)
    small_risk = cover.children_at_risk(small, delay_days=30)
    assert big_risk > small_risk


# --- expiry -----------------------------------------------------------------


def test_expiry_risk_flags_stock_the_caseload_cannot_consume():
    _caseload(children=433)  # 100 cartons a week
    org = SupplierOrgFactory(legal_name="Expiry Co")
    node = SupplyNodeFactory(name="Forward store", kind=SupplyNode.Kind.WAREHOUSE, adm1_code=BORNO)
    origin = SupplyNodeFactory(name="Origin", kind=SupplyNode.Kind.PORT, adm1_code="")
    contract = ContractFactory(org=org)
    shipment = Shipment.objects.create(
        contract=contract,
        reference="SHP-EXP-1",
        origin=origin,
        destination=node,
        quantity=Decimal("5000"),
        status=Shipment.Status.DELIVERED,
    )
    # 5000 cartons expiring in 70 days, against a burn of 100/week: only
    # ~1000 can be consumed, so ~4000 are at risk.
    ShipmentLine.objects.create(
        shipment=shipment,
        gtin="1",
        batch_lot="LOT-EXP",
        expiry_date=date.today() + timedelta(days=70),
        quantity=Decimal("5000"),
    )
    risk = cover.expiry_risk(node)
    assert risk is not None
    assert risk["cartons_at_risk"] == pytest.approx(4000, abs=50)


def test_no_expiry_risk_when_the_caseload_can_work_through_it():
    _caseload(children=4330)  # 1000 cartons a week
    org = SupplierOrgFactory(legal_name="Fast Co")
    node = SupplyNodeFactory(name="Busy hub", kind=SupplyNode.Kind.DISTRIBUTION_HUB, adm1_code=BORNO)
    origin = SupplyNodeFactory(name="Origin2", kind=SupplyNode.Kind.PORT, adm1_code="")
    contract = ContractFactory(org=org)
    shipment = Shipment.objects.create(
        contract=contract,
        reference="SHP-EXP-2",
        origin=origin,
        destination=node,
        quantity=Decimal("2000"),
        status=Shipment.Status.CONFIRMED,
    )
    ShipmentLine.objects.create(
        shipment=shipment,
        gtin="1",
        batch_lot="LOT-OK",
        expiry_date=date.today() + timedelta(days=120),
        quantity=Decimal("2000"),
    )
    assert cover.expiry_risk(node) is None


def test_a_district_splits_between_sites_by_catchment_not_evenly():
    """Identical figures down a page are the signature of a generated world.

    An even split gave all eleven of Komadugu's sites the same 214 children and
    214 cartons on every row of the distribution calendar. Sites are not the
    same size — a town hosting displaced families admits several times what a
    rural post does — so the district divides by catchment weight.
    """
    _caseload(children=4330)
    big = SupplyNodeFactory(
        name="Big town", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO, catchment_weight=3.0
    )
    small = SupplyNodeFactory(
        name="Rural post", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO, catchment_weight=1.0
    )

    assert cover.served_children(big) == pytest.approx(4330 * 0.75)
    assert cover.served_children(small) == pytest.approx(4330 * 0.25)
    # The district total is still conserved — weighting redistributes, it does
    # not invent or lose children.
    assert cover.served_children(big) + cover.served_children(small) == pytest.approx(4330)


def test_zero_weights_fall_back_to_an_even_split():
    """A district whose sites carry no weights must not divide by zero."""
    _caseload(children=1000)
    a = SupplyNodeFactory(name="A", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO, catchment_weight=0)
    SupplyNodeFactory(name="B", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO, catchment_weight=0)
    assert cover.served_children(a) == 500
