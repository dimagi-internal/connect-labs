"""User-facing MCP Personal Access Token management.

Mounted at /labs/mcp/tokens/ via labs/urls.py. Each user can create, list,
revoke, and rotate their own tokens — no staff role required, since the
universally-permitted action is "manage your own credentials."

The raw token is rendered exactly once, in the response that creates it.
It is never persisted in session or shown on a refresh — same contract as
the mcp_create_token management command.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_http_methods, require_POST
from oauth2_provider.models import get_access_token_model, get_application_model, get_refresh_token_model

from .models import MCPAccessToken
from .snippets import build_mcp_json_snippet


def _connected_mcp_clients(user):
    """Apps this user has signed in to from an MCP client, newest sign-in first."""
    tokens = (
        get_access_token_model()
        .objects.filter(user=user, application__mcp_client__isnull=False, expires__gt=timezone.now())
        .select_related("application")
        .order_by("-created")
    )
    connected: dict[int, dict] = {}
    for token in tokens:
        connected.setdefault(
            token.application_id,
            {"application": token.application, "signed_in_at": token.created, "expires": token.expires},
        )
    return list(connected.values())


def _render_index(request, *, raw_token: str | None = None, raw_token_name: str | None = None):
    tokens = MCPAccessToken.objects.filter(user=request.user, is_active=True).order_by("-created_at")
    now = timezone.now()
    context = {
        "tokens": tokens,
        "connected_clients": _connected_mcp_clients(request.user),
        "now": now,
        "raw_token": raw_token,
        "raw_token_name": raw_token_name,
        "mcp_json_snippet": build_mcp_json_snippet(raw_token) if raw_token else None,
    }
    return render(request, "mcp/tokens.html", context)


@login_required
@require_http_methods(["GET"])
def tokens_index(request):
    return _render_index(request)


def _parse_ttl(value: str | None) -> int:
    """Convert form-string ttl_days to an int. Blank/invalid → 90.

    0 (historically "no expiry") and anything above the model's MAX_TTL_DAYS
    clamp to MAX_TTL_DAYS — self-service tokens must always age out.
    """
    if value is None or value == "":
        return 90
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        return 90
    if ttl <= 0 or ttl > MCPAccessToken.MAX_TTL_DAYS:
        return MCPAccessToken.MAX_TTL_DAYS
    return ttl


@login_required
@require_POST
def tokens_create(request):
    name = (request.POST.get("name") or "").strip()
    if not name:
        messages.error(request, "Token name is required.")
        return redirect(reverse("labs:mcp_tokens_index"))
    if len(name) > 100:
        name = name[:100]

    ttl_days = _parse_ttl(request.POST.get("ttl_days"))
    _, raw = MCPAccessToken.create_token(request.user, name=name, ttl_days=ttl_days)
    return _render_index(request, raw_token=raw, raw_token_name=name)


@login_required
@require_POST
def tokens_revoke(request, pk: int):
    token = get_object_or_404(MCPAccessToken, pk=pk, user=request.user)
    if token.is_active:
        token.is_active = False
        token.save(update_fields=["is_active"])
        messages.success(request, f"Revoked token “{token.name}”.")
    return redirect(reverse("labs:mcp_tokens_index"))


@login_required
@require_POST
def clients_disconnect(request, pk: int):
    """Cut a signed-in MCP client off for good.

    Deleting its access token is not enough, which is why this exists rather
    than a link to the toolkit's own "authorized tokens" page: that page deletes
    the AccessToken only, the RefreshToken survives it
    (``RefreshToken.access_token`` is SET_NULL) and carries no expiry of its own,
    so the client mints a fresh access token on its next call and "revoked"
    lasts until it next refreshes. Revoke the refresh tokens, then clear
    whatever access tokens are left.
    """
    application = get_object_or_404(get_application_model(), pk=pk, mcp_client__isnull=False)
    refresh_tokens = list(get_refresh_token_model().objects.filter(user=request.user, application=application))
    with transaction.atomic():
        for refresh_token in refresh_tokens:
            refresh_token.revoke()
        deleted, _ = get_access_token_model().objects.filter(user=request.user, application=application).delete()
    if refresh_tokens or deleted:
        messages.success(request, f"Disconnected “{application.name}”. It will have to sign in again.")
    return redirect(reverse("labs:mcp_tokens_index"))


@login_required
@require_POST
def tokens_rotate(request, pk: int):
    """Revoke + recreate-with-same-name in one step.

    Common when migrating between machines. Original TTL is not preserved —
    the new token defaults to 90 days, matching the management command.
    """
    old = get_object_or_404(MCPAccessToken, pk=pk, user=request.user)
    name = old.name
    with transaction.atomic():
        if old.is_active:
            old.is_active = False
            old.save(update_fields=["is_active"])
        _, raw = MCPAccessToken.create_token(request.user, name=name, ttl_days=90)
    return _render_index(request, raw_token=raw, raw_token_name=name)
