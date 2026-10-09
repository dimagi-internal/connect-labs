"""Pages at addresses that name their scope.

    /labs/p/org/<slug>/[<page>/]
    /labs/p/programme/<id>/[<page>/]
    /labs/p/opportunity/<id>/[<page>/]

With no page slug, the scope's `home` (the `labs` namespace in Settings) -- which is
what an organisation coming in lands on. With one, the page definition owned by that
scope whose `page.slug` it is. The scope is checked FIRST: someone not in it is
refused before any definition is read, so a link cannot pull a page out of a scope
the viewer is not in.

A page renders in page mode (page_mode.py): no run, the scope as its `scope` prop.
Its definition is read where it lives -- the `home` fill names the owner scope, which
for a labs-only organisation (keyed by slug, no Connect id to own records with) is
one of its programmes.
"""

from __future__ import annotations

import logging

from django.contrib.auth.decorators import login_required
from django.http import Http404, HttpResponseRedirect
from django.shortcuts import render
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.context import get_org_data, save_context_to_session
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope, organization_entry
from connect_labs.workflow.page_mode import is_page
from connect_labs.workflow.views import WorkflowRunView

logger = logging.getLogger(__name__)

# The address says "programme" and "org"; the scope model says "program" and "organization".
SCOPE_TYPES = {"org": "organization", "programme": "program", "opportunity": "opportunity"}
ADDRESS = {v: k for k, v in SCOPE_TYPES.items()}


def page_url(scope: Scope, slug: str | None = None) -> str:
    return f"/labs/p/{ADDRESS[scope.type]}/{scope.key}/" + (f"{slug}/" if slug else "")


def _scope(kwargs) -> Scope:
    try:
        return Scope.of(SCOPE_TYPES[kwargs["scope_type"]], kwargs["scope_key"])
    except (KeyError, ValueError) as exc:
        raise Http404(str(exc)) from None


def _owner_context(request, owner: dict) -> dict | None:
    """The labs context to read a definition owned by `owner` in, or None if it cannot be."""
    if owner.get("opportunity_id"):
        return {"opportunity_id": int(owner["opportunity_id"])}
    if owner.get("program_id"):
        return {"program_id": int(owner["program_id"])}
    org = owner.get("organization_id")
    if org not in (None, ""):
        if str(org).isdigit():
            return {"organization_id": int(org)}
        entry = organization_entry(get_org_data(request) or {}, org) or {}
        if isinstance(entry.get("id"), int):
            return {"organization_id": entry["id"], "organization_slug": org}
    return None


def _scope_owner(scope: Scope, request) -> dict:
    if scope.type == "organization":
        return {"organization_id": scope.key}
    return scope.kwargs()


def find_page(request, scope: Scope, slug: str):
    """(definition, owner) for the page `slug` owned by `scope`, or (None, None)."""
    from connect_labs.workflow.data_access import WorkflowDataAccess

    owner = _scope_owner(scope, request)
    context = _owner_context(request, owner)
    if context is None:
        return None, None
    access = WorkflowDataAccess(request=request, **{k: v for k, v in context.items() if k.endswith("_id")})
    try:
        for definition in access.list_definitions():
            if is_page(definition) and ((definition.data or {}).get("page") or {}).get("slug") == slug:
                return definition, owner
    finally:
        access.close()
    return None, None


class PageView(WorkflowRunView):
    template_name = "workflow/page.html"

    def get(self, request, *args, **kwargs):
        scope = _scope(kwargs)
        caller = Caller(user=request.user, request=request)
        if service.refusal(caller, scope):
            # Same as any labs page for a scope you are not in.
            raise Http404("not a scope you can use")
        slug = kwargs.get("page")
        if slug:
            definition, owner = find_page(request, scope, slug)
            if definition is None:
                raise Http404(f"no page {slug!r} here")
            definition_id = definition.id
        else:
            home = (service.resolved_for_request(request, "labs", scope).get("home") or {}).get("fill")
            if not home:
                return render(
                    request,
                    "workflow/page_none.html",
                    {"scope_label": scope.label(get_org_data(request) or {}), "settings_url": _settings_url(scope)},
                )
            definition_id = int(home["workflow"])
            owner = {k: home[k] for k in ("organization_id", "program_id", "opportunity_id") if home.get(k)}
        context = _owner_context(request, owner)
        if context is None:
            raise Http404("this page's owner cannot be read here")
        # Opening a page puts you in its scope, as any labs page naming a scope does.
        save_context_to_session(
            request,
            {"organization_id": scope.key} if scope.type == "organization" else scope.kwargs(),
        )
        request.labs_context = context
        self.page_mode = True
        self.page_scope = scope
        self.page_owner = owner
        self.kwargs["definition_id"] = definition_id
        # Not WorkflowRunView.get: its redirects re-scope a bare workflow URL, which this is not.
        return TemplateView.get(self, request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = self.page_scope
        tree = get_org_data(self.request) or {}
        context["page_scope_label"] = scope.label(tree)[0].upper() + scope.label(tree)[1:]
        context["page_settings_url"] = _settings_url(scope)
        definition = context.get("definition")
        user = self.request.user
        author = (getattr(definition, "data", None) or {}).get("username") if definition else None
        if definition and (user.is_staff or user.username == author):
            owner = "&".join(f"{k}={v}" for k, v in self.page_owner.items())
            context["page_edit_url"] = f"/labs/workflow/{definition.id}/run/?{owner}&edit=true"
        # An organisation-owned page's data calls name the organisation, so they are read
        # where the page lives whatever the session holds by then.
        org = self.page_owner.get("organization_id")
        endpoints = (context.get("workflow_data") or {}).get("apiEndpoints") or {}
        if org and not (self.page_owner.get("program_id") or self.page_owner.get("opportunity_id")):
            for key, url in list(endpoints.items()):
                if isinstance(url, str) and url.startswith("/labs/workflow/api/"):
                    endpoints[key] = url + ("&" if "?" in url else "?") + f"organization_id={org}"
        return context


def _settings_url(scope: Scope) -> str:
    return f"/labs/settings/{scope.type}/{scope.key}/"


@login_required
def old_page(request, slug):
    """`/labs/p/<slug>/`, an address from before pages named their scope.

    The old card pages it served are gone (the scope-config spec, section 8). If the
    scope in view -- or its programme, for an opportunity -- has a page with that
    slug, go there; otherwise say what happened rather than 404 a link someone was sent.
    """
    from connect_labs.scope_config.service import scope_in_view

    scope = scope_in_view(getattr(request, "labs_context", None) or {})
    searched = []
    while scope is not None and scope not in searched:
        searched.append(scope)
        if not service.refusal(Caller(user=request.user, request=request), scope):
            definition, _ = find_page(request, scope, slug)
            if definition is not None:
                return HttpResponseRedirect(page_url(scope, slug))
        program = (request.labs_context or {}).get("program_id") if scope.type == "opportunity" else None
        scope = Scope.of("program", program) if program else None
    return render(
        request,
        "workflow/page_moved.html",
        {"slug": slug, "searched": [s.label(get_org_data(request) or {}) for s in searched]},
    )
