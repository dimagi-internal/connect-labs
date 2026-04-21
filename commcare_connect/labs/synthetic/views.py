"""CRUD views for SyntheticOpportunity registry entries."""

from __future__ import annotations

import logging

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import get_object_or_404
from django.urls import reverse, reverse_lazy
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from commcare_connect.labs.integrations.connect import factory
from commcare_connect.labs.synthetic import registry
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

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        return super().form_valid(form)


class SyntheticUpdateView(LoginRequiredMixin, UpdateView):
    model = SyntheticOpportunity
    form_class = SyntheticOpportunityForm
    template_name = "labs/synthetic/form.html"
    success_url = reverse_lazy("labs:synthetic:list")


class SyntheticDeleteView(LoginRequiredMixin, DeleteView):
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
    opp = get_object_or_404(SyntheticOpportunity, pk=pk)
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
