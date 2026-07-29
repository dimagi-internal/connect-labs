"""Demand-side endpoints: the partner's signal, and the centre's answer.

The two halves of one loop. A partner raises a shortfall from their own
distribution calendar; the centre reallocates against it and the signal
resolves against the action that resolved it — so the decision and the evidence
that prompted it end up as one record.
"""
from datetime import date

from django.http import JsonResponse

from .. import audit
from ..decorators import require_perm
from ..models import DistributionRecord, Shipment, ShortfallSignal, SupplyAction, SupplyNode
from ..serializers import child_outcome_dict, distribution_record_dict, shortfall_signal_dict, supply_action_dict
from ..services import actions, cover, coverage
from ..services.org_actions import ActionError
from .common import handle_action_errors, json_body, method_required


def _parse_date(value, field):
    if not value:
        raise ActionError(f"{field} is required")
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise ActionError(f"{field} must be an ISO date") from None


@require_perm("signals", "raise")
@method_required("POST")
@handle_action_errors
def raise_shortfall(request):
    """A partner reporting they will run short, from their own screen."""
    body = json_body(request)
    org = request.supply_actor.org
    site = SupplyNode.objects.filter(id=body.get("site_id")).first()
    if site is None:
        return JsonResponse({"error": "not found"}, status=404)

    signal = actions.raise_shortfall(
        org=org,
        site=site,
        needed_by=_parse_date(body.get("needed_by"), "needed_by"),
        children_affected=body.get("children_affected"),
        cartons_short=body.get("cartons_short") or 0,
        note=body.get("note", ""),
    )
    audit.log_action(request, "shortfall.raise", "ShortfallSignal", signal.id, {"site": site.name})
    return JsonResponse({"signal": shortfall_signal_dict(signal)})


@require_perm("signals", "view")
def signals(request):
    """The queue of open shortfalls. Partners see their own, staff see all."""
    actor = request.supply_actor
    qs = ShortfallSignal.objects.select_related("site", "org")
    if actor.role == "partner":
        qs = qs.filter(org=actor.org)
    return JsonResponse({"signals": [shortfall_signal_dict(s) for s in qs]})


@require_perm("actions", "create")
@method_required("POST")
@handle_action_errors
def reallocate(request):
    """Move surplus between nodes as a real consignment, and resolve a signal.

    The response carries the recomputed cover for both ends, because the point
    of the beat is that the numbers move as a consequence of the shipment
    rather than being adjusted alongside it.
    """
    body = json_body(request)
    source = SupplyNode.objects.filter(id=body.get("source_node_id")).first()
    target = SupplyNode.objects.filter(id=body.get("target_node_id")).first()
    if source is None or target is None:
        return JsonResponse({"error": "not found"}, status=404)

    signal = None
    if body.get("signal_id"):
        signal = ShortfallSignal.objects.filter(id=body["signal_id"]).first()

    action = actions.reallocate(
        # The person, by the name the rest of the screen calls them. The record
        # read "oes-lead@oes.example" while the chrome above it read "Ada
        # Nwosu", at the one moment the product asks you to read WHO decided —
        # which is half of what the action log exists to carry. The audit entry
        # below still keys on the account; this is the human-facing label.
        actor=(getattr(request.user, "name", "") or "").strip() or request.user.email or request.user.get_username(),
        source_node=source,
        target_node=target,
        quantity=body.get("quantity"),
        rationale=body.get("rationale", ""),
        signal=signal,
    )
    audit.log_action(
        request,
        "supply.reallocate",
        "SupplyAction",
        action.id,
        {"from": source.name, "to": target.name, "quantity": str(action.quantity)},
    )
    return JsonResponse(
        {
            "action": supply_action_dict(action),
            "cover": [c for c in (cover.cover_for_node(source), cover.cover_for_node(target)) if c],
        }
    )


@require_perm("actions", "create")
@method_required("POST")
@handle_action_errors
def expedite(request, shipment_id):
    shipment = Shipment.objects.filter(id=shipment_id).first()
    if shipment is None:
        return JsonResponse({"error": "not found"}, status=404)
    action = actions.expedite(
        actor=request.user.email or request.user.get_username(),
        shipment=shipment,
        rationale=json_body(request).get("rationale", ""),
    )
    audit.log_action(request, "supply.expedite", "SupplyAction", action.id, {"shipment": shipment.reference})
    return JsonResponse({"action": supply_action_dict(action)})


@require_perm("actions", "view")
def action_log(request):
    qs = SupplyAction.objects.select_related("source_node", "target_node", "shipment")
    return JsonResponse({"actions": [supply_action_dict(a) for a in qs[:200]]})


@require_perm("outcomes", "view")
def batch_drill(request, batch_lot):
    """A delivered batch, followed forward to the children it treated.

    The chain the funding standards describe and the sector abandons at the
    warehouse door: batch to distributions to a measurement series. Synthetic,
    and labelled so in every row it returns.
    """
    records = (
        DistributionRecord.objects.filter(batch_lot=batch_lot)
        .select_related("site", "org", "shipment_line__shipment")
        .prefetch_related("child_outcomes__site")
    )
    if not records.exists():
        return JsonResponse({"error": "not found"}, status=404)

    outcomes = [child_outcome_dict(c) for r in records for c in r.child_outcomes.all()]
    return JsonResponse(
        {
            "batch_lot": batch_lot,
            "records": [distribution_record_dict(r) for r in records],
            "outcomes": outcomes,
            "synthetic": True,
        }
    )


@require_perm("outcomes", "view")
def outcomes_summary(request):
    """Courses delivered beside recorded recoveries, each with its method."""
    country = None
    staff = getattr(request.user, "supply_staff_role", None)
    if request.supply_actor.role == "gov_observer" and staff:
        country = staff.country or None
    return JsonResponse(coverage.courses_versus_recoveries(country=country))
