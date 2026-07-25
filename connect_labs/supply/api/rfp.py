from django.http import JsonResponse

from .. import audit
from ..decorators import current_actor, require_perm
from ..models import Bid, Lot, LotBid, RFP
from ..serializers import bid_dict, lot_bid_dict, lot_dict, rfp_dict
from ..services import rfp_actions
from ..services.org_actions import ActionError
from .common import handle_action_errors, json_body


@require_perm("bids", "view")
def _list_eligible_rfps(request):
    org = request.supply_actor.org
    return JsonResponse({"rfps": [rfp_dict(r) for r in rfp_actions.eligible_rfps(org)]})


@require_perm("rfps", "view")
def _list_all_rfps(request):
    return JsonResponse({"rfps": [rfp_dict(r) for r in RFP.objects.all().order_by("-created_at")]})


@require_perm("rfps", "manage")
@handle_action_errors
def _create_rfp(request):
    rfp = rfp_actions.create_rfp(request.user, json_body(request))
    audit.log_action(request, "rfp.create", "RFP", rfp.id, {"title": rfp.title})
    return JsonResponse({"rfp": rfp_dict(rfp)})


def rfps(request):
    """Suppliers see only solicitations they are qualified for; staff see all."""
    if request.method == "POST":
        return _create_rfp(request)
    if current_actor(request).role == "supplier":
        return _list_eligible_rfps(request)
    return _list_all_rfps(request)


def _get_rfp_or_404(rfp_id):
    try:
        return RFP.objects.prefetch_related("lots").get(id=rfp_id)
    except RFP.DoesNotExist:
        return None


@require_perm("bids", "view")
def _supplier_rfp_detail(request, rfp):
    org = request.supply_actor.org
    if not rfp_actions.org_can_bid(org, rfp) or rfp.status == RFP.Status.DRAFT:
        return JsonResponse({"error": "not found"}, status=404)
    my_bid = Bid.objects.filter(org=org, rfp=rfp).prefetch_related("lot_bids__bid__org").first()
    return JsonResponse({"rfp": rfp_dict(rfp), "my_bid": bid_dict(my_bid) if my_bid else None})


@require_perm("rfps", "view")
def _staff_rfp_detail(request, rfp):
    return JsonResponse({"rfp": rfp_dict(rfp), "my_bid": None})


def rfp_detail(request, rfp_id):
    rfp = _get_rfp_or_404(rfp_id)
    if rfp is None:
        return JsonResponse({"error": "not found"}, status=404)
    if current_actor(request).role == "supplier":
        return _supplier_rfp_detail(request, rfp)
    return _staff_rfp_detail(request, rfp)


@require_perm("rfps", "manage")
@handle_action_errors
def add_lot(request, rfp_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    rfp = _get_rfp_or_404(rfp_id)
    if rfp is None:
        return JsonResponse({"error": "not found"}, status=404)
    lot = rfp_actions.add_lot(rfp, json_body(request))
    audit.log_action(request, "rfp.lot.add", "Lot", lot.id, {"rfp": rfp.id})
    return JsonResponse({"lot": lot_dict(lot)})


@require_perm("rfps", "manage")
@handle_action_errors
def transition_rfp(request, rfp_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    rfp = _get_rfp_or_404(rfp_id)
    if rfp is None:
        return JsonResponse({"error": "not found"}, status=404)
    new_status = json_body(request).get("status")
    rfp_actions.transition_rfp(rfp, new_status)
    audit.log_action(request, "rfp.transition", "RFP", rfp.id, {"status": new_status})
    return JsonResponse({"rfp": rfp_dict(rfp)})


@require_perm("bids", "submit")
@handle_action_errors
def save_bid(request, rfp_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    rfp = _get_rfp_or_404(rfp_id)
    if rfp is None:
        return JsonResponse({"error": "not found"}, status=404)
    org = request.supply_actor.org
    if not rfp_actions.org_can_bid(org, rfp):
        return JsonResponse({"error": "your organisation is not qualified for this solicitation"}, status=403)
    bid = rfp_actions.save_bid(org, rfp, json_body(request).get("lot_bids"))
    audit.log_action(request, "bid.save", "Bid", bid.id, {"rfp": rfp.id})
    return JsonResponse({"bid": bid_dict(bid)})


@require_perm("bids", "submit")
@handle_action_errors
def submit_bid(request, rfp_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    try:
        bid = Bid.objects.select_related("rfp").get(org=org, rfp_id=rfp_id)
    except Bid.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    rfp_actions.submit_bid(bid)
    audit.log_action(request, "bid.submit", "Bid", bid.id, {"rfp": rfp_id})
    return JsonResponse({"bid": bid_dict(bid)})


@require_perm("scoring", "score")
@handle_action_errors
def score_lot_bid(request, lot_bid_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        lot_bid = LotBid.objects.get(id=lot_bid_id)
    except LotBid.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    body = json_body(request)
    rfp_actions.score_lot_bid(request.user, lot_bid, body.get("technical_score"), body.get("notes", ""))
    audit.log_action(request, "bid.score", "LotBid", lot_bid.id, {"score": body.get("technical_score")})
    return JsonResponse({"lot_bid": lot_bid_dict(lot_bid, include_scores=True)})


@require_perm("scoring", "view")
def comparison(request, rfp_id):
    rfp = _get_rfp_or_404(rfp_id)
    if rfp is None:
        return JsonResponse({"error": "not found"}, status=404)
    lots = []
    for lot in rfp.lots.all().order_by("id"):
        rows = []
        for rank, lot_bid in enumerate(rfp_actions.lot_comparison(lot), start=1):
            row = lot_bid_dict(lot_bid, include_scores=True)
            row["price_rank"] = rank
            rows.append(row)
        lots.append({"lot": lot_dict(lot), "lot_bids": rows})
    return JsonResponse({"rfp": rfp_dict(rfp), "lots": lots})


@require_perm("rfps", "award")
@handle_action_errors
def award_lot(request, lot_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        lot = Lot.objects.select_related("rfp").get(id=lot_id)
    except Lot.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    lot_bid_id = json_body(request).get("lot_bid_id")
    if lot_bid_id is None:
        raise ActionError("lot_bid_id is required")
    award = rfp_actions.award_lot(request.user, lot, lot_bid_id)
    audit.log_action(request, "lot.award", "Lot", lot.id, {"lot_bid": award.lot_bid_id})
    lot.refresh_from_db()
    return JsonResponse({"lot": lot_dict(lot)})
