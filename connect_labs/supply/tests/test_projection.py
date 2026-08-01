"""The forward projection — what the network looks like if nobody acts.

Weeks of cover answers "what am I holding". This answers "when do I run dry",
which is a different question the moment anything is on the road, and it is the
one a pipeline call opens with.
"""
from datetime import date, timedelta

import pytest
from django.utils import timezone

from connect_labs.supply.models import Shipment, SupplyEvent, SupplyNode
from connect_labs.supply.services import cover

from .factories import CaseloadEstimateFactory, ContractFactory, SupplierOrgFactory, SupplyNodeFactory

pytestmark = pytest.mark.django_db

BORNO = "NGA-2839"


def _site(name, children=4330):
    """One site carrying a whole district: 4330/month -> 1000 cartons/week."""
    CaseloadEstimateFactory(adm1_code=BORNO, children_sam=children)
    return SupplyNodeFactory(name=name, kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)


def _receive(node, cartons):
    SupplyEvent.objects.create(
        biz_step=SupplyEvent.BizStep.RECEIVING,
        event_time=timezone.now(),
        read_point=node,
        quantity_list=[{"gtin": "1", "quantity": cartons, "uom": "CT"}],
        source_tier=SupplyEvent.SourceTier.CHECKIN,
    )


def _inbound(node, cartons, *, in_days, reference):
    org = SupplierOrgFactory()
    origin = SupplyNode.objects.filter(name="Proj origin").first() or SupplyNodeFactory(
        name="Proj origin", kind=SupplyNode.Kind.WAREHOUSE
    )
    return Shipment.objects.create(
        reference=reference,
        contract=ContractFactory(org=org),
        origin=origin,
        destination=node,
        quantity=cartons,
        unit="cartons",
        eta=timezone.now() + timedelta(days=in_days),
        status=Shipment.Status.IN_TRANSIT,
    )


def test_a_node_with_no_caseload_cannot_run_dry():
    """A port sits on the route and owes nobody a course."""
    port = SupplyNodeFactory(name="Proj port", kind=SupplyNode.Kind.PORT, adm1_code="")
    _receive(port, 20000)
    assert cover.projection_for_node(port) is None


def test_the_dry_date_matches_a_hand_computation():
    """2,000 cartons at 1,000/week is 14 days."""
    site = _site("Proj plain")
    _receive(site, 2000)

    p = cover.projection_for_node(site, as_of=date(2026, 8, 1))
    assert p["weekly_burn"] == pytest.approx(1000.0, abs=0.5)
    assert p["days_until_dry"] == 14
    assert p["dry_on"] == "2026-08-15"


def test_a_lorry_on_the_road_pushes_the_dry_date_out():
    """This is the whole point of the projection.

    Weeks of cover divides stock by burn and ignores the consignment arriving on
    Thursday — right for "what am I holding", wrong for "when do I run dry". A
    site with a week of stock and 2,000 cartons landing in five days does not go
    dry in seven.
    """
    site = _site("Proj inbound")
    _receive(site, 1000)  # one week of cover
    _inbound(site, 2000, in_days=5, reference="PROJ-IN-1")

    weeks_only = cover.cover_for_node(site, as_of=date.today())
    projected = cover.projection_for_node(site, as_of=date.today())

    assert weeks_only["weeks_of_cover"] == pytest.approx(1.0, abs=0.1)
    # Stock-only says 7 days; the pipeline says three weeks.
    assert projected["days_until_dry"] > 7
    assert projected["inbound_cartons"] == 2000
    assert projected["inbound_consignments"] == 1


def test_enough_inbound_means_the_node_never_goes_dry_in_the_horizon():
    site = _site("Proj covered")
    _receive(site, 1000)
    for i in range(5):
        _inbound(site, 1200, in_days=5 + i * 5, reference=f"PROJ-COV-{i}")

    p = cover.projection_for_node(site, as_of=date.today(), horizon_days=30)
    assert p["dry_on"] is None
    assert p["days_until_dry"] is None
    assert p["children_missed"] == 0


def test_children_missed_accrues_for_every_dry_day():
    """A carton not on the shelf is a course not given.

    Reported in the same unit as every other command-centre figure, so the
    projection can be ranked against the present-tense queue.
    """
    site = _site("Proj dry")
    _receive(site, 0)

    p = cover.projection_for_node(site, as_of=date.today(), horizon_days=7)
    # Dry from day zero, ~1000/week for 7 days.
    assert p["days_until_dry"] == 0
    assert p["children_missed"] == pytest.approx(1000, abs=30)


def test_a_delivered_consignment_is_not_counted_twice():
    """Delivered cartons are already in the event log.

    Landing them again from the shipment table would bank the same stock twice
    and quietly push every dry date out.
    """
    site = _site("Proj delivered")
    _receive(site, 1000)
    s = _inbound(site, 5000, in_days=2, reference="PROJ-DELIVERED")
    Shipment.objects.filter(pk=s.pk).update(status=Shipment.Status.DELIVERED)

    p = cover.projection_for_node(site, as_of=date.today())
    assert p["inbound_cartons"] == 0
    assert p["days_until_dry"] == 7


def test_the_network_projection_ranks_soonest_dry_first():
    CaseloadEstimateFactory(adm1_code=BORNO, children_sam=4330)
    dry_now = SupplyNodeFactory(name="Z dry now", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    later = SupplyNodeFactory(name="A dry later", kind=SupplyNode.Kind.DELIVERY_POINT, adm1_code=BORNO)
    _receive(later, 4000)

    net = cover.network_projection(as_of=date.today())
    names = [r["node_name"] for r in net["nodes"]]
    assert names.index(dry_now.name) < names.index(later.name)
    assert net["nodes_dry"] >= 1
    assert net["children_missed"] > 0
    assert net["first_dry_on"] is not None


def test_the_command_centre_receives_the_projection(client):
    """A forecast nobody's screen calls is a forecast that changes no decision."""
    import json as _json

    from django.core.management import call_command

    call_command("seed_supply_demo")
    client.post("/supply/login/", {"email": "oes-lead@oes.example", "password": "oes-demo-2026"})
    body = _json.loads(client.get("/supply/api/bootstrap/").content)

    assert "projection" in body, "the command centre payload carries no forward projection"
    proj = body["projection"]
    assert proj["horizon_days"] == cover.PROJECTION_HORIZON_DAYS
    assert proj["nodes_total"] > 0
    # The seeded world has genuinely thin sites, so the forecast must find them
    # — a projection that reports nothing is indistinguishable from one that is
    # not wired up.
    assert proj["nodes_dry"] > 0
    assert proj["children_missed"] > 0
    assert proj["first_dry_on"]
    for row in proj["nodes"]:
        assert {"node_name", "dry_on", "days_until_dry", "children_missed", "inbound_cartons"} <= set(row)
