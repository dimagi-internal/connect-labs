"""Semantic Registries -- view and delete the indicator registries a report computes from.

A registry is a LabsRecord (experiment `semantic`, type `semantic_registry`) holding a
report's indicator definitions. They are created and edited through the
`semantic_registry_*` MCP tools, which have no delete, and until this page nothing
listed them outside an MCP client -- so a scratch registry, once made, had no way out.

Reads are an exact scope match, as for every LabsRecord: a registry is listed from its
own opportunity, program or organisation, plus every shared one. The scope comes from
the query string (`?opportunity_id=` / `?program_id=` / `?organization_id=`), else from
the labs context, so a link to a registry carries the scope it lives in.

Deleting the registry a live report is bound to breaks that report on its next load,
so a delete must name the registry's id back -- checked here, not in the browser.
"""

from __future__ import annotations

import json
import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.view_mixins import AdminRequiredMixin

logger = logging.getLogger(__name__)

SCOPE_KEYS = ("opportunity_id", "program_id", "organization_id")


def _scope_from(params) -> dict:
    """The one scope the caller named, as {key: int}; empty means the labs context."""
    for key in SCOPE_KEYS:
        raw = (params.get(key) or "").strip()
        if raw:
            try:
                return {key: int(raw)}
            except ValueError:
                return {}
    return {}


def _record_scope(record) -> dict:
    """The scope a registry record lives in -- the one to read and delete it through."""
    for key in SCOPE_KEYS:
        value = getattr(record, key, None)
        if value not in (None, ""):
            try:
                return {key: int(value)}
            except (TypeError, ValueError):
                continue
    return {}


def _scope_query(scope: dict) -> str:
    return "&".join(f"{k}={v}" for k, v in scope.items())


def _access(request, scope: dict):
    from connect_labs.workflow.data_access import SemanticRegistryDataAccess

    return SemanticRegistryDataAccess(request=request, **scope)


def _summary(record) -> dict:
    from connect_labs.semantic.model import series_prefixes

    inds = record.indicators_doc or {}
    scope = _record_scope(record)
    return {
        "id": record.id,
        "name": record.name,
        "description": record.description,
        "version": record.version,
        "is_shared": record.is_shared or getattr(record, "public", False),
        "scope": scope,
        "scope_label": ", ".join(f"{k.replace('_id', '')} {v}" for k, v in scope.items()) or "—",
        "scope_query": _scope_query(scope),
        "families": ", ".join(series_prefixes(inds)) or "—",
        "indicator_count": sum(1 for m in inds.get("measures") or [] if isinstance(m, dict) and m.get("meta")),
        "property_count": len((record.properties_doc or {}).get("properties") or []),
    }


class SemanticRegistryListView(AdminRequiredMixin, TemplateView):
    """Every registry in one scope, plus the shared ones."""

    template_name = "labs/admin/semantic_registries.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        scope = _scope_from(self.request.GET)
        context["scope"] = scope
        context["registries"] = []
        try:
            with _access(self.request, scope) as access:
                # The scope actually read: the one named, else the labs context.
                context["scope_label"] = (
                    ", ".join(
                        f"{k.replace('_id', '')} {getattr(access, k)}" for k in SCOPE_KEYS if getattr(access, k, None)
                    )
                    or "no scope selected (shared registries only)"
                )
                records = access.list_registries(include_shared=True)
            context["registries"] = sorted((_summary(r) for r in records), key=lambda s: -s["id"])
        except Exception as exc:  # the page must say what failed, not 500
            logger.warning("semantic registry list failed", exc_info=True)
            messages.error(self.request, f"Could not list registries: {exc}")
        return context


class SemanticRegistryDetailView(AdminRequiredMixin, TemplateView):
    """One registry: what it is, its indicators, and its three documents."""

    template_name = "labs/admin/semantic_registry_detail.html"

    def get_context_data(self, **kwargs):
        from connect_labs.semantic.model import indicator_series

        context = super().get_context_data(**kwargs)
        registry_id = int(kwargs["registry_id"])
        scope = _scope_from(self.request.GET)
        with _access(self.request, scope) as access:
            record = access.get_registry(registry_id, **scope)
            if record is None:
                # Not in this scope -- it may still be a shared one.
                record = access.get_registry(registry_id, public=True)
        if record is None:
            raise Http404(f"No semantic registry {registry_id} in this scope")
        summary = _summary(record)
        inds = record.indicators_doc or {}
        context["registry"] = summary
        context["indicators"] = [
            {
                "id": (m.get("meta") or {}).get("indicator") or m.get("name"),
                "title": m.get("title") or "",
                "category": (m.get("meta") or {}).get("category") or "",
                "unit": (m.get("meta") or {}).get("unit") or "",
                "family": indicator_series(inds, m.get("meta")),
            }
            for m in inds.get("measures") or []
            if isinstance(m, dict) and m.get("meta")
        ]
        context["documents"] = [
            ("Indicators (Layer 3)", json.dumps(inds, indent=2, default=str)),
            ("Properties (Layer 2)", json.dumps(record.properties_doc or {}, indent=2, default=str)),
            ("Deployment facts", json.dumps(record.deployment or {}, indent=2, default=str)),
        ]
        return context


class SemanticRegistryDeleteView(AdminRequiredMixin, View):
    """Delete one registry, in the scope it lives in, once its id is typed back."""

    def post(self, request, registry_id):
        registry_id = int(registry_id)
        scope = _scope_from(request.POST)
        detail = reverse("labs_admin:semantic_registry_detail", args=[registry_id])
        if scope:
            detail += "?" + _scope_query(scope)

        # A registry a report is bound to is its indicator definitions: deleting it
        # takes that report down on the next load. The browser's confirm() is one
        # click away from a mistake; naming the id back is not.
        if (request.POST.get("confirm_id") or "").strip() != str(registry_id):
            messages.error(request, f"Not deleted: type {registry_id} to confirm.")
            return redirect(detail)

        try:
            with _access(request, scope) as access:
                record = access.get_registry(registry_id, **scope)
                if record is None:
                    messages.error(request, f"Registry {registry_id} is not in this scope, so it was not deleted.")
                    return redirect(detail)
                access.delete_registry(registry_id)
        except Exception as exc:
            logger.warning("semantic registry delete failed for %s", registry_id, exc_info=True)
            messages.error(request, f"Could not delete registry {registry_id}: {exc}")
            return redirect(detail)

        messages.success(request, f"Deleted registry {registry_id} ({record.name}).")
        target = reverse("labs_admin:semantic_registries")
        return redirect(target + ("?" + _scope_query(scope) if scope else ""))
