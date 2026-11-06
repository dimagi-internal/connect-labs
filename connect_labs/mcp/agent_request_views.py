"""Views for an agent asking a person for a temporary no-user-visit token (``agent_requests``).

Two JSON endpoints the AGENT calls, signed with its own labs token (``Authorization:
Bearer``): ``create`` and ``claim``. One page the PERSON uses, signed in to labs: it shows
who asked and what they would get, and approves or declines.
"""

from __future__ import annotations

import json

from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from . import agent_requests, oauth, token_scopes
from .models import AgentTokenRequest, MCPAccessToken


def _agent(request):
    """The labs account behind the request's bearer token, or a 401 response.

    Any active token will do (full or no-user-visit): what is asked for is fixed by this
    flow, not by the asker's own level.
    """
    header = request.headers.get("authorization", "")
    raw = header[len("Bearer ") :].strip() if header.lower().startswith("bearer ") else ""
    token = MCPAccessToken.verify(raw)
    if token is None:
        response = JsonResponse({"error": {"code": "UNAUTHORIZED", "message": "Missing or invalid token"}}, status=401)
        response["WWW-Authenticate"] = 'Bearer realm="labs-mcp"'
        return None, response
    token.touch()
    return token.user, None


def _error(exc: agent_requests.AgentRequestError, status: int):
    return JsonResponse({"error": {"code": exc.code.upper(), "message": exc.message}}, status=status)


def approval_url(request, req: AgentTokenRequest) -> str:
    path = reverse("labs:mcp_agent_request", args=[req.ref])
    base = oauth.public_base_url()
    return f"{base}{path}" if base else request.build_absolute_uri(path)


@csrf_exempt
@require_http_methods(["POST"])
def agent_request_create(request):
    """``POST {reason, recipient_key}`` -> ``201 {ref, approve_url, expires_at}``."""
    agent, err = _agent(request)
    if err:
        return err
    try:
        body = json.loads(request.body or b"{}")
    except ValueError:
        return JsonResponse({"error": {"code": "BAD_REQUEST", "message": "Body must be JSON"}}, status=400)
    if not isinstance(body, dict):
        return JsonResponse({"error": {"code": "BAD_REQUEST", "message": "Body must be a JSON object"}}, status=400)
    try:
        req = agent_requests.create(agent, reason=body.get("reason"), recipient_key=body.get("recipient_key"))
    except agent_requests.AgentRequestError as exc:
        return _error(exc, 429 if exc.code == "too_many_pending" else 400)
    return JsonResponse(
        {
            "ref": req.ref,
            "approve_url": approval_url(request, req),
            "expires_at": req.expires_at.isoformat(),
            "access": token_scopes.NO_USERVISIT_DATA,
        },
        status=201,
    )


@csrf_exempt
@require_http_methods(["POST"])
def agent_request_claim(request, ref):
    """``POST`` -> ``{status}`` (``pending``, ``expired``, ``declined``, ``claimed``) or, once,
    ``{status: "approved", sealed_token}`` for the agent to unseal with its private key."""
    agent, err = _agent(request)
    if err:
        return err
    try:
        return JsonResponse(agent_requests.claim(ref, agent))
    except agent_requests.AgentRequestError as exc:
        return _error(exc, 404)


@login_required
@require_http_methods(["GET", "POST"])
def agent_request_page(request, ref):
    """The person's side: who asked, what they would get, approve or decline."""
    req = AgentTokenRequest.objects.select_related("requested_by").filter(ref=ref).first()
    if req is None:
        return render(request, "mcp/agent_request.html", {"missing": True}, status=404)
    outcome = ""
    status = 200
    if request.method == "POST":
        action = request.POST.get("action")
        try:
            if action == "approve":
                agent_requests.approve(ref, request.user)
                outcome = "approved"
            elif action == "decline":
                agent_requests.decline(ref, request.user)
                outcome = "declined"
            else:
                outcome, status = "bad_action", 400
        except agent_requests.AgentRequestError as exc:
            outcome, status = exc.code, 409 if exc.code in ("already_answered", "expired") else 403
        req.refresh_from_db()
    return render(
        request,
        "mcp/agent_request.html",
        {
            "req": req,
            "outcome": outcome,
            "open": req.status == AgentTokenRequest.PENDING and not req.expired,
            "is_requester": request.user.pk == req.requested_by_id,
            "access_label": dict(token_scopes.SCOPE_CHOICES)[token_scopes.NO_USERVISIT_DATA],
            "access_text": token_scopes.ACCESS_TEXT[token_scopes.NO_USERVISIT_DATA],
            "lasts": "1 hour, then it stops working on its own.",
        },
        status=status,
    )
