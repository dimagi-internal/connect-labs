"""The single role-scoped payload the SPA renders from.

The SPA holds no client-side store: after any mutation it re-fetches this
endpoint. Server state is the only state.
"""
from django.db import models
from django.http import JsonResponse

from ..decorators import current_actor
from ..models import RFP, Appropriation, Bid, Contract, Discrepancy, EOIRound, EOISubmission, SupplyNode
from ..rbac import ROLE_PERMS
from ..serializers import (
    api_token_dict,
    appropriation_dict,
    bid_dict,
    contract_dict,
    discrepancy_dict,
    node_dict,
    org_dict,
    qualification_dict,
    rfp_dict,
    round_dict,
    submission_dict,
)
from ..services import eoi_actions, rfp_actions


def _supplier_world(actor):
    org = actor.org
    open_rounds = EOIRound.objects.filter(status=EOIRound.Status.OPEN).order_by("id")
    my_subs = EOISubmission.objects.filter(org=org).select_related("round", "org").order_by("-created_at")
    submitted_round_ids = set(my_subs.values_list("round_id", flat=True))

    eligible = rfp_actions.eligible_rfps(org)
    my_bids = {b.rfp_id: b for b in Bid.objects.filter(org=org).prefetch_related("lot_bids__bid__org")}
    rfps = []
    for rfp in eligible:
        data = rfp_dict(rfp)
        bid = my_bids.get(rfp.id)
        data["my_bid"] = bid_dict(bid) if bid else None
        rfps.append(data)

    contracts = (
        Contract.objects.filter(org=org)
        .select_related("award__lot", "org")
        .prefetch_related("shipments__origin", "shipments__destination", "shipments__milestones__node")
    )
    return {
        "org": org_dict(org),
        "contracts": [contract_dict(c, include_shipments=True) for c in contracts],
        "discrepancies": [
            discrepancy_dict(d)
            for d in Discrepancy.objects.filter(shipment__contract__org=org).select_related("shipment")
        ],
        "nodes": [node_dict(n) for n in SupplyNode.objects.all()],
        "api_tokens": [api_token_dict(t) for t in org.api_tokens.all()],
        "open_rounds": [{**round_dict(r), "applied": r.id in submitted_round_ids} for r in open_rounds],
        "my_submissions": [submission_dict(s) for s in my_subs],
        "eligible_rfps": rfps,
    }


def _staff_world(actor):
    world = {}
    role = actor.role
    if "eoi_review" in ROLE_PERMS.get(role, {}):
        queue = (
            EOISubmission.objects.filter(status=EOISubmission.Status.SUBMITTED)
            .select_related("round", "org")
            .order_by("submitted_at")
        )
        world["review_queue"] = [submission_dict(s) for s in queue]
    if "registry" in ROLE_PERMS.get(role, {}):
        rows = {}
        for qual in eoi_actions.live_qualifications():
            row = rows.setdefault(
                qual.org_id,
                {"org": org_dict(qual.org, include_qualifications=False), "qualifications": []},
            )
            row["qualifications"].append(qualification_dict(qual))
        world["registry"] = list(rows.values())
    if "rounds" in ROLE_PERMS.get(role, {}):
        world["rounds"] = [round_dict(r) for r in EOIRound.objects.all().order_by("-created_at")]
    if "rfps" in ROLE_PERMS.get(role, {}):
        world["rfps"] = [rfp_dict(r) for r in RFP.objects.prefetch_related("lots").all().order_by("-created_at")]
    if "execution" in ROLE_PERMS.get(role, {}):
        contracts = Contract.objects.select_related("award__lot", "org").prefetch_related(
            "shipments__origin", "shipments__destination", "shipments__milestones__node"
        )
        gov_country = _gov_country(actor)
        if gov_country:
            # Country scoping happens here, not in the browser: an observer's
            # payload must never contain another country's consignments.
            contracts = contracts.filter(
                models.Q(shipments__origin__country=gov_country)
                | models.Q(shipments__destination__country=gov_country)
            ).distinct()
        world["contracts"] = [contract_dict(c, include_shipments=True) for c in contracts]
        world["discrepancies"] = [discrepancy_dict(d) for d in Discrepancy.objects.select_related("shipment").all()]
        nodes = SupplyNode.objects.all()
        world["nodes"] = [node_dict(n) for n in nodes]
    if role == "funder":
        world["appropriations"] = [appropriation_dict(a) for a in Appropriation.objects.all()]
    gov_country = _gov_country(actor)
    if gov_country:
        world["scope_country"] = gov_country
    return world


def _gov_country(actor):
    """The country a government observer is scoped to, or None for other roles."""
    if actor.role != "gov_observer":
        return None
    staff = getattr(actor.user, "supply_staff_role", None)
    return (staff.country or None) if staff else None


def build_bootstrap(request):
    actor = current_actor(request)
    if actor.role is None:
        return None
    data = {
        "user": {
            "id": actor.user.id,
            "email": actor.user.email,
            "name": actor.user.get_display_name(),
        },
        "role": actor.role,
        "perms": ROLE_PERMS.get(actor.role, {}),
        "org": None,
    }
    if actor.role == "supplier":
        data.update(_supplier_world(actor))
    else:
        data.update(_staff_world(actor))
    return data


def bootstrap(request):
    data = build_bootstrap(request)
    if data is None:
        return JsonResponse({"error": "authentication required"}, status=401)
    return JsonResponse(data)
