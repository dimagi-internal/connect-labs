"""Simple Microplan: a browser-side creator for quick test microplans.

Boundary -> work areas (auto-grid, hand-drawn, or drawn with snapping) -> groups
-> the same CSV the standalone grid generator wrote. All the geometry runs in
the browser (`static/microplans/simple_microplan.js`); these views only list the
saved plans and store each one's state as a `SimpleMicroplan` row.

Everyone who can see Workflows can open the creator. A plan records the labs
context (organisation / program / opportunity) picked in the context picker when
it was made, and the list shows the plans of the context picked now -- the most
specific of the three wins; with nothing picked, the caller's own plans. Who may
open and change a plan follows the same Connect tree the picker offers
(``ContextAccess``): its creator, Dimagi staff, and anyone who can pick the plan's
most specific context. A plan with no context (made with nothing picked, or before
contexts existed) is its creator's and Dimagi staff's only. Only the creator or
Dimagi staff may delete. A plan someone may not see is a 404; a delete they may
not make is a 403.

**In a workspace program** (``labs.workspaces``) the same views serve
`/microplans/program/<id>/simple/` with the workspace's rules instead: a Program
Manager sees, changes and deletes every plan there; a Network Manager sees and
changes only their teams' plans (anything else is a 404) and may not delete
(403, on the audit trail). Those plans carry ``program_id`` + ``team`` and never
appear in the global list, which shows only plans with no program. ``_Scope``
holds the difference, so each view reads its plans through ``self.scope``.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

import markdown
import nh3
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from connect_labs.microplans.models import SimpleMicroplan
from connect_labs.microplans.views import _ProgramScopeMixin
from connect_labs.utils.dimagi_user import is_dimagi_staff
from connect_labs.utils.feature_access import user_has_feature_access

logger = logging.getLogger(__name__)

# The creator is offered beside Workflows on the overview, to the same people.
FEATURE_KEY = "workflow"
MAX_NAME_LENGTH = 200
DATA_KEYS = ("boundary", "rows", "settings", "csv_name")
# The editor's help popup IS the help-site page, so the per-merge docs updater
# (automation/update_docs.py) keeps both current from one file.
HELP_DOC = settings.BASE_DIR / "user_docs" / "simple-microplan.md"
HELP_SITE_URL = "/labs/docs/help/simple-microplan/"


def _slug(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")


def help_sections() -> list[dict]:
    """The help guide as one article per ``## `` section: id, title, html, plain text.

    The text before the first section becomes an "About" article. Rendered
    output is sanitised like every other markdown labs shows.
    """
    try:
        source = HELP_DOC.read_text(encoding="utf-8")
    except OSError:
        return []
    sections, title, lines = [], "About", []

    def flush():
        body = "\n".join(lines).strip().strip("-").strip()
        if body.startswith("# "):  # the page title
            body = body.split("\n", 1)[1].strip() if "\n" in body else ""
        if not body:
            return
        html = nh3.clean(markdown.markdown(body, extensions=["tables", "fenced_code", "sane_lists"]))
        text = re.sub(r"\s+", " ", nh3.clean(html, tags=set())).strip()
        sections.append({"id": _slug(title), "title": title, "html": html, "text": text})

    for line in source.splitlines():
        if line.startswith("## "):
            flush()
            title, lines = line[3:].strip(), []
        else:
            lines.append(line)
    flush()
    return sections


def _display_name(user) -> str:
    return (getattr(user, "name", "") or getattr(user, "username", "") or getattr(user, "email", "") or "").strip()


def is_creator_or_staff(user, plan: SimpleMicroplan) -> bool:
    """Who may delete a global plan: its creator, or Dimagi staff."""
    return (plan.created_by_id is not None and plan.created_by_id == user.pk) or is_dimagi_staff(user)


def _ids(rows) -> frozenset[int]:
    out = set()
    for row in rows or []:
        try:
            out.add(int(row.get("id")))
        except (AttributeError, TypeError, ValueError):
            continue
    return frozenset(out)


@dataclass(frozen=True)
class ContextAccess:
    """What a person may do with global plans, from the Connect organisations /
    programs / opportunities tree the labs context picker offers them
    (``labs.context.get_org_data``) -- never from the URL or the session's pick,
    which ``validate_context_access`` passes through unchecked when the cached
    tree does not hold it."""

    staff: bool = False
    org_slugs: frozenset = frozenset()
    program_ids: frozenset = frozenset()
    opportunity_ids: frozenset = frozenset()

    @classmethod
    def for_request(cls, request) -> ContextAccess:
        from connect_labs.labs.context import get_org_data

        data = get_org_data(request) or {}
        return cls(
            staff=is_dimagi_staff(request.user),
            org_slugs=frozenset(o["slug"] for o in data.get("organizations") or [] if o.get("slug")),
            program_ids=_ids(data.get("programs")),
            opportunity_ids=_ids(data.get("opportunities")),
        )

    def visible(self, plans, user):
        """The plans this person may open: their own, and those whose most specific
        context (opportunity, else program, else organisation) they can pick.
        A plan with no context matches none of those, so it stays its creator's."""
        if self.staff:
            return plans
        return plans.filter(
            Q(created_by_id=user.pk)
            | Q(opportunity_id__in=self.opportunity_ids)
            | Q(opportunity_id__isnull=True, context_program_id__in=self.program_ids)
            | Q(opportunity_id__isnull=True, context_program_id__isnull=True, organization_slug__in=self.org_slugs)
        )

    def can_open(self, user, plan) -> bool:
        """``visible`` for one plan. Opening a plan is changing it too."""
        if self.staff or (plan.created_by_id is not None and plan.created_by_id == user.pk):
            return True
        if plan.opportunity_id is not None:
            return plan.opportunity_id in self.opportunity_ids
        if plan.context_program_id is not None:
            return plan.context_program_id in self.program_ids
        return bool(plan.organization_slug) and plan.organization_slug in self.org_slugs

    def fields_for_new_plan(self, labs_context) -> dict:
        """The picked organisation, program and opportunity a new plan records --
        each only when this person can pick it (Dimagi staff: any). Something they
        cannot is left off rather than trusted from the URL."""
        ctx = labs_context or {}
        fields = {}
        slug = ctx.get("organization_slug")
        if slug and (self.staff or slug in self.org_slugs):
            fields["organization_slug"] = str(slug)[:255]
            fields["organization_name"] = str((ctx.get("organization") or {}).get("name") or slug)[:255]
        program_id = ctx.get("program_id")
        if program_id and (self.staff or program_id in self.program_ids):
            fields["context_program_id"] = int(program_id)
            fields["program_name"] = str((ctx.get("program") or {}).get("name") or "")[:255]
        opportunity_id = ctx.get("opportunity_id")
        if opportunity_id and (self.staff or opportunity_id in self.opportunity_ids):
            fields["opportunity_id"] = int(opportunity_id)
            fields["opportunity_name"] = str((ctx.get("opportunity") or {}).get("name") or "")[:255]
        return fields


def filter_by_picked_context(plans, labs_context, user):
    """The global list for what the context picker has picked: the most specific of
    opportunity, program, organisation wins; with nothing picked, the caller's own."""
    ctx = labs_context or {}
    if ctx.get("opportunity_id"):
        return plans.filter(opportunity_id=ctx["opportunity_id"])
    if ctx.get("program_id"):
        return plans.filter(context_program_id=ctx["program_id"])
    if ctx.get("organization_slug"):
        return plans.filter(organization_slug=ctx["organization_slug"])
    return plans.filter(created_by_id=user.pk)


def team_for_new_plan(role, requested=None) -> str:
    """The team a plan made in a workspace program belongs to.

    The rule ``ProgramPlanDataAccess.team_for_new_records`` applies to program
    plans: a Network Manager's is one of their teams (the one asked for, else
    their first); a Program Manager's is whichever they ask for ("" = Program
    Managers only)."""
    requested = (requested or "").strip()[:200]
    if role.is_program_manager:
        return requested
    return requested if requested in role.teams else role.teams[0]


@dataclass(frozen=True)
class _Scope:
    """Where a request's simple plans live and what the caller may do with them.

    ``program_id`` None: the global plans (no program), under ``access`` -- the
    caller's ``ContextAccess``. Otherwise a workspace program and the caller's role
    in it."""

    program_id: int | None = None
    workspace: object | None = None
    role: object | None = None  # labs.workspaces.WorkspaceRole
    user: object | None = None
    access: ContextAccess | None = None  # global plans only

    @property
    def in_workspace(self) -> bool:
        return self.program_id is not None

    def plans(self):
        """The plans this caller may see here. Every lookup goes through it, so a
        plan of a context they cannot pick, another team's plan, or one from
        another program is simply not found."""
        if not self.in_workspace:
            return self.access.visible(SimpleMicroplan.objects.filter(program_id__isnull=True), self.user)
        plans = SimpleMicroplan.objects.filter(program_id=self.program_id)
        if not self.role.is_program_manager:
            plans = plans.filter(team__in=self.role.teams)
        return plans

    def can_edit(self, user, plan) -> bool:
        if not self.in_workspace:
            return self.access.can_open(user, plan)
        # Team members work on their team's plans together.
        return self.role.is_program_manager or plan.team in self.role.teams

    def can_delete(self, user, plan) -> bool:
        if not self.in_workspace:
            return is_creator_or_staff(user, plan)
        # As for program plans (``ProgramPlanDataAccess._guard_delete``).
        return self.role.is_program_manager

    def url(self, name: str, **kwargs) -> str:
        if not self.in_workspace:
            return reverse(f"microplans:simple_{name}", kwargs=kwargs)
        return reverse(f"microplans:program_simple_{name}", kwargs={"program_id": self.program_id, **kwargs})

    def new_plan_fields(self, request) -> dict:
        if not self.in_workspace:
            return self.access.fields_for_new_plan(getattr(request, "labs_context", None))
        return {"program_id": self.program_id, "team": team_for_new_plan(self.role, request.POST.get("team"))}


def _resolve_scope(request, program_id) -> _Scope:
    """The caller's scope, or Http404.

    Simple Microplan files plans under a program only in a workspace program;
    any other program id is not a page. Someone with no role there was already
    refused by the program access check (in a workspace, access IS a role) --
    this does not rely on it."""
    user = request.user
    if not user_has_feature_access(user, FEATURE_KEY):
        raise Http404
    if program_id is None:
        return _Scope(user=user, access=ContextAccess.for_request(request))
    from connect_labs.labs.workspaces import role_in, workspace_for_program

    workspace = workspace_for_program(program_id)
    role = role_in(user, workspace) if workspace is not None else None
    if role is None:
        raise Http404
    return _Scope(program_id=int(program_id), workspace=workspace, role=role)


class _SimpleMicroplanAccessMixin(_ProgramScopeMixin, LoginRequiredMixin):
    """Who may open Simple Microplan here; sets ``self.scope``.

    On a workspace route ``_ProgramScopeMixin`` first runs the labs-only program
    access check every program route gets (a test walks the URLconf for it); on
    the global routes there is no program and it passes straight through.
    """

    def _refuse_other_teams_record(self, request, kwargs):
        # The program mixin's own team check looks ``plan_id`` up among the
        # program's LabsRecord plans, which a simple plan's id is not. Simple plans
        # are scoped by ``self.scope.plans()`` instead, resolved here: after the
        # access check, before the context picker follows the URL.
        self.scope = _resolve_scope(request, kwargs.get("program_id"))
        return None


def _clean_name(raw) -> str:
    return str(raw or "").strip()[:MAX_NAME_LENGTH]


def _picked_context_label(labs_context) -> str:
    """What the global list is showing, in words: the picked context that filters it."""
    ctx = labs_context or {}
    if ctx.get("opportunity_id"):
        name = (ctx.get("opportunity") or {}).get("name") or f"Opportunity #{ctx['opportunity_id']}"
        return f"opportunity {name}"
    if ctx.get("program_id"):
        name = (ctx.get("program") or {}).get("name") or f"Program #{ctx['program_id']}"
        return f"program {name}"
    if ctx.get("organization_slug"):
        name = (ctx.get("organization") or {}).get("name") or ctx["organization_slug"]
        return f"organization {name}"
    return ""


def _workspace_page_context(scope: _Scope, user, plans) -> dict:
    """The workspace part of the list page: the caller's role, the teams a new
    plan may be filed under, and team names for the list. Empty for the global list."""
    if not scope.in_workspace:
        return {}
    from connect_labs.labs.models import WorkspaceUser
    from connect_labs.labs.workspaces import can_manage_access, fallback_role, team_choices

    teams = team_choices(scope.workspace, scope.role, user=user)
    if scope.role.is_program_manager:
        # Teams already on a plan, or named by a Users entry, that are not (or no
        # longer) LLOs.
        known = {t["slug"] for t in teams}
        others = {p.team for p in plans} | set(
            scope.workspace.users.filter(role=WorkspaceUser.NETWORK_MANAGER).values_list("team", flat=True)
        )
        teams += [{"slug": t, "name": t} for t in sorted(others - known) if t]
    pid = scope.program_id
    # Only a Dimagi user manages access (labs.workspaces.can_manage_access): the
    # access page, and switching themselves back from a Network Manager entry --
    # only when, without it, they would be a Program Manager (and so could set it
    # again). Anyone else's entry is simply their role: no banner, no switch.
    manages = can_manage_access(user)
    testing = bool(scope.role.testing and manages)
    usual = fallback_role(user, scope.workspace) if testing else None
    return {
        "workspace": {
            "program_id": pid,
            "role_label": scope.role.label,
            "is_program_manager": scope.role.is_program_manager,
            "can_manage_access": bool(manages and scope.role.is_program_manager),
            "teams": teams,
            "team_names": {t["slug"]: t["name"] for t in teams},
            # A Dimagi user made a Network Manager by an explicit Users entry.
            "testing": testing,
            "can_switch_back": bool(usual is not None and usual.is_program_manager),
            "stop_testing_url": reverse("microplans:program_workspace_stop_testing", args=[pid]),
            "settings_url": reverse("microplans:program_workspace_settings", args=[pid]),
        }
    }


class SimpleMicroplanListView(_SimpleMicroplanAccessMixin, TemplateView):
    """The saved simple microplans the caller may see -- in the global list, those
    of the context picked now -- newest change first, and the form to start one."""

    template_name = "microplans/simple_list.html"
    sync_labs_context = True  # workspace routes: the context picker follows the program

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope, user = self.scope, self.request.user
        plans = scope.plans()
        if not scope.in_workspace:
            picked = getattr(self.request, "labs_context", None) or {}
            plans = filter_by_picked_context(plans, picked, user)
            context["picked_context"] = _picked_context_label(picked)
        plans = list(plans.defer("data"))
        context.update(_workspace_page_context(scope, user, plans))
        names = context["workspace"]["team_names"] if scope.in_workspace else {}
        for plan in plans:
            plan.editable = scope.can_edit(user, plan)
            plan.deletable = scope.can_delete(user, plan)
            plan.editor_url = scope.url("editor", plan_id=plan.pk)
            plan.delete_url = scope.url("delete", plan_id=plan.pk)
            plan.team_name = names.get(plan.team) or plan.team
        context["plans"] = plans
        return context

    def post(self, request, *args, **kwargs):
        name = _clean_name(request.POST.get("name"))
        if not name:
            context = self.get_context_data(name_error="Give the microplan a name.")
            return self.render_to_response(context, status=400)
        plan = SimpleMicroplan.objects.create(
            name=name,
            created_by=request.user,
            created_by_name=_display_name(request.user),
            **self.scope.new_plan_fields(request),
        )
        return HttpResponseRedirect(self.scope.url("editor", plan_id=plan.pk))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SimpleMicroplanEditorView(_SimpleMicroplanAccessMixin, TemplateView):
    """The creator itself: a full-screen map page seeded with the plan's saved state."""

    template_name = "microplans/simple_editor.html"
    sync_labs_context = True

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = self.scope
        plan = get_object_or_404(scope.plans(), pk=kwargs["plan_id"])
        context["plan"] = plan
        context["in_workspace"] = scope.in_workspace
        context["list_url"] = scope.url("list")
        context["plan_state"] = {
            "id": plan.pk,
            "name": plan.name,
            "created_by": plan.created_by_name,
            "created_at": plan.created_at.isoformat(),
            "updated_at": plan.updated_at.isoformat(),
            "editable": scope.can_edit(self.request.user, plan),
            "data": plan.data or {},
            "api_url": scope.url("api", plan_id=plan.pk),
            "list_url": context["list_url"],
        }
        sections = help_sections()
        # Article HTML goes into <template> elements, never through JSON-as-text:
        # the popup clones them instead of re-parsing strings as HTML.
        context["help_sections"] = sections
        context["help_index"] = [{k: sec[k] for k in ("id", "title", "text")} for sec in sections]
        context["help_site_url"] = HELP_SITE_URL
        return context


class SimpleMicroplanAPIView(_SimpleMicroplanAccessMixin, View):
    """POST JSON ``{"name": ...}`` to rename, ``{"data": {...}}`` to save, or both."""

    def post(self, request, plan_id, program_id=None):
        plan = get_object_or_404(self.scope.plans(), pk=plan_id)
        if not self.scope.can_edit(request.user, plan):
            return JsonResponse({"error": "Only the person who created this microplan can change it."}, status=403)
        try:
            body = json.loads(request.body or b"{}")
        except (TypeError, ValueError):
            return JsonResponse({"error": "Body must be JSON."}, status=400)
        if not isinstance(body, dict):
            return JsonResponse({"error": "Body must be a JSON object."}, status=400)

        update_fields = ["updated_at"]
        if "name" in body:
            name = _clean_name(body["name"])
            if not name:
                return JsonResponse({"error": "The name cannot be empty."}, status=400)
            plan.name = name
            update_fields.append("name")
        if "data" in body:
            data = body["data"]
            if not isinstance(data, dict) or not isinstance(data.get("rows", []), list):
                return JsonResponse({"error": "data must be an object with a rows list."}, status=400)
            plan.data = {k: data[k] for k in DATA_KEYS if k in data}
            plan.n_work_areas = len(plan.data.get("rows") or [])
            update_fields += ["data", "n_work_areas"]
        plan.save(update_fields=update_fields)
        return JsonResponse({"id": plan.pk, "name": plan.name, "updated_at": plan.updated_at.isoformat()})


class SimpleMicroplanDeleteView(_SimpleMicroplanAccessMixin, View):
    def post(self, request, plan_id, program_id=None):
        scope = self.scope
        plan = get_object_or_404(scope.plans(), pk=plan_id)
        if not scope.can_delete(request.user, plan):
            if not scope.in_workspace:
                raise PermissionDenied("Only the person who created this microplan, or Dimagi staff, can delete it.")
            _record_refused_delete(scope, plan, request.user)
            raise PermissionDenied("Only a Program Manager can delete a microplan in this program.")
        plan.delete()
        return HttpResponseRedirect(scope.url("list"))


def _record_refused_delete(scope: _Scope, plan, user) -> None:
    """Put a Network Manager's refused delete on the audit trail, as
    ``ProgramPlanDataAccess._guard_delete`` does for program plans."""
    from connect_labs.audit_trail.models import Action, Outcome
    from connect_labs.audit_trail.service import record

    logger.warning("Refusing simple microplan delete (program=%s plan=%s)", scope.program_id, plan.pk)
    record(
        Action.ACCESS_DENIED,
        resource_type="simple_microplan",
        resource_id=plan.pk,
        program_id=scope.program_id,
        labs_only=True,
        outcome=Outcome.FAILURE,
        status_code=403,
        metadata={"reason": "workspace delete requires a Program Manager"},
        user=user,
    )
