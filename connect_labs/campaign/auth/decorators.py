# connect_labs/campaign/auth/decorators.py
from __future__ import annotations

from functools import wraps

from django.http import JsonResponse
from django.shortcuts import redirect
from django.utils.decorators import method_decorator

from connect_labs.campaign.models import CampaignUser
from connect_labs.campaign.services import rbac


def current_campaign_user(request) -> CampaignUser | None:
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return None
    identity = (request.session.get("campaign_oauth") or {}).get("identity") or {}
    username = identity.get("username") or user.username
    if not username:
        return None
    return CampaignUser.objects.filter(commcare_username=username, status=CampaignUser.Status.ACTIVE).first()


def campaign_login_required(view_func):
    @wraps(view_func)
    def _inner(request, *args, **kwargs):
        if not getattr(request, "user", None) or not request.user.is_authenticated:
            return redirect("/campaign/login/")
        if not request.session.get("campaign_oauth"):
            return redirect("/campaign/login/")
        if current_campaign_user(request) is None:
            return redirect("/campaign/login/")
        return view_func(request, *args, **kwargs)

    return _inner


def require_perm(module: str, verb: str):
    def decorator(view_func):
        @wraps(view_func)
        @campaign_login_required
        def _inner(request, *args, **kwargs):
            cu = current_campaign_user(request)
            if cu is None or not rbac.can(cu.role, module, verb):
                return JsonResponse({"error": "forbidden", "detail": f"{module}:{verb} not permitted"}, status=403)
            return view_func(request, *args, **kwargs)

        return _inner

    return decorator


class CampaignLoginRequiredMixin:
    @method_decorator(campaign_login_required)
    def dispatch(self, request, *args, **kwargs):
        return super().dispatch(request, *args, **kwargs)
