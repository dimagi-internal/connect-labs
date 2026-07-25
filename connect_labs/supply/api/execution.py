"""Session-authenticated execution endpoints (supplier ops + staff oversight)."""
from django.http import JsonResponse

from .. import audit
from ..decorators import require_perm
from ..models import Contract, Discrepancy, Shipment
from ..serializers import api_token_dict, contract_dict, discrepancy_dict, node_dict, shipment_dict
from ..services import ingestion, tokens
from ..services.org_actions import ActionError
from .common import handle_action_errors, json_body


@require_perm("execution", "view")
def contracts(request):
    """Suppliers see their own contracts; staff see all."""
    actor = request.supply_actor
    qs = Contract.objects.select_related("org", "award__lot").prefetch_related("shipments")
    if actor.role == "supplier":
        qs = qs.filter(org=actor.org)
    return JsonResponse({"contracts": [contract_dict(c, include_shipments=True) for c in qs]})


@require_perm("execution", "view")
def shipment_detail(request, shipment_id):
    actor = request.supply_actor
    qs = Shipment.objects.select_related("contract__org", "origin", "destination")
    if actor.role == "supplier":
        qs = qs.filter(contract__org=actor.org)
    shipment = qs.filter(id=shipment_id).first()
    if shipment is None:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse({"shipment": shipment_dict(shipment, include_detail=True)})


@require_perm("execution", "report")
@handle_action_errors
def confirm_delivery(request, shipment_id):
    """Portal-tier delivery confirmation — the lowest ingestion tier."""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    shipment = Shipment.objects.filter(id=shipment_id, contract__org=org).first()
    if shipment is None:
        return JsonResponse({"error": "not found"}, status=404)
    body = json_body(request)
    ingestion.confirm_delivery(org, shipment, quantity=body.get("quantity"), note=body.get("note", ""))
    audit.log_action(request, "shipment.confirm", "Shipment", shipment.id)
    shipment.refresh_from_db()
    return JsonResponse({"shipment": shipment_dict(shipment, include_detail=True)})


@require_perm("execution", "report")
@handle_action_errors
def checkin(request, shipment_id):
    """Portal equivalent of the phone-app check-in."""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    shipment = Shipment.objects.filter(id=shipment_id, contract__org=org).first()
    if shipment is None:
        return JsonResponse({"error": "not found"}, status=404)
    body = dict(json_body(request))
    body["shipment_reference"] = shipment.reference
    event, _created = ingestion.capture_checkin(org, body)
    audit.log_action(request, "shipment.checkin", "Shipment", shipment.id, {"step": event.biz_step})
    shipment.refresh_from_db()
    return JsonResponse({"shipment": shipment_dict(shipment, include_detail=True)})


@require_perm("execution", "report")
@handle_action_errors
def create_shipment(request):
    """Webform equivalent of the ASN endpoint.

    Identical payload shape to ``POST /supply/api/v1/shipments/`` — a supplier
    with no integration keys in the same despatch by hand, and it is recorded
    as portal-tier so the provenance stays honest.
    """
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    from ..models import SupplyEvent

    org = request.supply_actor.org
    shipment, created = ingestion.capture_despatch_advice(
        org, json_body(request), source_tier=SupplyEvent.SourceTier.PORTAL
    )
    audit.log_action(request, "shipment.declare", "Shipment", shipment.id, {"created": created})
    return JsonResponse({"shipment": shipment_dict(shipment, include_detail=True)})


@require_perm("execution", "report")
@handle_action_errors
def record_event(request, shipment_id):
    """Webform equivalent of an EPCIS event."""
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    shipment = Shipment.objects.filter(id=shipment_id, contract__org=org).first()
    if shipment is None:
        return JsonResponse({"error": "not found"}, status=404)
    event, _created = ingestion.record_manual_event(org, shipment, json_body(request))
    audit.log_action(request, "shipment.event", "Shipment", shipment.id, {"step": event.biz_step})
    shipment.refresh_from_db()
    return JsonResponse({"shipment": shipment_dict(shipment, include_detail=True)})


@require_perm("execution", "view")
def discrepancies(request):
    actor = request.supply_actor
    qs = Discrepancy.objects.select_related("shipment__contract__org")
    if actor.role == "supplier":
        qs = qs.filter(shipment__contract__org=actor.org)
    return JsonResponse({"discrepancies": [discrepancy_dict(d) for d in qs]})


@require_perm("execution", "resolve")
@handle_action_errors
def resolve_discrepancy(request, discrepancy_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    from django.utils import timezone

    disc = Discrepancy.objects.filter(id=discrepancy_id).first()
    if disc is None:
        return JsonResponse({"error": "not found"}, status=404)
    disc.status = Discrepancy.Status.RESOLVED
    disc.resolved_at = timezone.now()
    disc.note = f"{disc.note}\nResolved: {json_body(request).get('note', '')}".strip()
    disc.save(update_fields=["status", "resolved_at", "note"])
    audit.log_action(request, "discrepancy.resolve", "Discrepancy", disc.id)
    return JsonResponse({"discrepancy": discrepancy_dict(disc)})


@require_perm("execution", "view")
def nodes(request):
    from ..models import SupplyNode

    return JsonResponse({"nodes": [node_dict(n) for n in SupplyNode.objects.all()]})


# ---------------------------------------------------------------------------
# API tokens (supplier self-service)
# ---------------------------------------------------------------------------


@require_perm("tokens", "manage")
@handle_action_errors
def api_tokens(request):
    org = request.supply_actor.org
    if request.method == "POST":
        label = (json_body(request).get("label") or "").strip()
        if not label:
            raise ActionError("label is required")
        token, raw = tokens.mint_token(org, label)
        audit.log_action(request, "token.mint", "ApiToken", token.id, {"label": label})
        # The raw token is returned exactly once.
        return JsonResponse({"token": api_token_dict(token), "secret": raw})
    return JsonResponse({"tokens": [api_token_dict(t) for t in org.api_tokens.all()]})


@require_perm("tokens", "manage")
@handle_action_errors
def revoke_api_token(request, token_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    if not tokens.revoke_token(org, token_id):
        return JsonResponse({"error": "not found"}, status=404)
    audit.log_action(request, "token.revoke", "ApiToken", token_id)
    return JsonResponse({"ok": True})
