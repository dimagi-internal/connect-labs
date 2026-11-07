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

from . import agent_requests, full_access, oauth, runner_check, token_scopes
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


def endpoint_url(request) -> str:
    """The URL the agent POSTed to (the runner proof is bound to it).

    From the named route, not ``request.path``: the MCP admin routes sit behind a Starlette
    mount that may strip a prefix, and the runner signed the public URL."""
    path = reverse("mcp:agent_request_create")
    base = oauth.public_base_url()
    return f"{base}{path}" if base else request.build_absolute_uri(path)


@csrf_exempt
@require_http_methods(["POST"])
def agent_request_create(request):
    """``POST {reason, recipient_key, scope?, agent?}`` -> ``201 {ref, approve_url, expires_at, access, runner}``.

    ``X-Canopy-Runner-Proof`` (``canopy vault runner-proof``) is optional: with it, canopy says
    which approved runner asked (``runner_check``); without it the request is "not verified".

    ``scope`` is ``no-uservisit-data`` (the default) or ``full``; the person's passkey is
    needed for either."""
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
        runner = runner_check.check(request, endpoint_url(request), body.get("agent"))
        req = agent_requests.create(
            agent,
            reason=body.get("reason"),
            recipient_key=body.get("recipient_key"),
            scope=body.get("scope") or token_scopes.NO_USERVISIT_DATA,
            runner=runner,
        )
    except agent_requests.AgentRequestError as exc:
        status = 429 if exc.code == "too_many_pending" else 401 if exc.code == "runner_proof_refused" else 400
        return _error(exc, status)
    return JsonResponse(
        {
            "ref": req.ref,
            "approve_url": approval_url(request, req),
            "expires_at": req.expires_at.isoformat(),
            "access": req.scope,
            "runner": req.runner or None,
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


def render_page(request, ref, *, outcome="", notice=None, status=200):
    """The person's page for one request: who asked, what they would get, and what happened."""
    req = AgentTokenRequest.objects.select_related("requested_by").filter(ref=ref).first()
    if req is None:
        return render(request, "mcp/agent_request.html", {"missing": True}, status=404)
    return render(
        request,
        "mcp/agent_request.html",
        {
            "req": req,
            "outcome": outcome,
            "full_access_notice": notice,
            "open": req.status == AgentTokenRequest.PENDING and not req.expired,
            "is_requester": request.user.pk == req.requested_by_id,
            "access_label": dict(token_scopes.SCOPE_CHOICES)[req.scope],
            "access_text": token_scopes.ACCESS_TEXT[req.scope],
            "is_full": req.scope == token_scopes.FULL,
            "lasts": "1 hour, then it stops working on its own.",
            "runner_line": runner_line(req),
        },
        status=status,
    )


def runner_line(req: AgentTokenRequest) -> str:
    """One plain sentence on where the request came from, for the page and the passkey card."""
    if not req.runner:
        return "Not verified: nothing showed this came from an approved canopy runner."
    line = f"Sent from the canopy runner \u201c{req.runner}\u201d, checked by canopy"
    if req.runner_agent:
        line += (
            f" (it serves {req.runner_agent})"
            if req.runner_serves_agent
            else (f" (it says it speaks for {req.runner_agent}, but that runner does not serve it)")
        )
    return line + "."


def _title(req: AgentTokenRequest) -> str:
    who = req.requested_by.email or req.requested_by.username
    level = dict(token_scopes.SCOPE_CHOICES)[req.scope].lower()
    return f"Let {who} use Connect Labs as you for 1 hour ({level})"


@login_required
@require_http_methods(["GET", "POST"])
def agent_request_page(request, ref):
    """The person's side: who asked, what they would get. Approve asks for their passkey in
    canopy (``full_access``); decline needs none."""
    req = AgentTokenRequest.objects.select_related("requested_by").filter(ref=ref).first()
    if req is None:
        return render_page(request, ref)
    if request.method == "GET":
        return render_page(request, ref)
    action = request.POST.get("action")
    try:
        if action == "approve":
            agent_requests.check_can_ask_passkey(ref, request.user)
            return full_access.begin(
                request,
                full_access.PendingFullAccess.AGENT_REQUEST,
                {"request_ref": ref},
                detail=f"{runner_line(req)} The agent says: {req.reason}"[:300],
                title=_title(req),
            )
        if action == "decline":
            agent_requests.decline(ref, request.user)
            return render_page(request, ref, outcome="declined")
    except agent_requests.AgentRequestError as exc:
        return render_page(
            request, ref, outcome=exc.code, status=409 if exc.code in ("already_answered", "expired") else 403
        )
    return render_page(request, ref, outcome="bad_action", status=400)
