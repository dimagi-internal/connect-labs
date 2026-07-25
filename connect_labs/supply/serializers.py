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
