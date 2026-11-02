"""The embed endpoints: the token endpoint, the session start, and the frame."""

from __future__ import annotations

import json
import logging

from django.http import Http404, JsonResponse
from django.shortcuts import render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_GET, require_POST
from django.views.generic import TemplateView

from connect_labs.audit_trail import service as audit
from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.embed import grant, sessions
from connect_labs.embed.framing import allow_framing
from connect_labs.embed.kinds import get_kind
from connect_labs.embed.middleware import embed_enabled
from connect_labs.embed.models import EmbedClient, EmbedSession
from connect_labs.workflow.views import WorkflowRunView

logger = logging.getLogger(__name__)

NO_STORE = {"Cache-Control": "no-store", "Pragma": "no-cache"}


def _json(body: dict, status: int = 200) -> JsonResponse:
    response = JsonResponse(body, status=status)
    for key, value in NO_STORE.items():
        response[key] = value
    return response


def _not_found_when_off():
    if not embed_enabled():
        raise Http404("embedding is switched off")


@csrf_exempt
@require_POST
def token_view(request):
    """RFC 7523 jwt-bearer: a client's signed assertion for a single-use bootstrap token."""
    _not_found_when_off()
    try:
        issued = grant.issue(request.POST, request)
    except grant.EmbedRefused as refused:
        logger.warning("embed token refused: %s (%s)", refused.reason, refused.code)
        audit.record(
            Action.ACCESS_DENIED,
            resource_type="embed_token",
            outcome=Outcome.FAILURE,
            status_code=refused.status,
            metadata={"embed_client": refused.client.client_id if refused.client else "", "reason": refused.reason},
        )
        return _json(refused.body(), refused.status)
    session = issued.session
    audit.record(
        Action.CREATE,
        resource_type="embed_token",
        resource_id=f"{session.kind}:{session.resource_id}",
        user=session.user,
        metadata={"embed_client": session.client.client_id, "embed_session": session.pk},
        **get_kind(session.kind).scope(session.entry),
    )
    return _json(issued.body())


@csrf_exempt
@require_POST
def session_view(request):
    """The frame trades its bootstrap token for a session cookie (sessions.py).

    No ambient authority is used -- the token in the body is the credential -- so there
    is no CSRF to defend; the response sets only the embed's own cookie.
    """
    _not_found_when_off()
    try:
        payload = json.loads(request.body or b"{}")
    except ValueError:
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    try:
        session, raw = sessions.start(
            payload.get("token"),
            str(payload.get("client_id") or ""),
            str(payload.get("kind") or ""),
            str(payload.get("id") or ""),
        )
    except sessions.SessionRefused as refused:
        logger.warning("embed session refused: %s", refused.reason)
        audit.record(
            Action.ACCESS_DENIED,
            resource_type="embed_session",
            outcome=Outcome.FAILURE,
            status_code=400,
            metadata={"embed_client": str(payload.get("client_id") or "")[:64], "reason": refused.reason},
        )
        return _json({"error": "invalid_grant", "error_description": refused.description}, 400)
    audit.record(
        Action.LOGIN,
        resource_type="embed_session",
        resource_id=f"{session.kind}:{session.resource_id}",
        user=session.user,
        metadata={"embed_client": session.client.client_id, "embed_session": session.pk},
    )
    response = _json({"ok": True, "expires_in": session.client.session_seconds})
    sessions.set_session_cookie(response, session, raw)
    return response


def embed_config_for(client, kind, resource_id) -> dict:
    """What the frame's token handling needs (templates/embed/_token.html)."""
    return {
        "client_id": client.client_id,
        "kind": kind,
        "id": str(resource_id),
        "origins": list(client.allowed_origins or []),
        "sessionUrl": reverse("embed:session"),
    }


def embed_config(session) -> dict:
    return embed_config_for(session.client, session.kind, session.resource_id)


@require_GET
def frame_view(request, client_id, kind, resource_id):
    """The frame: the resource, drawn by its kind, for a live session -- else the bootstrap
    page that takes a token from its parent. Framable by this client's origins only."""
    _not_found_when_off()
    client = EmbedClient.objects.filter(client_id=client_id, active=True).first()
    embed_kind = get_kind(kind)
    if client is None or embed_kind is None or client.resource_entry(kind, resource_id) is None:
        raise Http404("no such embed")
    session = getattr(request, "labs_embed", None)
    if (
        session is not None
        and (session.client_id, session.kind, session.resource_id) == (client.pk, kind, str(resource_id))
        and EmbedSession.objects.filter(pk=session.pk, drawn_at__isnull=True).update(drawn_at=timezone.now()) == 1
    ):
        # Drawn once per session: a second load of the frame -- a reload, or the embedder's
        # page opened again by someone else -- asks its parent for a new token instead.
        response = embed_kind.render(request, session)
        if hasattr(response, "render") and callable(response.render):
            response = response.render()
    else:
        response = render(
            request,
            "embed/bootstrap.html",
            {"embed_config": embed_config_for(client, kind, resource_id)},
        )
    allow_framing(response, client.allowed_origins)
    for key, value in NO_STORE.items():
        response[key] = value
    response["Referrer-Policy"] = "same-origin"
    return response


# --- the workflow kind's view -------------------------------------------------------------


def frame_reach(request, definition_id: int, scope: dict) -> tuple[int | None, dict]:
    """What the frame drawn for this definition may read, recorded on its session.

    The middleware binds every id a sandbox read route names to this
    (``kinds.WorkflowKind.allowed``): the workflow's runs, the definitions it declares it
    reads (``declared_reads``), and the opportunities and programmes it covers. Returns the
    newest run in the resource's scope (an embed never creates one) and that record.
    """
    from connect_labs.workflow.data_access import WorkflowDataAccess
    from connect_labs.workflow.declared_reads import readable_definition_ids

    access = WorkflowDataAccess(request=request)
    try:
        definition = access.get_definition(definition_id)
        runs = access.list_runs(definition_id)
    finally:
        access.close()
    opportunity_id = scope.get("opportunity_id")
    shown = [r for r in runs if not opportunity_id or getattr(r, "opportunity_id", None) in (None, opportunity_id)]
    opps = {scope.get("opportunity_id"), getattr(definition, "opportunity_id", None)}
    opps |= set(getattr(definition, "opportunity_ids", None) or [])
    programs = {scope.get("program_id"), getattr(definition, "program_id", None)}
    reach = {
        "runs": sorted({r.id for r in runs}),
        "readable": sorted(readable_definition_ids(definition) | {definition_id}) if definition else [definition_id],
        "opps": sorted(int(o) for o in opps if o),
        "programs": sorted(int(p) for p in programs if p),
    }
    return max((r.id for r in shown), default=None), reach


class EmbedWorkflowView(WorkflowRunView):
    """The workflow runner without chrome, as the embed session's person.

    The same page data as the workflow's own page (``WorkflowRunView``), the same runner and
    sandbox. It shows the newest run (a page: none), records it on the session so the
    middleware allows that run's reads, and leaves out the agent panel and every editing
    affordance.
    """

    template_name = "embed/workflow.html"
    no_runs = False

    def get(self, request, *args, **kwargs):
        session = request.labs_embed
        definition_id = int(session.resource_id)
        self.kwargs["definition_id"] = definition_id
        params = request.GET.copy()
        for name in ("run_id", "edit", "template_draft"):
            params.pop(name, None)
        is_page = self._is_page_definition(definition_id)
        run_id, reach = frame_reach(request, definition_id, request.labs_context)
        if is_page:
            self.page_mode = True
        elif is_page is False:
            if run_id is None:
                self.no_runs = True
            else:
                params["run_id"] = str(run_id)
        state = {**(session.state or {}), **reach, "run_id": run_id if is_page is False else None}
        if state != session.state:
            session.state = state
            session.save(update_fields=["state"])
        request.GET = params
        if self.no_runs:
            return self.render_to_response(
                {"no_runs": True, "present_mode": True, "embed_config": embed_config(session)}
            )
        # Not WorkflowRunView.get: its redirects re-scope a bare workflow URL, which this is not.
        return TemplateView.get(self, request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        for key in ("canopy_panel", "agent_settings", "agent_held_back"):
            context.pop(key, None)
        # Read-only: the workflow's actions would only be refused (the session reaches no
        # write), so their buttons are not offered.
        workflow_data = context.get("workflow_data")
        if isinstance(workflow_data, dict):
            workflow_data["actions"] = []
        context["present_mode"] = True
        context["chat_widget_enabled"] = False
        context["embed_config"] = embed_config(self.request.labs_embed)
        return context
