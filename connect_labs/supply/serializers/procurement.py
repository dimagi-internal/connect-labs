"""Wire shapes for the procurement domain.

These dicts are the contract the SPA reads. ``org_dict`` is also what gets
frozen into ``EOISubmission.profile_snapshot`` at submit time, so changing its
shape changes what reviewers see for historic applications — add fields rather
than renaming them.
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
    """One live qualification, including WHO granted it and from WHICH application.

    The registry is the screen that answers "can this supplier be issued a
    solicitation today", and the follow-up question is always "who decided that,
    and against what". Granted/expires alone cannot answer it: the detail panel
    showed two dates and no decision-maker, so the eligibility judgment was
    visible but not defensible. Both come off the submission the decision froze.
    """
    submission = qual.source_submission
    review = None
    if submission is not None:
        # The decision that created this qualification. Latest wins — an amended
        # review supersedes the one before it.
        review = submission.reviews.order_by("-id").first()
    reviewer = getattr(review, "reviewer", None)
    return {
        "id": qual.id,
        "category": qual.category,
        "granted_at": qual.granted_at.isoformat(),
        "expires_at": qual.expires_at.isoformat(),
        "status": qual.status,
        # Who signed it off. None when the record predates a named reviewer —
        # rendered as an explicit gap rather than a blank, because "unknown" is
        # itself the finding an auditor would write up.
        # get_display_name is the User model's own name-or-username-or-email
        # fallback; this project's User has no first_name/last_name at all.
        "granted_by": reviewer.get_display_name() if reviewer else None,
        # The frozen application the decision was made against.
        "source_submission_id": submission.id if submission else None,
        "source_round": submission.round.title if submission else None,
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
        # What became of them. A closed round rendered a bare em-dash in its
        # action column with 14 applications behind it — the outcome of every one
        # of them unreachable from the table that counted them. The count alone
        # says a round happened; the breakdown says what it decided.
        "submission_breakdown": {
            status: rnd.submissions.filter(status=status).count() for status in ("submitted", "qualified", "rejected")
        },
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
