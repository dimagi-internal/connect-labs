"""Tier 2 — despatch advice.

The shipment -> orders -> packages -> items tree of an X12 856 / EDIFACT
DESADV, expressed as JSON. That shape is what any commodity EDI translation
layer produces, and it is also what the supplier portal's despatch form posts,
so an integrated supplier and a hand-keying one travel the same code path.
"""
from django.db import transaction
from django.utils import timezone

from ... import gs1
from ...models import Contract, Milestone, Shipment, ShipmentLine, SupplyEvent
from ..org_actions import ActionError
from ._core import capture_event, node_by_gln, parse_event_time, resolve_node

@transaction.atomic
def capture_despatch_advice(org, payload, source_tier=SupplyEvent.SourceTier.ASN):
    """Materialise a shipment from an ASN-shaped document.

    The tree mirrors X12 856 / EDIFACT DESADV: shipment → orders → packages →
    items, which is what any EDI translation layer emits as JSON. The supplier
    portal posts the SAME shape from a webform with ``source_tier="portal"``,
    so a supplier with no system at all can key in what an API client sends.
    """
    if not isinstance(payload, dict):
        raise ActionError("body must be a despatch advice object")

    asn_ref = (payload.get("asn_reference") or payload.get("despatch_advice_number") or "").strip()
    if not asn_ref:
        raise ActionError("asn_reference is required")

    contract_ref = (payload.get("contract_reference") or "").strip()
    contract = Contract.objects.filter(reference=contract_ref, org=org).first()
    if contract is None:
        raise ActionError("contract_reference does not match a contract for your organisation")

    # node_by_gln, not a raw filter: a blank GLN would otherwise match the
    # first node that happens to have an empty one and bind the consignment to
    # an arbitrary location.
    origin = resolve_node(payload.get("ship_from")) or node_by_gln(payload.get("ship_from_gln"))
    destination = resolve_node(payload.get("ship_to")) or node_by_gln(payload.get("ship_to_gln"))
    if origin is None or destination is None:
        raise ActionError("ship_from and ship_to must resolve to known locations (by GLN)")

    existing = Shipment.objects.filter(asn_reference=asn_ref, contract__org=org).first()
    if existing:
        return existing, False

    packages = payload.get("packages") or []
    lines_data = []
    total = 0.0
    for package in packages:
        sscc = str(package.get("sscc") or "")
        for item in package.get("items") or []:
            qty = float(item.get("quantity") or 0)
            total += qty
            lines_data.append(
                {
                    "gtin": str(item.get("gtin") or ""),
                    "batch_lot": str(item.get("batch_lot") or item.get("lot") or ""),
                    "expiry_date": item.get("expiry_date") or None,
                    "quantity": qty,
                    "unit": item.get("unit") or "cartons",
                    "sscc": sscc,
                }
            )
    if not lines_data:
        raise ActionError("despatch advice contains no items")

    reference = payload.get("shipment_reference") or f"SHP-{asn_ref}"
    shipment = Shipment.objects.create(
        contract=contract,
        reference=reference,
        asn_reference=asn_ref,
        origin=origin,
        destination=destination,
        waypoints=payload.get("waypoint_gLNs") or [],
        quantity=total,
        unit=lines_data[0]["unit"],
        eta=parse_event_time(payload.get("eta")) if payload.get("eta") else None,
    )
    ShipmentLine.objects.bulk_create([ShipmentLine(shipment=shipment, **row) for row in lines_data])

    # An ASN also creates the planned legs, so ETA-vs-plan is renderable from
    # the moment of despatch.
    Milestone.objects.create(
        shipment=shipment,
        node=origin,
        kind=Milestone.Kind.DEPART,
        sequence=0,
        planned_at=parse_event_time(payload.get("departed_at")) if payload.get("departed_at") else None,
    )
    Milestone.objects.create(
        shipment=shipment,
        node=destination,
        kind=Milestone.Kind.ARRIVE,
        sequence=1,
        planned_at=shipment.eta,
        estimated_at=shipment.eta,
    )

    capture_event(
        org,
        biz_step=SupplyEvent.BizStep.DEPARTING,
        event_time=parse_event_time(payload.get("departed_at")) if payload.get("departed_at") else timezone.now(),
        read_point=origin,
        epc_list=[gs1.digital_link("00", p["sscc"]) for p in lines_data if p["sscc"]],
        quantity_list=[
            {"gtin": row["gtin"], "batch_lot": row["batch_lot"], "quantity": row["quantity"], "uom": "CT"}
            for row in lines_data
        ],
        biz_transactions={"desadv": asn_ref, "po": payload.get("po_reference", "")},
        disposition="in_transit",
        source_tier=source_tier,
        external_id=f"asn:{asn_ref}",
        raw=payload,
        shipment=shipment,
    )
    return shipment, True
