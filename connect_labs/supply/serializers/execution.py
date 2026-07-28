"""Wire shapes for the execution domain.

Two things here are load-bearing beyond simple field mapping: ``shipment_dict``
emits the route as a coordinate list for the flow map to animate, and
``contract_dict`` reports obligated, disbursed and delivered as three separate
figures so no consumer can accidentally collapse them into one.
"""
from .. import gs1
from .procurement import lot_dict  # noqa: F401  (execution rows reference lots)


def node_dict(node):
    return {
        "id": node.id,
        "name": node.name,
        "kind": node.kind,
        "country": node.country,
        "adm1_code": node.adm1_code,
        "gln": node.gln,
        "owner_id": node.owner_id,
        "lon": node.location.x if node.location else None,
        "lat": node.location.y if node.location else None,
    }


def milestone_dict(milestone):
    return {
        "id": milestone.id,
        "node_id": milestone.node_id,
        "node_name": milestone.node.name,
        "kind": milestone.kind,
        "sequence": milestone.sequence,
        "planned_at": milestone.planned_at.isoformat() if milestone.planned_at else None,
        "estimated_at": milestone.estimated_at.isoformat() if milestone.estimated_at else None,
        "actual_at": milestone.actual_at.isoformat() if milestone.actual_at else None,
        "delta_days": milestone.delta_days,
    }


def shipment_line_dict(line):
    return {
        "id": line.id,
        "gtin": line.gtin,
        "batch_lot": line.batch_lot,
        "expiry_date": line.expiry_date.isoformat() if line.expiry_date else None,
        "quantity": float(line.quantity),
        "unit": line.unit,
        "sscc": line.sscc,
    }


# Ordered weakest-to-strongest. A consignment reported at more than one tier is
# only as good as its weakest link, so that is what it is labelled with.
_TIER_ORDER = ("portal", "checkin", "asn", "epcis")


def _dominant_tier(shipment):
    """The weakest tier any of this consignment's events arrived on."""
    tiers = {e.source_tier for e in shipment.events.all() if e.source_tier}
    if not tiers:
        return "asn" if shipment.asn_reference else "portal"
    for tier in _TIER_ORDER:
        if tier in tiers:
            return tier
    return sorted(tiers)[0]


def shipment_dict(shipment, include_detail=False):
    quantity = float(shipment.quantity)
    data = {
        "id": shipment.id,
        "reference": shipment.reference,
        "asn_reference": shipment.asn_reference,
        "contract_id": shipment.contract_id,
        "contract_reference": shipment.contract.reference,
        "origin": {"id": shipment.origin_id, "name": shipment.origin.name},
        "destination": {"id": shipment.destination_id, "name": shipment.destination.name},
        "quantity": quantity,
        "unit": shipment.unit,
        "metric_tonnes": gs1.cartons_to_mt(quantity) if shipment.unit == "cartons" else None,
        "status": shipment.status,
        "departed_at": shipment.departed_at.isoformat() if shipment.departed_at else None,
        "eta": shipment.eta.isoformat() if shipment.eta else None,
        "delivered_at": shipment.delivered_at.isoformat() if shipment.delivered_at else None,
        "open_discrepancies": shipment.discrepancies.filter(status="open").count(),
        # HOW this consignment is known, not just what is known about it. The
        # tier is the honest part of the picture — a Kano plant posts EPCIS, a
        # despatch posts an advice, and the Port Sudan corridor is a driver on
        # the phone — and it was only ever visible on the supplier's own pages,
        # so the surface that argues the picture is brightest where access is
        # easiest could not show it.
        "source_tier": _dominant_tier(shipment),
        # [[lon, lat], ...] along the digitised corridor — what the flow map animates.
        "route": [list(pt) for pt in shipment.route.coords] if shipment.route else None,
    }
    milestones = list(shipment.milestones.select_related("node").all())
    data["milestones"] = [milestone_dict(m) for m in milestones]
    deltas = [m.delta_days for m in milestones if m.delta_days is not None]
    data["eta_delta_days"] = deltas[-1] if deltas else None
    if include_detail:
        data["lines"] = [shipment_line_dict(line) for line in shipment.lines.all()]
        data["events"] = [event_dict(e) for e in shipment.events.select_related("read_point").all()]
    return data


def event_dict(event):
    return {
        "id": event.id,
        "shipment_id": event.shipment_id,
        "event_type": event.event_type,
        "biz_step": event.biz_step,
        "disposition": event.disposition,
        "event_time": event.event_time.isoformat(),
        "recorded_at": event.recorded_at.isoformat() if event.recorded_at else None,
        "read_point": event.read_point.name if event.read_point else None,
        "read_point_id": event.read_point_id,
        "epc_list": event.epc_list,
        "quantity_list": event.quantity_list,
        "biz_transactions": event.biz_transactions,
        "source_tier": event.source_tier,
        "external_id": event.external_id,
    }


def discrepancy_dict(disc):
    return {
        "id": disc.id,
        "shipment_id": disc.shipment_id,
        "shipment_reference": disc.shipment.reference,
        "expected_quantity": float(disc.expected_quantity),
        "received_quantity": float(disc.received_quantity),
        "shortfall": float(disc.shortfall),
        "note": disc.note,
        "status": disc.status,
        "created_at": disc.created_at.isoformat() if disc.created_at else None,
    }


def contract_dict(contract, include_shipments=False):
    delivered = float(contract.delivered_quantity)
    data = {
        "id": contract.id,
        "reference": contract.reference,
        "org_id": contract.org_id,
        "org_name": contract.org.legal_name,
        "lot_description": contract.award.lot.description,
        # Which award this came from, by name. A contract detail that shows a
        # quantity but not its source reads as a contradiction the moment the
        # viewer has just watched a DIFFERENT lot on the same corridor be
        # awarded — they compare the two numbers and conclude the app lost one.
        "source_solicitation": contract.award.lot.rfp.title,
        "awarded_at": (contract.award.awarded_at.isoformat() if contract.award.awarded_at else None),
        "destination": contract.award.lot.delivery_place,
        "destination_country": contract.award.lot.delivery_country,
        "category": contract.award.lot.category,
        "total_quantity": float(contract.total_quantity),
        "unit": contract.unit,
        "unit_price": float(contract.unit_price),
        "currency": contract.currency,
        "status": contract.status,
        "iati_activity_id": contract.iati_activity_id,
        "appropriation_id": contract.appropriation_id,
        # The funding envelope this contract draws against, nested rather than
        # left as a bare id. "Which money is this?" is the first question asked
        # of any award, and answering it should not require a second request or
        # a payload only the funder role receives.
        "appropriation": (
            {
                "funder_name": contract.appropriation.funder_name,
                "title": contract.appropriation.title,
                "fiscal_year": contract.appropriation.fiscal_year,
                "iati_activity_id": contract.appropriation.iati_activity_id,
            }
            if contract.appropriation_id
            else None
        ),
        "obligated_value": float(contract.obligated_value),
        "disbursed_value": float(contract.disbursed_value),
        "shipped_quantity": float(contract.shipped_quantity),
        "delivered_quantity": delivered,
        # Confirmed at the delivery place, so the figure the disbursement was
        # paid against. Cost per child divides money by cartons, and taking the
        # numerator from confirmed arrivals and the denominator from every
        # arrival is how that ratio comes out below the price of a carton.
        "confirmed_quantity": float(contract.confirmed_quantity),
        # A haulage contract buys movement, not food; the unit ladder sums only
        # the contracts that actually bought cartons.
        "buys_goods": contract.buys_goods,
        "delivered_metric_tonnes": gs1.cartons_to_mt(delivered) if contract.unit == "cartons" else None,
        "children_treated": gs1.cartons_to_children(delivered) if contract.unit == "cartons" else None,
    }
    if include_shipments:
        data["shipments"] = [shipment_dict(s) for s in contract.shipments.all()]
    return data


def appropriation_dict(appropriation):
    return {
        "id": appropriation.id,
        "funder_name": appropriation.funder_name,
        "title": appropriation.title,
        "fiscal_year": appropriation.fiscal_year,
        "amount": float(appropriation.amount),
        "currency": appropriation.currency,
        "iati_activity_id": appropriation.iati_activity_id,
    }


def api_token_dict(token):
    return {
        "id": token.id,
        "label": token.label,
        "prefix": token.prefix,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked": token.revoked,
    }
