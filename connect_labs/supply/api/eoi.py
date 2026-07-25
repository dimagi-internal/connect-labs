from django.http import JsonResponse

from .. import audit
from ..decorators import require_perm
from ..models import EOIRound, EOISubmission
from ..serializers import org_dict, qualification_dict, round_dict, submission_dict
from ..services import eoi_actions
from .common import handle_action_errors, json_body


@require_perm("eoi", "view")
@handle_action_errors
def submissions(request):
    """Supplier-scoped: list own submissions, or upsert a draft."""
    org = request.supply_actor.org
    if request.method == "POST":
        sub = eoi_actions.save_submission(org, json_body(request))
        audit.log_action(request, "eoi.submission.save", "EOISubmission", sub.id)
        return JsonResponse({"submission": submission_dict(sub)})
    qs = EOISubmission.objects.filter(org=org).select_related("round", "org").order_by("-created_at")
    return JsonResponse({"submissions": [submission_dict(s) for s in qs]})


@require_perm("eoi", "submit")
@handle_action_errors
def submit_submission(request, submission_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    org = request.supply_actor.org
    try:
        sub = EOISubmission.objects.select_related("round", "org").get(id=submission_id, org=org)
    except EOISubmission.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    eoi_actions.submit_submission(sub)
    audit.log_action(request, "eoi.submission.submit", "EOISubmission", sub.id)
    return JsonResponse({"submission": submission_dict(sub)})


@require_perm("rounds", "manage")
@handle_action_errors
def _create_round(request):
    rnd = eoi_actions.create_round(request.user, json_body(request))
    audit.log_action(request, "eoi.round.create", "EOIRound", rnd.id, {"title": rnd.title})
    return JsonResponse({"round": round_dict(rnd)})


@require_perm("eoi", "view")
def _list_open_rounds(request):
    qs = EOIRound.objects.filter(status=EOIRound.Status.OPEN).order_by("id")
    return JsonResponse({"rounds": [round_dict(r) for r in qs]})


@require_perm("rounds", "view")
def _list_all_rounds(request):
    qs = EOIRound.objects.all().order_by("-created_at")
    return JsonResponse({"rounds": [round_dict(r) for r in qs]})


def rounds(request):
    """Creating a round needs rounds.manage; listing is role-shaped —
    suppliers see open rounds, staff see everything."""
    if request.method == "POST":
        return _create_round(request)
    from ..decorators import current_actor

    if current_actor(request).role == "supplier":
        return _list_open_rounds(request)
    return _list_all_rounds(request)


@require_perm("rounds", "manage")
@handle_action_errors
def transition_round(request, round_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        rnd = EOIRound.objects.get(id=round_id)
    except EOIRound.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    new_status = json_body(request).get("status")
    eoi_actions.transition_round(rnd, new_status)
    audit.log_action(request, "eoi.round.transition", "EOIRound", rnd.id, {"status": new_status})
    return JsonResponse({"round": round_dict(rnd)})


@require_perm("eoi_review", "view")
def review_queue(request):
    qs = (
        EOISubmission.objects.filter(status=EOISubmission.Status.SUBMITTED)
        .select_related("round", "org")
        .order_by("submitted_at")
    )
    return JsonResponse({"submissions": [submission_dict(s) for s in qs]})


@require_perm("eoi_review", "decide")
@handle_action_errors
def review_submission(request, submission_id):
    if request.method != "POST":
        return JsonResponse({"error": "method not allowed"}, status=405)
    try:
        sub = EOISubmission.objects.select_related("round", "org").get(id=submission_id)
    except EOISubmission.DoesNotExist:
        return JsonResponse({"error": "not found"}, status=404)
    body = json_body(request)
    eoi_actions.review_submission(request.user, sub, body.get("decisions") or {}, body.get("notes", ""))
    audit.log_action(request, "eoi.submission.review", "EOISubmission", sub.id, {"decisions": body.get("decisions")})
    return JsonResponse({"submission": submission_dict(sub)})


@require_perm("registry", "view")
def registry(request):
    """The supplier registry: orgs grouped with their live qualifications."""
    quals = eoi_actions.live_qualifications(
        category=request.GET.get("category"),
        country=request.GET.get("country"),
        expiring_within_days=request.GET.get("expiring_within_days"),
    )
    rows = {}
    for qual in quals:
        row = rows.setdefault(
            qual.org_id, {"org": org_dict(qual.org, include_qualifications=False), "qualifications": []}
        )
        row["qualifications"].append(qualification_dict(qual))
    return JsonResponse({"registry": list(rows.values())})
