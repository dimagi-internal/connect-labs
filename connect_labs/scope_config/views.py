"""The Settings page: `/labs/settings/<scope type>/<key>/`.

One section per namespace. Each shows what is in effect here and where each value
came from ("labs default", "organisation Dimagi", "this programme"); its members can
change this scope's own layer, see its history and undo any change. Someone who
cannot use the scope gets a 404 -- the same as any labs page for a scope they are
not in.
"""

from __future__ import annotations

import json

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponseRedirect
from django.views.generic import TemplateView

from connect_labs.labs.access.scopes import Caller
from connect_labs.scope_config import service
from connect_labs.scope_config.merge import diff_patch
from connect_labs.scope_config.namespaces import all_namespaces
from connect_labs.scope_config.scopes import Scope

SOURCE_LABELS = {"default": "labs default"}


def _caller(request) -> Caller:
    return Caller(user=request.user, request=request)


def _scope(kwargs) -> Scope:
    try:
        return Scope.of(kwargs["scope_type"], kwargs["scope_key"])
    except ValueError as exc:
        raise Http404(str(exc)) from None


def _rows(value, provenance, own_label) -> list[dict]:
    rows = []
    for path in sorted(provenance):
        cursor = value
        for part in path.split("."):
            cursor = cursor.get(part) if isinstance(cursor, dict) else None
        source = provenance[path]
        rows.append(
            {
                "path": path,
                "value": json.dumps(cursor),
                "source": (
                    "this " + own_label.split(" ", 1)[0] if source == own_label else SOURCE_LABELS.get(source, source)
                ),
            }
        )
    return rows


def _supply_tabs(got) -> list[dict]:
    """Supply's tabs as a reader sees them: built-in ones in order, then added ones."""
    from connect_labs.supply_chain.config import HOME, slug_of
    from connect_labs.supply_chain.navigation import SUPPLY_TABS

    tabs = got["value"].get("tabs") or {}
    own = (got["data"].get("tabs") or {}) if got["settable_here"] else {}

    def setter(key):
        sources = {v for p, v in got["provenance"].items() if p.startswith(f"tabs.{key}.") or p == f"tabs.{key}"}
        return ", ".join(sorted(sources)) or "labs default"

    out = []
    for name, label in SUPPLY_TABS:
        tab = tabs.get(name) or {}
        out.append(
            {
                "key": name,
                "label": tab.get("label") or label,
                "builtin_label": label,
                "shown": not tab.get("hidden"),
                "can_hide": name != HOME,
                "fill": tab.get("fill"),
                "slug": slug_of(name, tab) if tab.get("fill") else "",
                "set_by": setter(name),
                "own": name in own,
            }
        )
    for key, tab in tabs.items():
        if key in dict(SUPPLY_TABS):
            continue
        out.append(
            {
                "key": key,
                "label": tab.get("label") or key,
                "builtin_label": "",
                "shown": not tab.get("hidden"),
                "can_hide": True,
                "fill": tab.get("fill"),
                "slug": slug_of(key, tab),
                "set_by": setter(key),
                "own": key in own,
            }
        )
    return out


class SettingsView(LoginRequiredMixin, TemplateView):
    template_name = "scope_config/settings.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = _scope(self.kwargs)
        caller = _caller(self.request)
        if service.refusal(caller, scope):
            raise Http404("not a scope you can use")
        sections = []
        for namespace in all_namespaces():
            got = service.get(namespace.key, scope, caller)
            own_label = got["scope"]["label"]
            sections.append(
                {
                    "namespace": namespace,
                    "got": got,
                    "rows": _rows(got["value"], got["provenance"], own_label),
                    "own_json": json.dumps(got["data"], indent=2, sort_keys=True),
                    "history": service.history(namespace.key, scope, caller, limit=20) if got["settable_here"] else [],
                    "supply_tabs": _supply_tabs(got) if namespace.key == "supply" else None,
                }
            )
        context.update(scope=scope, scope_label=sections[0]["got"]["scope"]["label"] if sections else str(scope))
        context["sections"] = sections
        context["scope_noun"] = {"organization": "organisation", "program": "programme"}.get(scope.type, scope.type)
        return context

    def post(self, request, *args, **kwargs):
        scope = _scope(self.kwargs)
        caller = _caller(request)
        if service.refusal(caller, scope):
            raise Http404("not a scope you can use")
        namespace = request.POST.get("namespace", "")
        action = request.POST.get("action", "")
        try:
            if action == "save":
                current = service.get(namespace, scope, caller)
                try:
                    new = json.loads(request.POST.get("data") or "{}")
                except json.JSONDecodeError as exc:
                    raise service.Invalid(f"that is not valid JSON: {exc.msg} (line {exc.lineno})") from None
                if not isinstance(new, dict):
                    raise service.Invalid("a settings layer is a JSON object")
                service.update(
                    namespace,
                    scope,
                    diff_patch(current["data"], new),
                    caller,
                    expected_version=int(request.POST.get("version", "0")),
                    via="settings",
                )
                messages.success(request, "Saved.")
            elif action == "undo":
                service.undo(namespace, scope, caller, change_id=int(request.POST["change_id"]), via="settings:undo")
                messages.success(request, "Undone.")
            elif action in ("hide_tab", "show_tab"):
                key = request.POST.get("tab", "")
                current = (service.get(namespace, scope, caller)["data"].get("tabs") or {}).get(key) or {}
                if action == "hide_tab":
                    patch = {key: {"hidden": True}}
                else:
                    rest = {k: v for k, v in current.items() if k != "hidden"}
                    patch = {key: {"hidden": None} if rest else None}
                service.update(
                    namespace,
                    scope,
                    {"tabs": patch},
                    caller,
                    expected_version=int(request.POST.get("version", "0")),
                    via="settings",
                )
                messages.success(request, "Shown." if action == "show_tab" else "Hidden.")
            else:
                raise service.Invalid(f"unknown action {action!r}")
        except KeyError as exc:
            messages.error(request, str(exc).strip("'\""))
        except service.ConfigError as exc:
            messages.error(request, str(exc))
        return HttpResponseRedirect(request.path + (f"#{namespace}" if namespace else ""))
