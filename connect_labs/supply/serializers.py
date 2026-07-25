"""Entity → dict shapes consumed by the SPA.

These are the wire contract: the React tabs read exactly these keys, and
``EOISubmission.profile_snapshot`` stores ``org_dict(org, include_qualifications=False)``
verbatim at submit time.
"""


def certification_dict(cert):
    return {
        "id": cert.id,
        "cert_type": cert.cert_type,
        "issuer": cert.issuer,
        "expiry_date": cert.expiry_date.isoformat() if cert.expiry_date else None,
        "document_name": cert.document_name,
    }


def qualification_dict(qual):
    return {
        "id": qual.id,
        "category": qual.category,
        "granted_at": qual.granted_at.isoformat(),
        "expires_at": qual.expires_at.isoformat(),
        "status": qual.status,
    }


def org_dict(org, include_qualifications=True):
    data = {
        "id": org.id,
        "legal_name": org.legal_name,
        "registration_number": org.registration_number,
        "country": org.country,
        "hq_city": org.hq_city,
        "description": org.description,
        "contact_name": org.contact_name,
        "contact_email": org.contact_email,
        "gln": org.gln,
        "gs1_company_prefix": org.gs1_company_prefix,
        "certifications": [certification_dict(c) for c in org.certifications.all().order_by("cert_type")],
    }
    if include_qualifications:
        data["qualifications"] = [qualification_dict(q) for q in org.qualifications.all().order_by("category")]
    return data


def round_dict(rnd):
    return {
        "id": rnd.id,
        "title": rnd.title,
        "brief": rnd.brief,
        "categories": rnd.categories,
        "opens_at": rnd.opens_at.isoformat() if rnd.opens_at else None,
        "closes_at": rnd.closes_at.isoformat() if rnd.closes_at else None,
        "status": rnd.status,
        "submission_count": rnd.submissions.count(),
    }


def submission_dict(sub):
    return {
        "id": sub.id,
        "round_id": sub.round_id,
        "round_title": sub.round.title,
        "org_id": sub.org_id,
        "org_name": sub.org.legal_name,
        "org_country": sub.org.country,
        "categories": sub.categories,
        "commitments": sub.commitments,
        "status": sub.status,
        "submitted_at": sub.submitted_at.isoformat() if sub.submitted_at else None,
        "profile_snapshot": sub.profile_snapshot,
    }


def lot_dict(lot):
    award = getattr(lot, "award", None)
    return {
        "id": lot.id,
        "rfp_id": lot.rfp_id,
        "category": lot.category,
        "description": lot.description,
        "quantity": float(lot.quantity),
        "unit": lot.unit,
        "delivery_country": lot.delivery_country,
        "delivery_place": lot.delivery_place,
        "delivery_deadline": lot.delivery_deadline.isoformat() if lot.delivery_deadline else None,
        "awarded_org": award.lot_bid.bid.org.legal_name if award else None,
        "awarded_lot_bid_id": award.lot_bid_id if award else None,
    }


def rfp_dict(rfp, include_lots=True):
    data = {
        "id": rfp.id,
        "title": rfp.title,
        "brief": rfp.brief,
        "categories": rfp.categories,
        "countries": rfp.countries,
        "bid_deadline": rfp.bid_deadline.isoformat() if rfp.bid_deadline else None,
        "status": rfp.status,
    }
    if include_lots:
        data["lots"] = [lot_dict(lot) for lot in rfp.lots.all().order_by("id")]
    return data


def lot_bid_dict(lot_bid, include_scores=False):
    data = {
        "id": lot_bid.id,
        "bid_id": lot_bid.bid_id,
        "lot_id": lot_bid.lot_id,
        "org_name": lot_bid.bid.org.legal_name,
        "unit_price": float(lot_bid.unit_price),
        "currency": lot_bid.currency,
        "lead_time_days": lot_bid.lead_time_days,
        "notes": lot_bid.notes,
    }
    if include_scores:
        scores = list(lot_bid.scores.all())
        data["scores"] = [
            {
                "reviewer": s.reviewer.get_display_name() if s.reviewer else None,
                "technical_score": s.technical_score,
                "notes": s.notes,
            }
            for s in scores
        ]
        data["avg_technical_score"] = (
            round(sum(s.technical_score for s in scores) / len(scores), 1) if scores else None
        )
    return data


def bid_dict(bid):
    return {
        "id": bid.id,
        "rfp_id": bid.rfp_id,
        "org_id": bid.org_id,
        "status": bid.status,
        "submitted_at": bid.submitted_at.isoformat() if bid.submitted_at else None,
        "lot_bids": [lot_bid_dict(lb) for lb in bid.lot_bids.all().order_by("lot_id")],
    }


# ---------------------------------------------------------------------------
# Execution side
# ---------------------------------------------------------------------------


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
    from . import gs1

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
    from . import gs1

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


def api_token_dict(token):
    return {
        "id": token.id,
        "label": token.label,
        "prefix": token.prefix,
        "created_at": token.created_at.isoformat() if token.created_at else None,
        "last_used_at": token.last_used_at.isoformat() if token.last_used_at else None,
        "revoked": token.revoked,
    }
