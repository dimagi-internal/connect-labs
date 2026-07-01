import json

from django.http import JsonResponse

from connect_labs.campaign.api.bootstrap import _select_campaign
from connect_labs.campaign.auth.decorators import current_campaign_user, require_perm
from connect_labs.campaign.services import audit, serializers, worker_actions, worker_cases

INVESTIGATION_STATUSES = {"Open", "Under Review", "Resolved", "False Positive"}


def _body(request) -> dict:
    try:
        return json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}


def _campaign(request):
    # Resolve the same campaign the bootstrap/list views selected (national
    # CommCare-domain campaign when one exists), so mutations land on the campaign
    # the operator is actually looking at — not whatever sorts first by id.
    return _select_campaign(request)


def _ser(worker, campaign) -> dict:
    role_names = {r.role_id: r.name for r in campaign.worker_roles.all()}
    region_names = {r.region_id: r.name for r in campaign.regions.all()}
    return serializers._worker(worker, role_names, region_names)


@require_perm("workers", "view")
def workers_list(request):
    """Filtered + paginated worker list — the tables fetch from here instead of the
    bootstrap shipping every worker. Params: page, page_size, q, kyc, pay, role,
    region, fraud (flagged|clean). Reads the same campaign the bootstrap selected."""
    campaign = _select_campaign(request)
    if campaign is None:
        return JsonResponse({"workers": [], "total": 0, "page": 1, "page_size": 50})
    g = request.GET
    try:
        page = int(g.get("page", 1) or 1)
        page_size = int(g.get("page_size", 50) or 50)
    except (TypeError, ValueError):
        page, page_size = 1, 50
    workers, total = worker_cases.query_workers(
        campaign,
        q=g.get("q", ""),
        kyc=g.get("kyc", ""),
        pay=g.get("pay", ""),
        role=g.get("role", ""),
        region=g.get("region", ""),
        fraud=g.get("fraud", ""),
        page=page,
        page_size=page_size,
    )
    return JsonResponse({"workers": workers, "total": total, "page": page, "page_size": page_size})


@require_perm("payments", "approve")
def pay_set_status(request):
    data = _body(request)
    status = data.get("status")
    ids = data.get("worker_ids") or []
    if status not in ("paid", "approved", "pending", "rejected", "hold"):
        return JsonResponse({"error": "bad status"}, status=400)
    campaign = _campaign(request)
    workers = worker_cases.resolve_workers(campaign, ids)
    updated, blocked = worker_actions.set_pay(workers, status)
    if updated:
        verb = {"paid": "Marked paid", "approved": "Approved", "hold": "Held"}.get(status, f"Set {status} on")
        audit.record(request, f"{verb} {len(updated)} worker payment(s)", "Payments", campaign)
    return JsonResponse({"workers": [_ser(w, campaign) for w in updated], "blocked": blocked})


@require_perm("payments", "approve")
def pay_queue(request, worker_id):
    data = _body(request)
    campaign = _campaign(request)
    w = worker_cases.resolve_worker(campaign, worker_id)
    if w is None:
        return JsonResponse({"error": "worker not found"}, status=404)
    try:
        w = worker_actions.queue_pay(w, data.get("approved_count", 0))
    except worker_actions.FraudGuardError as e:
        return JsonResponse({"error": str(e)}, status=400)
    audit.record(request, f"Queued payment for {worker_id}", "Payments", campaign)
    return JsonResponse({"worker": _ser(w, campaign)})


@require_perm("kyc", "approve")
def kyc_status(request, worker_id):
    data = _body(request)
    status = data.get("status")
    if status not in ("approved", "pending", "review", "rejected"):
        return JsonResponse({"error": "bad status"}, status=400)
    campaign = _campaign(request)
    w = worker_cases.resolve_worker(campaign, worker_id)
    if w is None:
        return JsonResponse({"error": "worker not found"}, status=404)
    try:
        w = worker_actions.set_kyc(w, status)
    except worker_actions.FraudGuardError as e:
        return JsonResponse({"error": str(e)}, status=400)
    audit.record(request, f"Set KYC to {status} for {worker_id}", "KYC", campaign)
    return JsonResponse({"worker": _ser(w, campaign)})


@require_perm("kyc", "approve")
def kyc_resolve_dupe(request, worker_id):
    data = _body(request)
    if "keep" not in data:
        return JsonResponse({"error": "missing 'keep'"}, status=400)
    campaign = _campaign(request)
    w = worker_cases.resolve_worker(campaign, worker_id)
    if w is None:
        return JsonResponse({"error": "worker not found"}, status=404)
    w = worker_actions.resolve_duplicate(w, bool(data.get("keep")))
    return JsonResponse({"worker": _ser(w, campaign)})


@require_perm("kyc", "approve")
def kyc_investigation(request, worker_id):
    data = _body(request)
    status = data.get("status")
    if status and status not in INVESTIGATION_STATUSES:
        return JsonResponse({"error": "bad investigation status"}, status=400)
    campaign = _campaign(request)
    w = worker_cases.resolve_worker(campaign, worker_id)
    if w is None:
        return JsonResponse({"error": "worker not found"}, status=404)
    cu = current_campaign_user(request)
    w = worker_actions.save_investigation(
        w,
        status=status or "Open",
        outcome=data.get("outcome"),
        note=data.get("note"),
        by_name=(cu.name or cu.commcare_username),
    )
    return JsonResponse({"worker": _ser(w, campaign)})
