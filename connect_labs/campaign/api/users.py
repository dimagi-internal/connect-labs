import json

from django.http import JsonResponse
from django.shortcuts import get_object_or_404

from connect_labs.campaign.api.bootstrap import _select_campaign
from connect_labs.campaign.auth.decorators import current_campaign_user, require_perm
from connect_labs.campaign.models import CampaignUser
from connect_labs.campaign.services import audit, roles, serializers


def _campaign(request):
    # CampaignUser is global (not campaign-scoped), so this only decides which
    # campaign's audit log the entry attaches to — keep it the selected one.
    return _select_campaign(request)


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
    name = (d.get("name") or "").strip()
    scope = d.get("scope") or "All regions"
    cu, created = CampaignUser.objects.get_or_create(
        commcare_username=email,
        defaults={"email": email, "name": name, "role": key, "scope": scope, "status": "active"},
    )
    if not created:
        # Re-inviting an existing user re-roles and reactivates them (idempotent
        # upsert) rather than silently no-op'ing. Name is only overwritten when a
        # non-empty one is supplied so a bare re-invite can't wipe it.
        cu.role = key
        cu.scope = scope
        cu.status = "active"
        if name:
            cu.name = name
        cu.save(update_fields=["role", "scope", "status", "name"])
    verb = "Invited" if created else "Re-invited"
    audit.record(request, f"{verb} {email} ({roles.to_label(key)})", "User Management", _campaign(request))
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
    who = cu.name or cu.commcare_username
    audit.record(request, f"Changed {who}'s role to {roles.to_label(key)}", "User Management", _campaign(request))
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
    who = cu.name or cu.commcare_username
    verb = {"active": "Activated", "inactive": "Deactivated", "deactivated": "Deactivated"}[status]
    audit.record(request, f"{verb} user {who}", "User Management", _campaign(request))
    return JsonResponse({"user": _ser(cu, request)})
