"""CRUD views for SyntheticOpportunity registry entries."""

from __future__ import annotations

import logging
from collections.abc import Generator

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import PermissionDenied
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from commcare_connect.labs.analysis.sse_streaming import BaseSSEStreamView, send_sse_event
from commcare_connect.labs.integrations.connect import factory
from commcare_connect.labs.synthetic import registry
from commcare_connect.labs.synthetic.dump import dump_generator
from commcare_connect.labs.synthetic.forms import SyntheticOpportunityForm
from commcare_connect.labs.synthetic.gdrive import DriveAPIError, DriveAuthError, DriveClient
from commcare_connect.labs.synthetic.models import SyntheticOpportunity

logger = logging.getLogger(__name__)


class SyntheticListView(LoginRequiredMixin, ListView):
    model = SyntheticOpportunity
    template_name = "labs/synthetic/list.html"
    context_object_name = "opps"

    def get_queryset(self):
        opp_ids = registry.accessible_opp_ids(self.request)
        return super().get_queryset().filter(opportunity_id__in=opp_ids)


class SyntheticCreateView(LoginRequiredMixin, CreateView):
    model = SyntheticOpportunity
    form_class = SyntheticOpportunityForm
    template_name = "labs/synthetic/form.html"
    success_url = reverse_lazy("labs:synthetic:list")

    def dispatch(self, request, *args, **kwargs):
        labs_context = getattr(request, "labs_context", None) or {}
        opp_id = labs_context.get("opportunity_id")
        accessible = registry.accessible_opp_ids(request)
        if not opp_id or opp_id not in accessible:
            messages.warning(
                request,
                "Select an opportunity from the context selector before creating a synthetic entry.",
            )
            return HttpResponseRedirect(reverse("labs:synthetic:list"))
        self._context_opp_id = opp_id
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        return {"opportunity_id": self._context_opp_id}

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        if self.request.method in ("POST", "PUT") and "data" in kwargs:
            # Inject the context opp_id so form validation passes even though
            # the hidden input was not submitted in the POST body.
            data = kwargs["data"].copy()
            data["opportunity_id"] = self._context_opp_id
            kwargs["data"] = data
        return kwargs

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["context_opp_id"] = self._context_opp_id
        ctx["context_opp_name"] = self._lookup_opp_name(self._context_opp_id)
        return ctx

    def _lookup_opp_name(self, opp_id: int) -> str:
        from commcare_connect.labs.context import get_org_data

        for opp in get_org_data(self.request).get("opportunities", []):
            if int(opp.get("id", 0)) == int(opp_id):
                return opp.get("name", "")
        return ""

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        # Reassert from context to prevent POST tampering of the hidden input.
        form.instance.opportunity_id = self._context_opp_id
        return super().form_valid(form)


class _AccessScopedMixin:
    """Restrict the queryset to opportunities the current user has Connect access to."""

    def get_queryset(self):
        opp_ids = registry.accessible_opp_ids(self.request)
        return super().get_queryset().filter(opportunity_id__in=opp_ids)


class SyntheticUpdateView(LoginRequiredMixin, _AccessScopedMixin, UpdateView):
    model = SyntheticOpportunity
    form_class = SyntheticOpportunityForm
    template_name = "labs/synthetic/form.html"
    success_url = reverse_lazy("labs:synthetic:list")

    def form_valid(self, form):
        # opportunity_id is identity; never allow it to change after creation.
        form.instance.opportunity_id = self.get_object().opportunity_id
        return super().form_valid(form)


class SyntheticDeleteView(LoginRequiredMixin, _AccessScopedMixin, DeleteView):
    model = SyntheticOpportunity
    template_name = "labs/synthetic/confirm_delete.html"
    success_url = reverse_lazy("labs:synthetic:list")


@login_required
@require_POST
def refresh_cache_view(request):
    """Clear the in-worker registry cache."""
    registry.invalidate_cache()
    messages.success(request, "Registry cache refreshed.")
    return HttpResponseRedirect(reverse("labs:synthetic:list"))


@login_required
@require_POST
def reload_fixtures_view(request, pk: int):
    """Drop the in-process fixture cache for one opp so the next call re-pulls from GDrive."""
    opp_ids = registry.accessible_opp_ids(request)
    opp = get_object_or_404(SyntheticOpportunity, pk=pk, opportunity_id__in=opp_ids)
    store = factory._get_fixture_store()
    store.reload(opp.opportunity_id)
    messages.success(request, f"Fixture cache reloaded for opp {opp.opportunity_id}.")
    return HttpResponseRedirect(reverse("labs:synthetic:list"))


@login_required
def test_access_view(request):
    """AJAX endpoint: list files visible in a GDrive folder for the current service account."""
    folder_id = request.GET.get("folder_id", "").strip()
    if not folder_id:
        return JsonResponse({"ok": False, "error": "folder_id is required"}, status=400)
    try:
        drive = DriveClient()
        files = drive.list_folder(folder_id)
    except DriveAuthError as e:
        return JsonResponse({"ok": False, "error": f"Auth failure: {e}"}, status=500)
    except DriveAPIError as e:
        return JsonResponse({"ok": False, "error": f"Drive error: {e}"}, status=500)
    return JsonResponse({"ok": True, "files": sorted(files.keys())})


class DumpStreamView(BaseSSEStreamView):
    """SSE endpoint: dump the current labs_context opp's exports to a new GDrive folder.

    Any exception is caught here and surfaced as a final SSE error event so the
    browser sees the failure reason before the stream closes.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        try:
            labs_context = getattr(request, "labs_context", None) or {}
            opp_id = labs_context.get("opportunity_id")
            if not opp_id:
                raise PermissionDenied("No opportunity selected in labs context.")
            if opp_id not in registry.accessible_opp_ids(request):
                raise PermissionDenied(f"Opportunity {opp_id} not in user's accessible set.")

            access_token = (request.session.get("labs_oauth") or {}).get("access_token")
            if not access_token:
                raise PermissionDenied("No OAuth access token in session.")

            yield from dump_generator(opp_id, access_token)
        except Exception as e:  # noqa: BLE001
            logger.exception("synthetic dump failed")
            yield send_sse_event("Dump failed", error=f"{type(e).__name__}: {e}")
