"""The single role-scoped payload the SPA renders from.

The SPA holds no client-side store: after any mutation it re-fetches this
endpoint. Server state is the only state.
"""
from django.db import models
from django.http import JsonResponse
from django.utils import timezone

from ..decorators import current_actor
from ..models import (
    RFP,
    Appropriation,
    Bid,
    CaseloadEstimate,
    Contract,
    Discrepancy,
    DistributionPlan,
    DistributionRecord,
    EOIRound,
    EOISubmission,
    Shipment,
    ShortfallSignal,
    SupplyAction,
    SupplyNode,
)
from ..rbac import ROLE_PERMS
from ..serializers import (
    api_token_dict,
    appropriation_dict,
    bid_dict,
    caseload_dict,
    contract_dict,
    discrepancy_dict,
    distribution_plan_dict,
    distribution_record_dict,
    node_dict,
    org_dict,
    qualification_dict,
    rfp_dict,
    round_dict,
    shortfall_signal_dict,
    submission_dict,
    supply_action_dict,
)
from ..services import cover, coverage, eoi_actions, exceptions, rfp_actions


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


def _inbound_by_site(site_ids):
    """Cartons still on the road to each site, with their expected arrival.

    Only consignments that have not yet landed — anything delivered is already
    counted in stock on hand, and adding both would double-count the same
    cartons.
    """
    inbound = {}
    rows = Shipment.objects.filter(
        destination_id__in=site_ids,
        unit="cartons",
        status__in=[Shipment.Status.PLANNED, Shipment.Status.IN_TRANSIT],
    ).select_related("destination")
    for shipment in rows:
        eta = shipment.eta.date() if shipment.eta else None
        inbound.setdefault(shipment.destination_id, []).append((eta, float(shipment.quantity)))
    return inbound


def _plans_with_running_balance(plans, inbound, site_ids):
    """Resolve each planned distribution against a RUNNING balance per site.

    Cartons are spent when they are distributed. Scoring every planned day
    against the same opening stock let one site's 329 cartons cover both its
    5 August and its 12 August distribution, and the calendar then marked a day
    "covered" that the cover projection said the site would already be dry for —
    the two panels disagreeing about the same site on the same date.

    Walking the plans in date order and decrementing as they are served is what
    makes the calendar and the projection two views of one ledger instead of two
    independent guesses.
    """
    balance = {sid: float(cover.stock_on_hand_by_id(sid)) for sid in site_ids}
    pending = {sid: sorted(rows, key=lambda r: (r[0] is None, r[0])) for sid, rows in inbound.items()}

    rows_out = []
    for plan in plans:  # already ordered by scheduled_for
        sid = plan.site_id
        # Land anything due to arrive on or before this distribution day.
        still_out = []
        arrived = 0.0
        for eta, qty in pending.get(sid, []):
            if eta is not None and eta <= plan.scheduled_for:
                arrived += qty
            else:
                still_out.append((eta, qty))
        pending[sid] = still_out
        balance[sid] = balance.get(sid, 0.0) + arrived

        on_hand = balance.get(sid, 0.0)
        inbound_ahead = sum(q for _e, q in pending.get(sid, []))
        rows_out.append(distribution_plan_dict(plan, inbound_cartons=inbound_ahead, on_hand=on_hand))
        # Spend what this distribution consumes — capped at what is actually
        # there, because a site cannot dispense stock it does not hold.
        balance[sid] = max(0.0, on_hand - float(plan.cartons_required or 0))
    return rows_out


def _partner_world(actor):
    """Komadugu's view: their own sites, their own calendar, their own children.

    Scoped on the server exactly the way the government observer's country
    filter is. Another partner's sites are absent from the payload rather than
    hidden in the browser — the property that survives being asked "what else
    can this page see?".
    """
    org = actor.org
    sites = SupplyNode.objects.filter(owner=org).order_by("name")
    site_ids = list(sites.values_list("id", flat=True))

    plans = (
        DistributionPlan.objects.filter(org=org, site_id__in=site_ids)
        .select_related("site")
        .order_by("scheduled_for", "site__name")
    )
    plan_rows = _plans_with_running_balance(plans, _inbound_by_site(site_ids), site_ids)

    contracts = (
        Contract.objects.filter(shipments__destination_id__in=site_ids)
        .distinct()
        .select_related("award__lot", "org")
        .prefetch_related("shipments__origin", "shipments__destination", "shipments__milestones__node")
    )

    records = (
        DistributionRecord.objects.filter(org=org)
        .select_related("site", "org", "shipment_line__shipment")
        .prefetch_related("child_outcomes__site")
    )

    return {
        "org": org_dict(org),
        "nodes": [node_dict(n) for n in SupplyNode.objects.all()],
        "sites": [node_dict(n) for n in sites],
        "contracts": [contract_dict(c, include_shipments=True) for c in contracts],
        "discrepancies": [
            discrepancy_dict(d)
            for d in Discrepancy.objects.filter(shipment__destination_id__in=site_ids).select_related("shipment")
        ],
        "distribution_plans": plan_rows,
        "cover": cover.cover_by_node(nodes=sites),
        "shortfall_signals": [
            shortfall_signal_dict(s) for s in ShortfallSignal.objects.filter(org=org).select_related("site", "org")
        ],
        "distribution_records": [distribution_record_dict(r, include_outcomes=True) for r in records],
        "api_tokens": [api_token_dict(t) for t in org.api_tokens.all()],
    }


def _staff_world(actor):
    world = {}
    role = actor.role
    # Resolved once, up front: every block below that emits geography has to
    # apply the same scope, and reading it per-block is how one of them ends up
    # not applying it.
    gov_country = _gov_country(actor)
    if "eoi_review" in ROLE_PERMS.get(role, {}):
        queue = (
            EOISubmission.objects.filter(status=EOISubmission.Status.SUBMITTED)
            .select_related("round", "org")
            .order_by("submitted_at")
        )
        world["review_queue"] = [submission_dict(s) for s in queue]
        # Every application, so a closed round is not a dead end.
        #
        # `review_queue` is deliberately only the SUBMITTED ones — it is a
        # worklist. But the rounds table counted 8 applications beside a queue
        # showing 4, and offered no route to the other 4 or to any decided
        # application at all.
        world["eoi_submissions"] = [
            submission_dict(s) for s in EOISubmission.objects.select_related("round", "org").order_by("-submitted_at")
        ]
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

        # The demand side. Caseload is what turns delivery from a count into
        # coverage, and it is scoped with everything else — a government
        # observer reads their own districts, not the region's.
        caseloads = CaseloadEstimate.objects.all()
        if gov_country:
            caseloads = caseloads.filter(country=gov_country)
        world["caseloads"] = [caseload_dict(c) for c in caseloads]
        world["coverage"] = coverage.coverage_by_district(country=gov_country)
        world["cover"] = cover.cover_by_node()

    if "signals" in ROLE_PERMS.get(role, {}):
        world["shortfall_signals"] = [
            shortfall_signal_dict(s)
            for s in ShortfallSignal.objects.select_related("site", "org").exclude(
                status=ShortfallSignal.Status.RESOLVED
            )
        ]
        # Severity lives on the server so the ranking is testable and so the
        # partner surface and this queue cannot disagree about the same node.
        world["exceptions"] = exceptions.build_queue()
        # The queue's own advice is "reallocate from a node holding surplus".
        # Naming which nodes those are, and how much each could spare without
        # dropping below its own threshold, is what turns that sentence into
        # something the screen can actually do.
        world["surplus_nodes"] = cover.nodes_holding_surplus()
    if "actions" in ROLE_PERMS.get(role, {}):
        world["actions"] = [
            supply_action_dict(a)
            for a in SupplyAction.objects.select_related("source_node", "target_node", "shipment")[:50]
        ]
    if role == "funder":
        world["appropriations"] = [appropriation_dict(a) for a in Appropriation.objects.all()]
        world["coverage_by_country"] = coverage.coverage_by_country()
    if "outcomes" in ROLE_PERMS.get(role, {}):
        # The number everyone quotes, beside the one that was measured.
        world["outcomes"] = coverage.courses_versus_recoveries(country=gov_country)
        world["distribution_records"] = [
            distribution_record_dict(r, include_outcomes=True)
            for r in DistributionRecord.objects.select_related(
                "site", "org", "shipment_line__shipment"
            ).prefetch_related("child_outcomes__site")
        ]
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
        # The instant every figure in this payload is true of.
        #
        # No surface carried one, while rows carried dates spanning several days
        # either side of today. An auditor cannot cite a disbursement or a
        # coverage figure without knowing what moment it was true of, and the
        # absence is exactly what let a consignment dated tomorrow sit marked
        # "Delivered" with nothing on screen contradicting it. Set here rather
        # than per-role so no surface can be built without one.
        "as_of": timezone.localdate().isoformat(),
    }
    if actor.role == "supplier":
        data.update(_supplier_world(actor))
    elif actor.role == "partner":
        data.update(_partner_world(actor))
    else:
        data.update(_staff_world(actor))
    return data


def bootstrap(request):
    data = build_bootstrap(request)
    if data is None:
        return JsonResponse({"error": "authentication required"}, status=401)
    return JsonResponse(data)
