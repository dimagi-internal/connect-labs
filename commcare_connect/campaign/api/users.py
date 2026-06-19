import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from commcare_connect.campaign.auth.decorators import current_campaign_user, require_perm
from commcare_connect.campaign.models import CampaignUser
from commcare_connect.campaign.services import roles, serializers


def _body(request):
    try:
        return json.loads(request.body or "{}")
    except (ValueError, TypeError):
        return {}


def _ser(cu, request):
    return serializers._user(cu, current_username=current_campaign_user(request).commcare_username)


@require_perm("users", "manage")
def user_invite(request):
    d = _body(request)
    email = (d.get("email") or "").strip()
    short = d.get("role") or "reporting"
    key = roles.to_key(short)
    if not email or "@" not in email or key is None:
        return JsonResponse({"error": "valid email and role required"}, status=400)
    cu, _ = CampaignUser.objects.get_or_create(
        commcare_username=email,
        defaults={
            "email": email,
            "name": (d.get("name") or "").strip(),
            "role": key,
            "scope": d.get("scope") or "All regions",
            "status": "active",
        },
    )
    return JsonResponse({"user": _ser(cu, request)})


@require_perm("users", "manage")
def user_set_role(request, username):
    key = roles.to_key(_body(request).get("role"))
    if key is None:
        return JsonResponse({"error": "bad role"}, status=400)
    if username == current_campaign_user(request).commcare_username:
        return JsonResponse({"error": "cannot change your own role"}, status=400)
    cu = get_object_or_404(CampaignUser, commcare_username=username)
    cu.role = key
    cu.save(update_fields=["role"])
    return JsonResponse({"user": _ser(cu, request)})


@require_perm("users", "manage")
def user_set_status(request, username):
    status = _body(request).get("status")
    if status not in ("active", "inactive", "deactivated"):
        return JsonResponse({"error": "bad status"}, status=400)
    if username == current_campaign_user(request).commcare_username:
        return JsonResponse({"error": "cannot change your own status"}, status=400)
    cu = get_object_or_404(CampaignUser, commcare_username=username)
    cu.status = status
    cu.save(update_fields=["status"])
    return JsonResponse({"user": _ser(cu, request)})
