"""Simple Microplan: a browser-side creator for quick test microplans.

Boundary -> work areas (auto-grid, hand-drawn, or drawn with snapping) -> groups
-> the same CSV the standalone grid generator wrote. All the geometry runs in
the browser (`static/microplans/simple_microplan.js`); these views only list the
saved plans and store each one's state as a `SimpleMicroplan` row.

Everyone who can see Workflows can open the creator and every saved plan. Only
the person who made a plan (or Dimagi staff) may change, rename or delete it;
anyone else opens it read-only and can still export the CSV.
"""

from __future__ import annotations

import json
import re

import markdown
import nh3
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from connect_labs.microplans.models import SimpleMicroplan
from connect_labs.utils.dimagi_user import is_dimagi_staff
from connect_labs.utils.feature_access import user_has_feature_access

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


def can_edit(user, plan: SimpleMicroplan) -> bool:
    return (plan.created_by_id is not None and plan.created_by_id == user.pk) or is_dimagi_staff(user)


class _SimpleMicroplanAccessMixin(LoginRequiredMixin):
    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and not user_has_feature_access(request.user, FEATURE_KEY):
            raise Http404
        return super().dispatch(request, *args, **kwargs)


def _clean_name(raw) -> str:
    return str(raw or "").strip()[:MAX_NAME_LENGTH]


class SimpleMicroplanListView(_SimpleMicroplanAccessMixin, TemplateView):
    """Every saved simple microplan, newest change first, and the form to start one."""

    template_name = "microplans/simple_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plans = list(SimpleMicroplan.objects.defer("data"))
        for plan in plans:
            plan.editable = can_edit(self.request.user, plan)
        context["plans"] = plans
        return context

    def post(self, request, *args, **kwargs):
        name = _clean_name(request.POST.get("name"))
        if not name:
            context = self.get_context_data(name_error="Give the microplan a name.")
            return self.render_to_response(context, status=400)
        plan = SimpleMicroplan.objects.create(
            name=name, created_by=request.user, created_by_name=_display_name(request.user)
        )
        return HttpResponseRedirect(reverse("microplans:simple_editor", kwargs={"plan_id": plan.pk}))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SimpleMicroplanEditorView(_SimpleMicroplanAccessMixin, TemplateView):
    """The creator itself: a full-screen map page seeded with the plan's saved state."""

    template_name = "microplans/simple_editor.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        plan = get_object_or_404(SimpleMicroplan, pk=kwargs["plan_id"])
        context["plan"] = plan
        context["plan_state"] = {
            "id": plan.pk,
            "name": plan.name,
            "created_by": plan.created_by_name,
            "created_at": plan.created_at.isoformat(),
            "updated_at": plan.updated_at.isoformat(),
            "editable": can_edit(self.request.user, plan),
            "data": plan.data or {},
            "api_url": reverse("microplans:simple_api", kwargs={"plan_id": plan.pk}),
            "list_url": reverse("microplans:simple_list"),
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

    def post(self, request, plan_id):
        plan = get_object_or_404(SimpleMicroplan, pk=plan_id)
        if not can_edit(request.user, plan):
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
    def post(self, request, plan_id):
        plan = get_object_or_404(SimpleMicroplan, pk=plan_id)
        if not can_edit(request.user, plan):
            raise Http404
        plan.delete()
        return HttpResponseRedirect(reverse("microplans:simple_list"))
