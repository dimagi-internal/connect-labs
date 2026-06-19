import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from commcare_connect.campaign.auth.decorators import current_campaign_user, require_perm
from commcare_connect.campaign.models import Campaign, Worker
from commcare_connect.campaign.services import serializers, worker_actions


def _body(request) -> dict:
    try:
        return json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}


def _campaign():
    return Campaign.objects.order_by("id").first()


def _ser(worker) -> dict:
    c = worker.campaign
    role_names = {r.role_id: r.name for r in c.worker_roles.all()}
    region_names = {r.region_id: r.name for r in c.regions.all()}
    return serializers._worker(worker, role_names, region_names)


@require_perm("payments", "approve")
def pay_set_status(request):
    data = _body(request)
    status = data.get("status")
    ids = data.get("worker_ids") or []
    if status not in ("paid", "approved", "pending", "rejected", "hold"):
        return JsonResponse({"error": "bad status"}, status=400)
    qs = Worker.objects.filter(campaign=_campaign(), worker_id__in=ids)
    updated, blocked = worker_actions.set_pay(qs, status)
    return JsonResponse({"workers": [_ser(w) for w in updated], "blocked": blocked})


@require_perm("payments", "approve")
def pay_queue(request, worker_id):
    data = _body(request)
    w = get_object_or_404(Worker, campaign=_campaign(), worker_id=worker_id)
    try:
        w = worker_actions.queue_pay(w, data.get("approved_count", 0))
    except worker_actions.FraudGuardError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"worker": _ser(w)})


@require_perm("kyc", "approve")
def kyc_status(request, worker_id):
    data = _body(request)
    status = data.get("status")
    if status not in ("approved", "pending", "review", "rejected"):
        return JsonResponse({"error": "bad status"}, status=400)
    w = get_object_or_404(Worker, campaign=_campaign(), worker_id=worker_id)
    try:
        w = worker_actions.set_kyc(w, status)
    except worker_actions.FraudGuardError as e:
        return JsonResponse({"error": str(e)}, status=400)
    return JsonResponse({"worker": _ser(w)})


@require_perm("kyc", "approve")
def kyc_resolve_dupe(request, worker_id):
    data = _body(request)
    w = get_object_or_404(Worker, campaign=_campaign(), worker_id=worker_id)
    w = worker_actions.resolve_duplicate(w, bool(data.get("keep")))
    return JsonResponse({"worker": _ser(w)})


@require_perm("kyc", "approve")
def kyc_investigation(request, worker_id):
    data = _body(request)
    w = get_object_or_404(Worker, campaign=_campaign(), worker_id=worker_id)
    cu = current_campaign_user(request)
    w = worker_actions.save_investigation(
        w,
        status=data.get("status", "Open"),
        outcome=data.get("outcome"),
        note=data.get("note"),
        by_name=(cu.name or cu.commcare_username),
    )
    return JsonResponse({"worker": _ser(w)})
