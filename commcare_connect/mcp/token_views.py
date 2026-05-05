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

from .models import MCPAccessToken
from .snippets import build_mcp_json_snippet


def _render_index(request, *, raw_token: str | None = None, raw_token_name: str | None = None):
    tokens = MCPAccessToken.objects.filter(user=request.user, is_active=True).order_by("-created_at")
    now = timezone.now()
    context = {
        "tokens": tokens,
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


def _parse_ttl(value: str | None) -> int | None:
    """Convert form-string ttl_days to int|None. 0/empty → None (no expiry)."""
    if value is None or value == "":
        return 90
    try:
        ttl = int(value)
    except (TypeError, ValueError):
        return 90
    if ttl <= 0:
        return None
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
