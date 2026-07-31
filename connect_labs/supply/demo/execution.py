"""Seeds the execution half: nodes, contracts, shipments and their events.

Kept beside the procurement seed rather than inside it so each half stays
readable. Called by ``seed_supply_demo``.

The event history is deliberately spread across ingestion tiers, mirroring the
real capability gradient: the Kano plant emits EPCIS, despatches arrive as
despatch advices, and the Port Sudan corridor arrives as sparse check-ins.
"""
from datetime import datetime, time, timedelta

from django.contrib.gis.geos import LineString, Point
from django.utils import timezone

from .. import gs1, routes
from ..models import (
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
from .data import (
    APPROPRIATIONS,
    CONTRACT_BY_PREFIX,
    CONTRACTS,
    NODE_DISTRICTS,
    NODES,
    SHIPMENT_SLIP_DAYS,
    SHIPMENTS,
    SHORT_DATED_LOTS,
    STATUS_STEPS,
)


def seed_execution(rng, orgs, staff):
    nodes = _seed_nodes(orgs)
    appropriations = _seed_appropriations()
    contracts = _seed_contracts(orgs, appropriations)
    if not contracts:
        return nodes, contracts
    _seed_shipments(rng, nodes, contracts)
    return nodes, contracts


def backdate_awards_to_precede_execution():
    """An award cannot be dated after the deliveries it authorised.

    Called from the seeder's top level AFTER both halves have run, not from
    ``seed_execution``: ``seed_demand`` adds each site an earlier, already-despatched
    consignment, so running this at the end of execution reconciled against only
    two thirds of the shipments and left twenty rows still predating their award.

    ``Award.awarded_at`` is ``auto_now_add``, so every award is stamped with the
    moment the seeder ran — while shipment ETAs are authored in the past, because
    the demo world has to open mid-flight with deliveries already made. The result
    was thirty shipments dated before the contract that paid for them: SHP-2026-0805
    showed an ETA of 23 April against a contract awarded 31 July, and a *delivered*
    consignment sat under a contract awarded eleven days later.

    In a demo whose subject is auditable procurement that is the most damaging
    possible detail, and it regenerated itself on every reseed.

    Runs after shipments exist, so it can reconcile against real dates rather than
    a guess: each award moves to a day before the earliest thing its contract
    already did. ``auto_now_add`` ignores assignment, hence ``update()``.
    """
    for award in Award.objects.select_related("contract").all():
        contract = getattr(award, "contract", None)
        if contract is None:
            continue
        moments = []
        for shipment in contract.shipments.all():
            for value in (shipment.departed_at, shipment.eta):
                if value:
                    moments.append(timezone.localtime(value).date())
        if contract.starts_on:
            moments.append(contract.starts_on)
        if not moments:
            continue
        earliest = min(moments)
        # A day clear of the first movement: an award signed the same morning a
        # lorry left reads as backdated paperwork, which is the impression this
        # is here to avoid.
        awarded_on = earliest - timedelta(days=1)
        stamp = timezone.make_aware(datetime.combine(awarded_on, time(9, 0)))
        Award.objects.filter(pk=award.pk).update(awarded_at=stamp)
        if contract.starts_on and contract.starts_on < awarded_on:
            Contract.objects.filter(pk=contract.pk).update(starts_on=awarded_on)


def _seed_nodes(orgs):
    nodes = {}
    for index, (name, kind, country, lon, lat, owner_name) in enumerate(NODES):
        owner = orgs.get(owner_name) if owner_name else None
        node, _ = SupplyNode.objects.update_or_create(
            name=name,
            defaults={
                "kind": kind,
                "country": country,
                "adm1_code": NODE_DISTRICTS.get(name, ""),
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
                # The reference already begins "OES-" — see the note in
                # rfp_actions._contract_from. Prepending it again duplicated
                # the segment.
                "iati_activity_id": f"US-GOV-1-{reference}",
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
        # a few legs run late, which is what populates the exception queue —
        # authored per leg (see SHIPMENT_SLIP_DAYS) so the queue ranks on a
        # story rather than on whatever two draws happened to collide
        slip_days = SHIPMENT_SLIP_DAYS.get(reference, 0)
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
                expiry_date=(now + timedelta(days=SHORT_DATED_LOTS.get(reference, 540))).date(),
                quantity=cartons,
                unit="cartons",
                sscc=gs1.make_sscc("629123", 1000 + index),
            )

        _seed_legs(shipment, origin, destination, waypoint_nodes, departed, planned_arrival, slip_days=slip_days)
        _seed_events(org, shipment, origin, destination, state, tier, departed, actual_arrival, cartons, index, rng)


def _seed_legs(shipment, origin, destination, waypoint_nodes, departed, planned_arrival, slip_days=0):
    """Plan, estimate and actual per leg — with the ESTIMATE actually moving.

    Every milestone was seeded with ``estimated_at == planned_at``, so a
    consignment already running late but not yet arrived reported a delay of
    zero and never entered the exception queue at all. Only legs that had
    already been delivered — the ones with an ``actual_at`` — could ever be
    late, which is exactly backwards: a delay you can still do something about
    is the one worth surfacing.

    It also left the product's own claim undemonstrated. Keeping planned,
    estimated and actual as three separate timestamps only means anything if
    the middle one moves when a leg slips, and nothing in the demo world ever
    moved it.
    """
    if shipment.milestones.exists():
        return
    legs = [(origin, Milestone.Kind.DEPART, departed)]
    hops = waypoint_nodes + [destination]
    span = (planned_arrival - departed) if departed else timedelta(days=7)
    for i, node in enumerate(hops, start=1):
        legs.append((node, Milestone.Kind.ARRIVE, (departed + (span * i / len(hops))) if departed else None))
    last_index = len(legs) - 1
    for sequence, (node, kind, planned) in enumerate(legs):
        # The slip lands on the arrival the consignment is still short of. An
        # onward leg's estimate moving is what "nine days behind plan" means
        # while the truck is still on the road.
        estimated = planned
        if slip_days and planned is not None and sequence == last_index:
            estimated = planned + timedelta(days=slip_days)
        Milestone.objects.create(
            shipment=shipment,
            node=node,
            kind=kind,
            sequence=sequence,
            planned_at=planned,
            estimated_at=estimated,
        )


def _seed_events(org, shipment, origin, destination, state, tier, departed, actual_arrival, cartons, index, rng):
    """Replay the shipment's history through the real capture path.

    Using the ingestion service rather than writing rows directly means the
    seeded world is produced by exactly the code an API client exercises.
    """
    from ..services import ingestion

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
