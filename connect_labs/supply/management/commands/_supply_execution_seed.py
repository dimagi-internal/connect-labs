"""Execution half of the OES demo world: nodes, contracts, shipments, events.

Kept beside the procurement seed rather than inside it so each half stays
readable. Called by ``seed_supply_demo``.

The event history is deliberately spread across ingestion tiers, mirroring the
real capability gradient: the Kano plant emits EPCIS, despatches arrive as
despatch advices, and the Port Sudan corridor arrives as sparse check-ins.
"""
from datetime import timedelta

from django.contrib.gis.geos import LineString, Point
from django.utils import timezone

from connect_labs.supply import gs1, routes
from connect_labs.supply.models import (
    Appropriation,
    Award,
    Contract,
    Discrepancy,
    Milestone,
    Shipment,
    ShipmentLine,
    SupplierOrg,
    SupplyEvent,
    SupplyNode,
)

# name, kind, country, lon, lat, owning org (None = OES network)
NODES = [
    # factories — mirrors the real RUTF producer geography
    ("Kano RUTF Plant", "factory", "NG", 8.5920, 12.0022, "Savanna Nutrients Ltd"),
    ("Lagos Therapeutic Foods Plant", "factory", "NG", 3.3792, 6.5244, "Lagos NutriWorks Ltd"),
    ("Ouagadougou RUTF Plant", "factory", "BF", -1.5197, 12.3714, "Faso NutriWorks SA"),
    ("Addis Ababa RUTF Plant", "factory", "ET", 38.7578, 8.9806, "Rift Valley Therapeutics PLC"),
    # ports and corridor gateways — Ethiopia and Burkina Faso are landlocked,
    # so their corridors run through Djibouti and Lomé respectively
    ("Port of Lagos (Apapa)", "port", "NG", 3.3600, 6.4400, None),
    ("Port Sudan", "port", "SD", 37.2164, 19.6158, None),
    ("Port of Djibouti", "port", "DJ", 43.1450, 11.5890, None),
    ("Port of Lomé", "port", "TG", 1.2833, 6.1319, None),
    # national and regional warehouses
    ("Kano Central Warehouse", "warehouse", "NG", 8.5167, 12.0000, None),
    ("Khartoum Central Warehouse", "warehouse", "SD", 32.5599, 15.5007, None),
    ("Addis Central Depot", "warehouse", "ET", 38.7400, 9.0100, "Addis Central Depot PLC"),
    ("Ouagadougou Central Warehouse", "warehouse", "BF", -1.5330, 12.3600, None),
    ("Kassala Forward Store", "warehouse", "SD", 36.4000, 15.4500, "Kassala Warehousing Co"),
    ("Dire Dawa Transit Store", "warehouse", "ET", 41.8661, 9.5931, None),
    # distribution hubs in the famine-affected zones
    ("Maiduguri Distribution Hub", "distribution_hub", "NG", 13.1510, 11.8311, None),
    ("Damaturu Distribution Hub", "distribution_hub", "NG", 11.9660, 11.7480, None),
    ("El Fasher Distribution Hub", "distribution_hub", "SD", 25.3494, 13.6279, None),
    ("Nyala Distribution Hub", "distribution_hub", "SD", 24.8917, 12.0489, None),
    ("Gode Distribution Hub", "distribution_hub", "ET", 43.5500, 5.9527, None),
    ("Jijiga Distribution Hub", "distribution_hub", "ET", 42.7947, 9.3500, None),
    ("Djibo Distribution Hub", "distribution_hub", "BF", -1.6300, 14.0995, None),
    ("Dori Distribution Hub", "distribution_hub", "BF", -0.0345, 14.0354, None),
    # last-mile delivery points
    ("Bama Health Post", "delivery_point", "NG", 13.6890, 11.5210, None),
    ("Monguno Health Post", "delivery_point", "NG", 13.6100, 12.6750, None),
    ("Tawila Nutrition Site", "delivery_point", "SD", 25.0000, 13.8300, None),
    ("Kebkabiya Nutrition Site", "delivery_point", "SD", 24.0700, 13.6500, None),
    ("Kelafo Nutrition Site", "delivery_point", "ET", 44.3600, 5.6500, None),
    ("Sebba Nutrition Site", "delivery_point", "BF", 0.5150, 13.4370, None),
]

APPROPRIATIONS = [
    (
        "US Government",
        "FY2026 Emergency Food Security — Horn of Africa & Sahel",
        "FY2026",
        48_000_000,
        "US-GOV-1-OES-FY2026-001",
    ),
    (
        "US Government",
        "FY2026 Famine Prevention Reserve",
        "FY2026",
        22_500_000,
        "US-GOV-1-OES-FY2026-002",
    ),
]

# contract_ref, org, lot description to match, quantity, unit price
# One per corridor, so a shipment always belongs to a contract for its own
# country and supplier.
CONTRACTS = [
    ("OES-C-2026-ET1", "Rift Valley Therapeutics PLC", "48,000 cartons RUTF delivered to Gode", 48000, 41.80),
    ("OES-C-2026-NG1", "Savanna Nutrients Ltd", "45,000 cartons RUTF delivered to Maiduguri", 45000, 42.10),
    ("OES-C-2026-BF1", "Faso NutriWorks SA", "20,000 cartons RUTF delivered to Djibo", 20000, 43.60),
    ("OES-C-2026-SD1", "Blue Nile Freight Co", "Port Sudan inland corridor haulage, 6 months", 6, 41500.00),
]

# Which contract each shipment belongs to, by reference prefix.
CONTRACT_BY_PREFIX = {
    "SHP-2026-01": "OES-C-2026-ET1",
    "SHP-2026-02": "OES-C-2026-SD1",
    "SHP-2026-03": "OES-C-2026-NG1",
    "SHP-2026-04": "OES-C-2026-BF1",
}

# reference, contract, origin, destination, waypoints, cartons, state, tier, days ago departed
SHIPMENTS = [
    # Ethiopia corridor: clean EPCIS from the plant, delivered and confirmed
    ("SHP-2026-0101", "Addis Ababa RUTF Plant", "Addis Central Depot", [], 16000, "confirmed", "epcis", 34),
    (
        "SHP-2026-0102",
        "Addis Central Depot",
        "Gode Distribution Hub",
        ["Dire Dawa Transit Store"],
        12000,
        "confirmed",
        "epcis",
        26,
    ),
    ("SHP-2026-0103", "Addis Central Depot", "Jijiga Distribution Hub", [], 8000, "delivered", "epcis", 12),
    ("SHP-2026-0104", "Addis Ababa RUTF Plant", "Addis Central Depot", [], 12000, "in_transit", "asn", 3),
    # Sudan corridor: imported through Port Sudan, tracked by check-ins only
    ("SHP-2026-0201", "Port Sudan", "Khartoum Central Warehouse", [], 14000, "confirmed", "checkin", 30),
    (
        "SHP-2026-0202",
        "Khartoum Central Warehouse",
        "El Fasher Distribution Hub",
        ["Kassala Forward Store"],
        9000,
        "in_transit",
        "checkin",
        9,
    ),
    (
        "SHP-2026-0203",
        "Port Sudan",
        "Nyala Distribution Hub",
        ["Khartoum Central Warehouse"],
        11000,
        "in_transit",
        "checkin",
        5,
    ),
    # Nigeria: a short, well-instrumented corridor
    ("SHP-2026-0301", "Kano RUTF Plant", "Kano Central Warehouse", [], 20000, "confirmed", "epcis", 21),
    ("SHP-2026-0302", "Kano Central Warehouse", "Maiduguri Distribution Hub", [], 15000, "delivered", "asn", 8),
    ("SHP-2026-0303", "Kano Central Warehouse", "Damaturu Distribution Hub", [], 10000, "in_transit", "asn", 2),
    ("SHP-2026-0304", "Maiduguri Distribution Hub", "Bama Health Post", [], 3000, "planned", "portal", None),
    # Burkina Faso: plant to the Sahel, one hand-keyed leg
    ("SHP-2026-0401", "Ouagadougou RUTF Plant", "Ouagadougou Central Warehouse", [], 9000, "confirmed", "asn", 24),
    ("SHP-2026-0402", "Ouagadougou Central Warehouse", "Djibo Distribution Hub", [], 6000, "delivered", "portal", 11),
    ("SHP-2026-0403", "Ouagadougou Central Warehouse", "Dori Distribution Hub", [], 5000, "in_transit", "portal", 4),
]

STATUS_STEPS = {
    "planned": [],
    "in_transit": ["departing"],
    "delivered": ["departing", "arriving", "receiving"],
    "confirmed": ["departing", "arriving", "receiving"],
}


def seed_execution(rng, orgs, staff):
    nodes = _seed_nodes(orgs)
    appropriations = _seed_appropriations()
    contracts = _seed_contracts(orgs, appropriations)
    if not contracts:
        return nodes, contracts
    _seed_shipments(rng, nodes, contracts)
    return nodes, contracts


def _seed_nodes(orgs):
    nodes = {}
    for index, (name, kind, country, lon, lat, owner_name) in enumerate(NODES):
        owner = orgs.get(owner_name) if owner_name else None
        node, _ = SupplyNode.objects.update_or_create(
            name=name,
            defaults={
                "kind": kind,
                "country": country,
                "gln": gs1.make_gln("629123", 100 + index),
                "location": Point(lon, lat, srid=4326),
                "owner": owner,
            },
        )
        nodes[name] = node
    return nodes


def _seed_appropriations():
    out = []
    for funder, title, fy, amount, iati in APPROPRIATIONS:
        appropriation, _ = Appropriation.objects.update_or_create(
            title=title,
            defaults={
                "funder_name": funder,
                "fiscal_year": fy,
                "amount": amount,
                "currency": "USD",
                "iati_activity_id": iati,
            },
        )
        out.append(appropriation)
    return out


def _seed_contracts(orgs, appropriations):
    """Contracts hang off real awards, so the procurement→execution chain is intact."""
    contracts = {}
    for index, (reference, org_name, lot_description, quantity, unit_price) in enumerate(CONTRACTS):
        award = Award.objects.filter(lot__description=lot_description).select_related("lot").first()
        if award is None:
            continue
        contract, _ = Contract.objects.update_or_create(
            award=award,
            defaults={
                # spread across both funding envelopes so the funder view has
                # more than one appropriation to attribute delivery against
                "appropriation": appropriations[index % len(appropriations)] if appropriations else None,
                "org": orgs[org_name],
                "reference": reference,
                "total_quantity": quantity,
                "unit": "cartons",
                "unit_price": unit_price,
                "currency": "USD",
                "starts_on": (timezone.now() - timedelta(days=60)).date(),
                "ends_on": (timezone.now() + timedelta(days=120)).date(),
                "iati_activity_id": f"US-GOV-1-OES-{reference}",
            },
        )
        contracts[reference] = contract
    return contracts


def _route(origin, destination, waypoint_nodes):
    """A LineString following the digitised corridor for each hop."""
    coords = routes.build_route(origin, destination, waypoint_nodes)
    if not coords:
        return None
    return LineString(coords, srid=4326)


def _seed_shipments(rng, nodes, contracts):
    now = timezone.now()

    for index, (
        reference,
        origin_name,
        destination_name,
        waypoint_names,
        cartons,
        state,
        tier,
        days_ago,
    ) in enumerate(SHIPMENTS):
        contract = contracts.get(CONTRACT_BY_PREFIX[reference[:11]])
        if contract is None:
            continue
        org = contract.org
        origin = nodes[origin_name]
        destination = nodes[destination_name]
        waypoint_nodes = [nodes[name] for name in waypoint_names]

        departed = now - timedelta(days=days_ago) if days_ago is not None else None
        transit_days = rng.randint(3, 9)
        planned_arrival = (departed + timedelta(days=transit_days)) if departed else now + timedelta(days=7)
        # a few legs run late, which is what populates the exception queue
        slip_days = rng.choice([0, 0, 0, 1, 2, 4])
        actual_arrival = planned_arrival + timedelta(days=slip_days)

        # Descriptive fields are refreshed on every run; LIFECYCLE state is not.
        # Events are idempotent, so re-running would not replay them — resetting
        # status here would strand every shipment in "planned".
        shipment, _created = Shipment.objects.update_or_create(
            reference=reference,
            defaults={
                "contract": contract,
                "asn_reference": f"ASN-{reference[-8:]}" if tier in ("asn", "portal") else "",
                "origin": origin,
                "destination": destination,
                "waypoints": [n.id for n in waypoint_nodes],
                "route": _route(origin, destination, waypoint_nodes),
                "quantity": cartons,
                "unit": "cartons",
                "eta": planned_arrival,
            },
        )

        if not shipment.lines.exists():
            ShipmentLine.objects.create(
                shipment=shipment,
                gtin=gs1.make_gtin("629123", 7346),
                batch_lot=f"LOT26{index:02d}A",
                expiry_date=(now + timedelta(days=540)).date(),
                quantity=cartons,
                unit="cartons",
                sscc=gs1.make_sscc("629123", 1000 + index),
            )

        _seed_legs(shipment, origin, destination, waypoint_nodes, departed, planned_arrival)
        _seed_events(org, shipment, origin, destination, state, tier, departed, actual_arrival, cartons, index, rng)


def _seed_legs(shipment, origin, destination, waypoint_nodes, departed, planned_arrival):
    if shipment.milestones.exists():
        return
    legs = [(origin, Milestone.Kind.DEPART, departed)]
    hops = waypoint_nodes + [destination]
    span = (planned_arrival - departed) if departed else timedelta(days=7)
    for i, node in enumerate(hops, start=1):
        legs.append((node, Milestone.Kind.ARRIVE, (departed + (span * i / len(hops))) if departed else None))
    for sequence, (node, kind, planned) in enumerate(legs):
        Milestone.objects.create(
            shipment=shipment,
            node=node,
            kind=kind,
            sequence=sequence,
            planned_at=planned,
            estimated_at=planned,
        )


def _seed_events(org, shipment, origin, destination, state, tier, departed, actual_arrival, cartons, index, rng):
    """Replay the shipment's history through the real capture path.

    Using the ingestion service rather than writing rows directly means the
    seeded world is produced by exactly the code an API client exercises.
    """
    from connect_labs.supply.services import ingestion

    steps = STATUS_STEPS[state]
    if not steps or departed is None:
        return

    tier_value = {
        "epcis": SupplyEvent.SourceTier.EPCIS,
        "asn": SupplyEvent.SourceTier.ASN,
        "checkin": SupplyEvent.SourceTier.CHECKIN,
        "portal": SupplyEvent.SourceTier.PORTAL,
    }[tier]

    line = shipment.lines.first()
    quantities = [{"gtin": line.gtin, "batch_lot": line.batch_lot, "quantity": float(cartons), "uom": "CT"}]

    # One leg's receipt comes up short, which seeds the discrepancy feed.
    short = index == 8
    for step in steps:
        if step == "departing":
            when, where, qty = departed, origin, quantities
        elif step == "arriving":
            when, where, qty = actual_arrival, destination, quantities
        else:
            received = cartons - 240 if short else cartons
            when, where = actual_arrival + timedelta(hours=6), destination
            qty = [{**quantities[0], "quantity": float(received)}]

        ingestion.capture_event(
            org,
            biz_step=step,
            event_time=when,
            read_point=where,
            epc_list=[gs1.digital_link("00", line.sscc)] if tier_value != SupplyEvent.SourceTier.CHECKIN else [],
            quantity_list=qty if tier_value != SupplyEvent.SourceTier.CHECKIN else [],
            biz_transactions={"shipment": shipment.reference, "desadv": shipment.asn_reference or ""},
            disposition="in_transit" if step == "departing" else "in_progress",
            source_tier=tier_value,
            external_id=f"seed:{shipment.reference}:{step}",
            shipment=shipment,
        )

    if state == "confirmed":
        shipment.refresh_from_db()
        if shipment.status == Shipment.Status.DELIVERED:
            shipment.status = Shipment.Status.CONFIRMED
            shipment.save(update_fields=["status"])


def execution_summary():
    return (
        f"{SupplyNode.objects.count()} nodes, {Contract.objects.count()} contracts, "
        f"{Shipment.objects.count()} shipments, {SupplyEvent.objects.count()} events, "
        f"{Discrepancy.objects.filter(status='open').count()} open discrepancies"
    )


def reset_execution():
    Discrepancy.objects.all().delete()
    SupplyEvent.objects.all().delete()
    Milestone.objects.all().delete()
    ShipmentLine.objects.all().delete()
    Shipment.objects.all().delete()
    Contract.objects.all().delete()
    Appropriation.objects.all().delete()
    SupplyNode.objects.all().delete()
    SupplierOrg  # imported for symmetry with the procurement reset
