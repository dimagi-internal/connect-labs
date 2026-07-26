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
        "obligated_value": float(contract.obligated_value),
        "disbursed_value": float(contract.disbursed_value),
        "shipped_quantity": float(contract.shipped_quantity),
        "delivered_quantity": delivered,
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
