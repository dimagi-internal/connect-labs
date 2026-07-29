"""The read-only stakeholder surfaces: government observer and funder.

These roles are read-only by construction, and the government observer is
scoped to a single country. Scoping is enforced server-side so the client is
never trusted to filter another country's data out of view.
"""
import json
from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.supply.models import Shipment

from . import factories as f

pytestmark = pytest.mark.django_db


@pytest.fixture
def gov_client(db):
    from django.test import Client

    role = f.StaffRoleFactory(role="gov_observer", country="NG", user__username="gov-ng")
    client = Client()
    client.force_login(role.user)
    return client, role


@pytest.fixture
def funder_client(db):
    from django.test import Client

    role = f.StaffRoleFactory(role="funder", user__username="usg")
    client = Client()
    client.force_login(role.user)
    return client, role


@pytest.fixture
def network(db):
    """Two corridors: one Nigerian, one Ethiopian."""
    appropriation = f.AppropriationFactory(title="FY2026 Emergency Food Security", amount=10_000_000)
    ng_hub = f.SupplyNodeFactory(name="Maiduguri Hub", country="NG", kind="distribution_hub")
    ng_plant = f.SupplyNodeFactory(name="Kano Plant", country="NG", kind="factory")
    et_hub = f.SupplyNodeFactory(name="Gode Hub", country="ET", kind="distribution_hub")
    et_plant = f.SupplyNodeFactory(name="Addis Plant", country="ET", kind="factory")

    ng_contract = f.ContractFactory(
        appropriation=appropriation, reference="OES-C-NG", total_quantity=45000, unit_price=42
    )
    et_contract = f.ContractFactory(
        appropriation=appropriation, reference="OES-C-ET", total_quantity=48000, unit_price=41
    )
    ng_ship = f.ShipmentFactory(
        contract=ng_contract,
        reference="SHP-NG-1",
        origin=ng_plant,
        destination=ng_hub,
        quantity=15000,
        status=Shipment.Status.CONFIRMED,
    )
    et_ship = f.ShipmentFactory(
        contract=et_contract,
        reference="SHP-ET-1",
        origin=et_plant,
        destination=et_hub,
        quantity=12000,
        status=Shipment.Status.IN_TRANSIT,
    )
    return {
        "appropriation": appropriation,
        "ng_contract": ng_contract,
        "et_contract": et_contract,
        "ng_ship": ng_ship,
        "et_ship": et_ship,
    }


# ---------------------------------------------------------------------------
# Government observer
# ---------------------------------------------------------------------------


def test_gov_observer_bootstrap_is_country_scoped(gov_client, network):
    client, _role = gov_client
    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "gov_observer"
    assert body["scope_country"] == "NG"
    assert "contracts" in body and "nodes" in body
    # a government observer never receives procurement surfaces
    assert "review_queue" not in body
    assert "registry" not in body
    assert "rfps" not in body
    assert "appropriations" not in body


def test_gov_observer_is_read_only(gov_client, network):
    client, _role = gov_client
    ship = network["ng_ship"]
    assert client.post(f"/supply/api/shipments/{ship.id}/confirm/").status_code == 403
    assert (
        client.post(
            "/supply/api/shipments/",
            data=json.dumps({}),
            content_type="application/json",
        ).status_code
        == 403
    )
    assert client.get("/supply/api/tokens/").status_code == 403
    assert (
        client.post(
            "/supply/api/eoi/rounds/", data=json.dumps({"title": "x"}), content_type="application/json"
        ).status_code
        == 403
    )


def test_gov_observer_cannot_resolve_discrepancies(gov_client, network):
    client, _role = gov_client
    disc = f.DiscrepancyFactory(shipment=network["ng_ship"])
    assert client.post(f"/supply/api/discrepancies/{disc.id}/resolve/").status_code == 403


# ---------------------------------------------------------------------------
# Funder
# ---------------------------------------------------------------------------


def test_funder_bootstrap_carries_the_money_chain(funder_client, network):
    client, _role = funder_client
    body = client.get("/supply/api/bootstrap/").json()
    assert body["role"] == "funder"
    assert [a["title"] for a in body["appropriations"]] == ["FY2026 Emergency Food Security"]

    contracts = {c["reference"]: c for c in body["contracts"]}
    ng = contracts["OES-C-NG"]
    # the three money stages must arrive as separate numbers
    assert {"obligated_value", "disbursed_value", "delivered_quantity"} <= set(ng)
    assert ng["obligated_value"] == pytest.approx(45000 * 42)
    # confirmed delivery is disbursable; in-transit is not
    assert ng["disbursed_value"] == pytest.approx(15000 * 42)
    assert contracts["OES-C-ET"]["disbursed_value"] == 0
    # and each contract names the envelope funding it, so the Sankey conserves
    assert ng["appropriation_id"] == network["appropriation"].id


def test_funder_is_read_only(funder_client, network):
    client, _role = funder_client
    assert client.post(f"/supply/api/shipments/{network['ng_ship'].id}/confirm/").status_code == 403
    assert client.get("/supply/api/tokens/").status_code == 403


def test_funder_sees_no_procurement_surfaces(funder_client, network):
    client, _role = funder_client
    body = client.get("/supply/api/bootstrap/").json()
    for key in ("review_queue", "registry", "rounds", "rfps", "org"):
        assert not body.get(key), f"funder should not receive {key}"


# ---------------------------------------------------------------------------
# Flow-map payload
# ---------------------------------------------------------------------------


def test_shipment_payload_carries_route_geometry_for_the_map(admin_client, network):
    from django.contrib.gis.geos import LineString

    client, _user = admin_client
    ship = network["ng_ship"]
    ship.route = LineString([(8.5, 12.0), (10.6, 11.75), (13.15, 11.83)], srid=4326)
    ship.save(update_fields=["route"])

    body = client.get(f"/supply/api/shipments/{ship.id}/").json()["shipment"]
    assert body["route"] == [[8.5, 12.0], [10.6, 11.75], [13.15, 11.83]]

    # a shipment with no digitised corridor reports null rather than an empty
    # path the map would try to animate
    other = network["et_ship"]
    assert client.get(f"/supply/api/shipments/{other.id}/").json()["shipment"]["route"] is None


def test_delivered_and_in_transit_are_distinguishable_on_the_wire(admin_client, network):
    client, _user = admin_client
    contracts = {c["reference"]: c for c in client.get("/supply/api/contracts/").json()["contracts"]}
    ng_statuses = {s["status"] for s in contracts["OES-C-NG"]["shipments"]}
    et_statuses = {s["status"] for s in contracts["OES-C-ET"]["shipments"]}
    assert ng_statuses == {"confirmed"}
    assert et_statuses == {"in_transit"}


def test_eta_delta_surfaces_lateness(admin_client, network):
    client, _user = admin_client
    ship = network["et_ship"]
    now = timezone.now()
    f.MilestoneFactory(
        shipment=ship,
        node=ship.destination,
        kind="arrive",
        sequence=1,
        planned_at=now - timedelta(days=3),
        actual_at=now,
    )
    body = client.get(f"/supply/api/shipments/{ship.id}/").json()["shipment"]
    assert body["eta_delta_days"] == pytest.approx(3.0, abs=0.1)


def test_disbursement_never_exceeds_obligation(admin_client):
    """A funder view that shows more paid than committed is not credible.

    Regression guard: the Sudan corridor contract was once priced per
    truck-month while its shipments were counted in cartons, which reported a
    nine-figure disbursement against a six-figure obligation.
    """
    from django.core.management import call_command

    from connect_labs.supply.models import Contract

    call_command("seed_supply_demo", "--reset")
    for contract in Contract.objects.all():
        assert contract.disbursed_value <= contract.obligated_value, (
            f"{contract.reference} disbursed {contract.disbursed_value} against "
            f"obligated {contract.obligated_value}"
        )
        assert (
            contract.delivered_quantity <= contract.total_quantity
        ), f"{contract.reference} delivered more than it contracted for"


def test_a_carton_counts_once_however_many_legs_it_travels(db):
    """A consignment moving in hops must not be counted at every hop.

    Each hop is its own Shipment, so summing them all counted a carton once per
    leg it travelled: OES-C-2026-NG1 read 54,910 shipped against 45,000
    contracted, and the funder page put 115,170 children beside 48,787 courses
    with the same stated method. A contract for "45,000 cartons delivered to
    Maiduguri" is discharged by the cartons that reach Maiduguri.
    """
    contract = f.ContractFactory(total_quantity=45000, unit="cartons", unit_price=42)
    delivery_place = contract.award.lot.delivery_place
    hub = f.SupplyNodeFactory(name=delivery_place)
    plant = f.SupplyNodeFactory(name="Kano RUTF Plant")
    warehouse = f.SupplyNodeFactory(name="Kano Central Warehouse")
    clinic = f.SupplyNodeFactory(name="Bama Health Post")

    # the leg that discharges the contract
    f.ShipmentFactory(
        contract=contract, origin=warehouse, destination=hub, quantity=15000, status=Shipment.Status.CONFIRMED
    )
    # upstream of the delivery place: not delivery yet
    f.ShipmentFactory(
        contract=contract, origin=plant, destination=warehouse, quantity=20000, status=Shipment.Status.CONFIRMED
    )
    # downstream of it: last-mile distribution past the delivery point
    f.ShipmentFactory(
        contract=contract, origin=hub, destination=clinic, quantity=9910, status=Shipment.Status.CONFIRMED
    )

    assert contract.delivered_quantity == 15000
    assert contract.shipped_quantity == 15000
    assert contract.disbursed_value == 15000 * 42
    # the whole point: the sum of every leg would have exceeded the contract
    assert contract.delivered_quantity < 15000 + 20000 + 9910


def test_shipments_in_a_foreign_unit_are_excluded_from_money(db):
    """Only shipments denominated in the contract's unit can move money."""
    contract = f.ContractFactory(total_quantity=100, unit="truck-months", unit_price=1000)
    f.ShipmentFactory(contract=contract, quantity=5, unit="truck-months", status=Shipment.Status.CONFIRMED)
    # a carton-denominated shipment on a truck-month contract must not count
    f.ShipmentFactory(contract=contract, quantity=20000, unit="cartons", status=Shipment.Status.CONFIRMED)

    assert contract.delivered_quantity == 5
    assert contract.disbursed_value == 5000
