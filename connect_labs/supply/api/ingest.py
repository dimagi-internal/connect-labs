"""The supplier-facing ingestion API (``/supply/api/v1/``).

Authenticated by an org-scoped bearer token, so a supplier's own system can
post without a browser session. Three capture endpoints matching the three
capability tiers, plus a pull endpoint whose payload matches what webhooks
would push — push/pull parity is how a real visibility platform lets a partner
reconcile.
"""
import json
from functools import wraps

from django.http import JsonResponse
from django.views.decorators.csrf import csrf_exempt

from ..models import Shipment
from ..serializers import event_dict, shipment_dict
from ..services import ingestion, tokens
from ..services.org_actions import ActionError


def token_required(view):
    """Bearer-token auth. Sets ``request.supply_org``."""

    @wraps(view)
    @csrf_exempt
    def wrapped(request, *args, **kwargs):
        header = request.headers.get("Authorization", "")
        if not header.lower().startswith("bearer "):
            return JsonResponse({"error": "bearer token required"}, status=401)
        org = tokens.resolve_token(header[7:])
        if org is None:
            return JsonResponse({"error": "invalid or revoked token"}, status=401)
        request.supply_org = org
        try:
            return view(request, *args, **kwargs)
        except ActionError as exc:
            return JsonResponse({"error": str(exc)}, status=400)

    return wrapped


def _body(request):
    if request.method != "POST":
        return None, JsonResponse({"error": "method not allowed"}, status=405)
    try:
        return json.loads(request.body or b"{}"), None
    except ValueError:
        return None, JsonResponse({"error": "body must be valid JSON"}, status=400)


@token_required
def epcis_capture(request):
    """GS1 EPCIS 2.0 capture endpoint.

    Accepts an ``EPCISDocument``; returns a capture summary. Repeat posts of
    the same ``eventID`` are counted as duplicates, not errors.
    """
    payload, error = _body(request)
    if error:
        return error
    result = ingestion.capture_epcis_document(request.supply_org, payload)
    return JsonResponse(result, status=201 if result["captured"] else 200)


@token_required
def shipments(request):
    """Despatch advice (ASN shape) → creates a shipment with lines and legs."""
    payload, error = _body(request)
    if error:
        return error
    shipment, created = ingestion.capture_despatch_advice(request.supply_org, payload)
    return JsonResponse({"shipment": shipment_dict(shipment)}, status=201 if created else 200)


@token_required
def checkins(request):
    """Low-tech tier: a phone-app confirmation on a corridor leg."""
    payload, error = _body(request)
    if error:
        return error
    event, created = ingestion.capture_checkin(request.supply_org, payload)
    return JsonResponse({"event": event_dict(event)}, status=201 if created else 200)


@token_required
def shipment_events(request, shipment_id):
    """Pull parity: exactly what the webhooks for this shipment would push."""
    if request.method != "GET":
        return JsonResponse({"error": "method not allowed"}, status=405)
    shipment = Shipment.objects.filter(id=shipment_id, contract__org=request.supply_org).first()
    if shipment is None:
        return JsonResponse({"error": "not found"}, status=404)
    return JsonResponse(
        {
            "shipment": shipment_dict(shipment),
            "events": [event_dict(e) for e in shipment.events.all()],
        }
    )
