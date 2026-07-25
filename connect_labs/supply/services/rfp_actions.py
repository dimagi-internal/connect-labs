"""RFP / lot / bid / scoring / award lifecycle.

Eligibility has a single source of truth here (``org_can_bid``): an org may bid
only while it holds a live qualification in one of the RFP's categories. The
registry is what gates access to solicitations — that is the whole point of the
two-stage EOI → RFP model.
"""
from datetime import date

from django.db import transaction
from django.utils import timezone

from ..models import RFP, Award, Bid, BidScore, Category, Lot, LotBid, Qualification
from .org_actions import ActionError

VALID_CATEGORIES = {c.value for c in Category}

RFP_TRANSITIONS = {
    RFP.Status.DRAFT: {RFP.Status.PUBLISHED},
    RFP.Status.PUBLISHED: {RFP.Status.CLOSED},
    RFP.Status.CLOSED: set(),
    RFP.Status.AWARDED: set(),
}


def live_qualification_categories(org):
    today = date.today()
    return set(
        Qualification.objects.filter(org=org, status=Qualification.Status.ACTIVE, expires_at__gte=today).values_list(
            "category", flat=True
        )
    )


def org_can_bid(org, rfp):
    return bool(live_qualification_categories(org) & set(rfp.categories))


def eligible_rfps(org):
    """Published RFPs the org is qualified for."""
    categories = live_qualification_categories(org)
    if not categories:
        return RFP.objects.none()
    ids = [r.id for r in RFP.objects.filter(status=RFP.Status.PUBLISHED) if set(r.categories) & categories]
    return RFP.objects.filter(id__in=ids).order_by("-created_at")


def create_rfp(user, data):
    title = (data.get("title") or "").strip()
    if not title:
        raise ActionError("title is required")
    categories = data.get("categories") or []
    unknown = set(categories) - VALID_CATEGORIES
    if unknown:
        raise ActionError(f"unknown categories: {sorted(unknown)}")
    return RFP.objects.create(
        title=title,
        brief=(data.get("brief") or "").strip(),
        categories=categories,
        countries=[c.upper() for c in (data.get("countries") or [])],
        bid_deadline=data.get("bid_deadline") or None,
        created_by=user,
    )


def add_lot(rfp, data):
    if rfp.status != RFP.Status.DRAFT:
        raise ActionError("lots can only be added while the RFP is a draft")
    category = data.get("category")
    if category not in VALID_CATEGORIES:
        raise ActionError("a valid category is required")
    try:
        quantity = float(data.get("quantity"))
    except (TypeError, ValueError):
        raise ActionError("quantity must be a number")
    return Lot.objects.create(
        rfp=rfp,
        category=category,
        description=(data.get("description") or "").strip(),
        quantity=quantity,
        unit=(data.get("unit") or "cartons").strip(),
        delivery_country=(data.get("delivery_country") or "").upper(),
        delivery_place=(data.get("delivery_place") or "").strip(),
        delivery_deadline=data.get("delivery_deadline") or None,
    )


def transition_rfp(rfp, new_status):
    if new_status not in RFP_TRANSITIONS.get(rfp.status, set()):
        raise ActionError(f"cannot move a {rfp.status} RFP to {new_status}")
    if new_status == RFP.Status.PUBLISHED and not rfp.lots.exists():
        raise ActionError("add at least one lot before publishing")
    rfp.status = new_status
    rfp.save(update_fields=["status"])
    return rfp


def _assert_bidding_open(rfp):
    if rfp.status != RFP.Status.PUBLISHED:
        raise ActionError("this solicitation is not open for bids")
    if rfp.bid_deadline and rfp.bid_deadline < date.today():
        raise ActionError("the bid deadline has passed")


@transaction.atomic
def save_bid(org, rfp, lot_bids_data):
    """Upsert the org's draft bid. Lot rows are replaced wholesale."""
    _assert_bidding_open(rfp)
    if not org_can_bid(org, rfp):
        raise ActionError("your organisation is not qualified for this solicitation")

    bid, _created = Bid.objects.get_or_create(org=org, rfp=rfp)
    if bid.status != Bid.Status.DRAFT:
        raise ActionError("this bid has already been submitted")

    rows = []
    lot_ids = set(rfp.lots.values_list("id", flat=True))
    for row in lot_bids_data or []:
        lot_id = row.get("lot_id")
        if lot_id not in lot_ids:
            raise ActionError(f"lot {lot_id} does not belong to this solicitation")
        try:
            price = float(row.get("unit_price"))
        except (TypeError, ValueError):
            raise ActionError("unit_price must be a number")
        rows.append(
            LotBid(
                bid=bid,
                lot_id=lot_id,
                unit_price=price,
                currency=(row.get("currency") or "USD").upper()[:3],
                lead_time_days=row.get("lead_time_days") or None,
                notes=(row.get("notes") or "").strip(),
            )
        )

    bid.lot_bids.all().delete()
    LotBid.objects.bulk_create(rows)
    return bid


def submit_bid(bid):
    _assert_bidding_open(bid.rfp)
    if bid.status != Bid.Status.DRAFT:
        raise ActionError("this bid has already been submitted")
    if not bid.lot_bids.exists():
        raise ActionError("price at least one lot before submitting")
    bid.status = Bid.Status.SUBMITTED
    bid.submitted_at = timezone.now()
    bid.save(update_fields=["status", "submitted_at"])
    return bid


def score_lot_bid(reviewer, lot_bid, technical_score, notes=""):
    try:
        score = int(technical_score)
    except (TypeError, ValueError):
        raise ActionError("technical_score must be a whole number 0-100")
    if not 0 <= score <= 100:
        raise ActionError("technical_score must be between 0 and 100")
    obj, _created = BidScore.objects.update_or_create(
        lot_bid=lot_bid, reviewer=reviewer, defaults={"technical_score": score, "notes": notes}
    )
    return obj


def lot_comparison(lot):
    """Submitted lot bids for a lot, cheapest first, with price rank."""
    rows = list(
        LotBid.objects.filter(lot=lot, bid__status=Bid.Status.SUBMITTED)
        .select_related("bid__org")
        .prefetch_related("scores__reviewer")
        .order_by("unit_price", "id")
    )
    return rows


@transaction.atomic
def award_lot(user, lot, lot_bid_id):
    if hasattr(lot, "award"):
        raise ActionError("this lot has already been awarded")
    try:
        lot_bid = LotBid.objects.select_related("bid", "lot__rfp").get(id=lot_bid_id, lot=lot)
    except LotBid.DoesNotExist:
        raise ActionError("that bid is not on this lot")
    if lot_bid.bid.status != Bid.Status.SUBMITTED:
        raise ActionError("only submitted bids can be awarded")

    award = Award.objects.create(lot=lot, lot_bid=lot_bid, awarded_by=user)

    rfp = lot.rfp
    if not rfp.lots.filter(award__isnull=True).exists():
        rfp.status = RFP.Status.AWARDED
        rfp.save(update_fields=["status"])
    return award
