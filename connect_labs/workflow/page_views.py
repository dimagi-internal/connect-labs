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

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseBadRequest, HttpResponseRedirect
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.generic import TemplateView, View

from connect_labs.labs.access.scopes import Caller
from connect_labs.labs.context import get_org_data, save_context_to_session
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope, name_of, opportunity_entry, organization_entry, program_entry
from connect_labs.workflow.page_mode import is_page, page_edit_refusal
from connect_labs.workflow.views import WorkflowRunView

logger = logging.getLogger(__name__)

# The address says "programme" and "org"; the scope model says "program" and "organization".
SCOPE_TYPES = {"org": "organization", "programme": "program", "opportunity": "opportunity"}
ADDRESS = {v: k for k, v in SCOPE_TYPES.items()}


def page_url(scope: Scope, slug: str | None = None) -> str:
    return f"/labs/p/{ADDRESS[scope.type]}/{scope.key}/" + (f"{slug}/" if slug else "")


def pages_url(scope: Scope) -> str:
    """The list of `scope`'s pages (PagesView)."""
    return page_url(scope, "pages")


def _scope(kwargs) -> Scope:
    try:
        return Scope.of(SCOPE_TYPES[kwargs["scope_type"]], kwargs["scope_key"])
    except (KeyError, ValueError) as exc:
        raise Http404(str(exc)) from None


def _owner_context(request, owner: dict) -> dict | None:
    """The labs context to read a definition owned by `owner` in, or None if the viewer may not.

    The owner comes from Settings or a page's address, which someone else wrote, so it
    is validated exactly as the middleware validates a context named in a URL
    (`validate_context_access`): a labs-only scope the viewer cannot use is refused
    here, and a real one is passed on for Connect to enforce on the read. An
    organisation is named by slug and read by its Connect id; a labs-only
    organisation has none, so it owns no records.
    """
    from connect_labs.labs.context import validate_context_access

    if owner.get("opportunity_id"):
        wanted = {"opportunity_id": int(owner["opportunity_id"])}
    elif owner.get("program_id"):
        wanted = {"program_id": int(owner["program_id"])}
    elif owner.get("organization_id") not in (None, "") and not str(owner["organization_id"]).isdigit():
        wanted = {"organization_id": str(owner["organization_id"])}
    else:
        return None
    validated = validate_context_access(request, wanted)
    key = next(iter(wanted))
    if key == "organization_id":
        if not isinstance(validated.get("organization_id"), int):
            return None
        return {"organization_id": validated["organization_id"], "organization_slug": wanted[key]}
    if validated.get(key) != wanted[key]:
        return None
    return {key: wanted[key]}


def resolve_home(request, scope: Scope):
    """(definition_id, owner, owner context) for `scope`'s home page, or None if it has none it can open."""
    home = (service.resolved_for_request(request, "labs", scope).get("home") or {}).get("fill")
    if not home:
        return None
    owner = {k: home[k] for k in ("organization_id", "program_id", "opportunity_id") if home.get(k)}
    context = _owner_context(request, owner)
    if context is None:
        return None
    return int(home["workflow"]), owner, context


def _scope_owner(scope: Scope, request) -> dict:
    if scope.type == "organization":
        return {"organization_id": scope.key}
    return scope.kwargs()


def _with_scope_named(request, context: dict, scope: Scope) -> dict:
    """`context` plus the display entry of the scope in view, for the header's context picker.

    The context a page is read in holds only ids (and, for a home owned by one of an
    organisation's programmes, not even the organisation's), so the picker had nothing
    to name and showed "Select Context". Only the display object is added -- never an
    id -- so what the page reads, and where, is unchanged.
    """
    tree = get_org_data(request) or {}
    key, find = {
        "organization": ("organization", organization_entry),
        "program": ("program", program_entry),
        "opportunity": ("opportunity", opportunity_entry),
    }[scope.type]
    entry = find(tree, scope.key)
    return {**context, key: entry} if entry else context


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
                # Nothing is read here, so the picker names the scope in view and nothing older.
                request.labs_context = _with_scope_named(request, {}, scope)
                return render(
                    request,
                    "workflow/page_none.html",
                    {
                        "scope_label": scope.label(get_org_data(request) or {}),
                        "settings_url": _settings_url(scope),
                        "pages_url": pages_url(scope),
                    },
                )
            definition_id = int(home["workflow"])
            owner = {k: home[k] for k in ("organization_id", "program_id", "opportunity_id") if home.get(k)}
        # The owner is validated before anything is read in it: a 404 for a scope the
        # viewer may not use, the same as naming that scope in a URL would give.
        context = _owner_context(request, owner)
        if context is None:
            raise Http404("this page's owner cannot be read here")
        # Opening a page puts you in its scope, as any labs page naming a scope does.
        save_context_to_session(
            request,
            {"organization_id": scope.key} if scope.type == "organization" else scope.kwargs(),
        )
        request.labs_context = _with_scope_named(request, context, scope)
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
        if definition is not None and not is_page(definition):
            # A home set to a workflow with runs (Settings refuses one, but a definition's
            # kind can change after it was chosen): not drawn here, whatever it holds.
            for key in ("workflow_data", "render_code", "canopy_panel"):
                context.pop(key, None)
            context["error"] = (
                "This scope's home is set to a workflow that is not a page, so it is not shown here. "
                "Choose a page for it in Settings."
            )
            context["not_a_page"] = True
            return context
        # Whoever may change the page's owning scope (page_mode.page_edit_refusal) -- the
        # same rule the edit view and the render-code save enforce, so the link is
        # never offered to someone the server would refuse.
        if definition and page_edit_refusal(self.request, definition, self.page_owner) is None:
            owner = "&".join(f"{k}={v}" for k, v in self.page_owner.items())
            context["page_edit_url"] = f"/labs/workflow/{definition.id}/run/?{owner}&edit=true"
        if definition:
            self._add_page_manager_links(context, definition, tree)
        # An organisation-owned page's data calls name the organisation, so they are read
        # where the page lives whatever the session holds by then.
        org = self.page_owner.get("organization_id")
        endpoints = (context.get("workflow_data") or {}).get("apiEndpoints") or {}
        # (The owner is a slug: the middleware turns `?organization_id=<slug>` into its id.)
        if org and not (self.page_owner.get("program_id") or self.page_owner.get("opportunity_id")):
            for key, url in list(endpoints.items()):
                if isinstance(url, str) and url.startswith("/labs/workflow/api/"):
                    endpoints[key] = url + ("&" if "?" in url else "?") + f"organization_id={org}"
        return context

    def _add_page_manager_links(self, context, definition, tree) -> None:
        """For whoever may change the scope in view: the list of its pages, and "Make this the home page".

        Host chrome, outside the page's frame. The offer follows the server's rule
        (PagesView.post re-checks it): someone who may not change the scope is offered neither.
        """
        from connect_labs.workflow import page_manager

        scope = self.page_scope
        caller = Caller(user=self.request.user, request=self.request)
        if service.write_refusal(caller, scope, service.tree_for(caller)):
            return
        context["page_manager_url"] = pages_url(scope)
        owner = page_manager.owner_from_token(
            self.request, scope, _owner_token(self.page_owner), service.tree_for(caller)
        )
        home = (service.resolved_for_request(self.request, "labs", scope).get("home") or {}).get("fill") or {}
        is_home = int(home.get("workflow") or 0) == definition.id and _owner_token(home) == _owner_token(
            self.page_owner
        )
        context["page_is_home"] = is_home
        if owner is not None and not is_home:
            context["page_make_home"] = {"workflow": definition.id, "owner": owner.token}


def _owner_token(owner: dict) -> str:
    """ "program:25" for an owner or fill dict ({"program_id": 25})."""
    from connect_labs.scope_config.builtin import fill_owner

    found = fill_owner(owner or {})
    return f"{found.type}:{found.key}" if found else ""


class PagesView(LoginRequiredMixin, View):
    """`/labs/p/<scope>/<key>/pages/`: the scope's pages, a new one, and which one it opens on.

    Anyone who can use the scope sees the list; whoever may change the scope (Settings'
    write rule, Dimagi staff included) makes pages and chooses the home. Every POST is
    re-checked here and in the scope config service, whatever the page offered.
    """

    def _scope(self, kwargs) -> Scope:
        scope = _scope(kwargs)
        if service.refusal(Caller(user=self.request.user, request=self.request), scope):
            raise Http404("not a scope you can use")
        return scope

    def get(self, request, *args, **kwargs):
        from connect_labs.workflow import page_manager

        scope = self._scope(kwargs)
        listing = page_manager.list_pages(request, scope)
        request.labs_context = _with_scope_named(request, getattr(request, "labs_context", None) or {}, scope)
        listing["write_note"] = (listing["write_note"] or "")[:1].upper() + (listing["write_note"] or "")[1:]
        return render(
            request,
            "workflow/page_manager.html",
            {
                **listing,
                "scope_label": listing["scope_label"][:1].upper() + listing["scope_label"][1:],
                "scope_name": name_of(scope, get_org_data(request) or {}) or scope.key,
                "settings_url": _settings_url(scope),
                "home_page": next((p for p in listing["pages"] if p["is_home"]), None),
            },
        )

    def post(self, request, *args, **kwargs):
        from connect_labs.workflow import page_manager

        scope = self._scope(kwargs)
        action = request.POST.get("action", "")
        tree = service.tree_for(Caller(user=request.user, request=request))
        here = pages_url(scope)
        try:
            if action == "create":
                owner = page_manager.owner_from_token(request, scope, request.POST.get("owner", ""), tree)
                if owner is None:
                    raise service.Invalid("choose where the page is kept")
                definition_id, owner = page_manager.create_page(
                    request, scope, owner, request.POST.get("name", ""), request.POST.get("slug", "").strip()
                )
                if request.POST.get("make_home"):
                    page_manager.set_home(request, scope, owner, definition_id, label=request.POST.get("name", ""))
                # Straight into edit mode on the new page.
                return HttpResponseRedirect(f"/labs/workflow/{definition_id}/run/?{owner.query}&edit=true")
            if action == "make_home":
                owner = page_manager.owner_from_token(request, scope, request.POST.get("owner", ""), tree)
                try:
                    definition_id = int(request.POST.get("workflow", ""))
                except ValueError:
                    return HttpResponseBadRequest("workflow must be a whole number")
                if owner is None:
                    raise service.Invalid("that page is not one of this scope's")
                page_manager.set_home(request, scope, owner, definition_id, label=request.POST.get("label", ""))
                messages.success(request, "Home page set.")
            elif action == "clear_home":
                page_manager.set_home(request, scope, None, None)
                messages.success(request, "Home page cleared.")
            else:
                return HttpResponseBadRequest(f"unknown action {action!r}")
        except service.ConfigError as exc:
            messages.error(request, str(exc)[:1].upper() + str(exc)[1:])
        target = request.POST.get("next") or here
        if not url_has_allowed_host_and_scheme(target, allowed_hosts={request.get_host()}, require_https=False):
            target = here
        return HttpResponseRedirect(target)


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
