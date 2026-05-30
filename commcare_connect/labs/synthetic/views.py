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
from django.utils import timezone
from django.views.decorators.http import require_POST
from django.views.generic import CreateView, DeleteView, ListView, UpdateView

from commcare_connect.labs.analysis.sse_streaming import BaseSSEStreamView, send_sse_event
from commcare_connect.labs.integrations.connect import factory
from commcare_connect.labs.synthetic import registry
from commcare_connect.labs.synthetic.dump import dump_generator
from commcare_connect.labs.synthetic.forms import LabsOnlySyntheticOpportunityForm, SyntheticOpportunityForm
from commcare_connect.labs.synthetic.gdrive import DriveAPIError, DriveAuthError, DriveClient
from commcare_connect.labs.synthetic.models import SyntheticOpportunity, UserSyntheticDataset
from commcare_connect.labs.synthetic.self_service import SyntheticGenerationError, generate_and_save

logger = logging.getLogger(__name__)


class SyntheticListView(LoginRequiredMixin, ListView):
    model = SyntheticOpportunity
    template_name = "labs/synthetic/list.html"
    context_object_name = "opps"

    def get_queryset(self):
        opp_ids = registry.accessible_opp_ids(self.request)
        qs = super().get_queryset().filter(opportunity_id__in=opp_ids)
        # Annotate each row with the Connect opp name so the template can
        # show "Demo Opp (1237)" rather than a bare integer.
        from commcare_connect.labs.context import get_org_data

        names = {
            int(o["id"]): o.get("name", "")
            for o in get_org_data(self.request).get("opportunities", [])
            if o.get("id") is not None
        }
        rows = list(qs)
        for r in rows:
            r.connect_name = names.get(r.opportunity_id, "")
        return rows

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        opps = ctx["opps"]
        ctx["active_count"] = sum(1 for o in opps if o.enabled)
        ctx["disabled_count"] = sum(1 for o in opps if not o.enabled)
        ctx["accessible_count"] = len(registry.accessible_opp_ids(self.request))
        return ctx


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
        name, org = self._lookup_opp(self._context_opp_id)
        ctx["context_opp_id"] = self._context_opp_id
        ctx["context_opp_name"] = name
        ctx["context_opp_org"] = org
        return ctx

    def _lookup_opp(self, opp_id: int) -> tuple[str, str]:
        from commcare_connect.labs.context import get_org_data

        for opp in get_org_data(self.request).get("opportunities", []):
            if int(opp.get("id", 0)) == int(opp_id):
                return opp.get("name", ""), opp.get("organization", "")
        return "", ""

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


class LabsOnlySyntheticCreateView(LoginRequiredMixin, CreateView):
    """Create a brand-new labs-only synthetic opp (no real Connect opp behind it).

    Unlike SyntheticCreateView, this does NOT require a Connect opp in the
    labs_context — labs-only opps are stand-alone fixtures. opportunity_id is
    auto-allocated by the form via SyntheticOpportunity.next_labs_only_opp_id().
    """

    model = SyntheticOpportunity
    form_class = LabsOnlySyntheticOpportunityForm
    template_name = "labs/synthetic/labs_only_form.html"
    success_url = reverse_lazy("labs:synthetic:list")

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        registry.invalidate_cache()
        messages.success(
            self.request,
            f"Labs-only synthetic opp {self.object.opportunity_id} created.",
        )
        return response


class LabsOnlySyntheticUpdateView(LoginRequiredMixin, UpdateView):
    """Edit a labs-only synthetic opp. Access scoped to labs_only rows the user owns or can see."""

    model = SyntheticOpportunity
    form_class = LabsOnlySyntheticOpportunityForm
    template_name = "labs/synthetic/labs_only_form.html"
    success_url = reverse_lazy("labs:synthetic:list")

    def get_queryset(self):
        # Limit to labs_only rows the user can see — visibility uses the same is_visible_to
        # gate that controls labs_context injection, with created_by as a fallback for
        # opps the user authored before turning view_synthetic_opps on.
        user = self.request.user
        qs = super().get_queryset().filter(labs_only=True)
        visible_ids = [opp.id for opp in qs if opp.is_visible_to(user) or opp.created_by_id == user.id]
        return qs.filter(id__in=visible_ids)

    def form_valid(self, form):
        # opportunity_id is identity; never allow it to change after creation.
        form.instance.opportunity_id = self.get_object().opportunity_id
        response = super().form_valid(form)
        registry.invalidate_cache()
        return response


class LabsOnlyCloneFromOppView(LoginRequiredMixin, CreateView):
    """Clone an existing SyntheticOpportunity (real OR labs-only) into a new labs-only one.

    Reuses the source's gdrive_folder_id by default (same fixture set, new opp_id).
    The user lands on the labs-only create form with everything pre-filled and can
    edit before saving. Source opp must be one the user can access via the registry.
    """

    model = SyntheticOpportunity
    form_class = LabsOnlySyntheticOpportunityForm
    template_name = "labs/synthetic/labs_only_form.html"
    success_url = reverse_lazy("labs:synthetic:list")

    def dispatch(self, request, *args, **kwargs):
        source_opp_id = kwargs["source_opp_id"]
        accessible = registry.accessible_opp_ids(request)
        if source_opp_id not in accessible:
            messages.warning(
                request,
                f"You don't have access to opportunity {source_opp_id} — cannot clone.",
            )
            return HttpResponseRedirect(reverse("labs:synthetic:list"))
        self._source = get_object_or_404(SyntheticOpportunity, opportunity_id=source_opp_id)
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        src = self._source
        return {
            "label": f"Clone of {src.label or src.opportunity_id}",
            "org_name": src.org_name or "Labs Synthetic",
            "program_name": src.program_name or "Labs Synthetic",
            "gdrive_folder_id": src.gdrive_folder_id,
            "enabled": True,
            "notes": f"Cloned from opp {src.opportunity_id} on {timezone.now().date().isoformat()}.",
        }

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["source"] = self._source
        ctx["clone_mode"] = True
        return ctx

    def form_valid(self, form):
        form.instance.created_by = self.request.user
        response = super().form_valid(form)
        registry.invalidate_cache()
        messages.success(
            self.request,
            f"Cloned opp {self._source.opportunity_id} → labs-only opp {self.object.opportunity_id}.",
        )
        return response


@login_required
@require_POST
def refresh_cache_view(request):
    """Clear the in-worker registry cache."""
    registry.invalidate_cache()
    messages.success(request, "Registry cache refreshed.")
    return HttpResponseRedirect(reverse("labs:synthetic:list"))


@login_required
@require_POST
def toggle_view_synthetic_opps_view(request):
    """Flip the current user's view_synthetic_opps toggle.

    Labs-only synthetic opps appear in this user's labs_context iff this is on
    AND their email domain matches one of the opp's allowed_domains.
    """
    user = request.user
    user.view_synthetic_opps = not user.view_synthetic_opps
    user.save(update_fields=["view_synthetic_opps"])
    state = "on" if user.view_synthetic_opps else "off"
    messages.success(request, f"view_synthetic_opps is now {state}.")
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


@login_required
@require_POST
def self_service_generate_view(request):
    """Generate synthetic data for the current user + opportunity."""
    labs_context = getattr(request, "labs_context", None) or {}
    opp_id = labs_context.get("opportunity_id")
    if not opp_id:
        return JsonResponse({"ok": False, "error": "No opportunity selected."}, status=400)

    try:
        visit_count = int(request.POST.get("visit_count", 200))
        visit_count = max(10, min(visit_count, 2000))
    except (TypeError, ValueError):
        return JsonResponse({"ok": False, "error": "Invalid visit count."}, status=400)

    real_visit_count = (labs_context.get("opportunity") or {}).get("visit_count", 0) or 0
    min_visits = 5
    if real_visit_count < min_visits:
        return JsonResponse(
            {
                "ok": False,
                "error": (
                    f"Synthetic data requires at least {min_visits} real visits."
                    f" This opportunity has {real_visit_count}."
                ),
            },
            status=400,
        )

    access_token = (request.session.get("labs_oauth") or {}).get("access_token")
    if not access_token:
        return JsonResponse({"ok": False, "error": "No OAuth token. Please log in again."}, status=403)

    try:
        dataset = generate_and_save(
            user=request.user,
            opportunity_id=opp_id,
            visit_count=visit_count,
            access_token=access_token,
        )
        # Clear the analysis SQL cache so the pipeline fetches fresh synthetic data
        from commcare_connect.labs.analysis.backends.sql.cache import SQLCacheManager

        SQLCacheManager.delete_all_cache(opp_id)
        return JsonResponse(
            {
                "ok": True,
                "visit_count": dataset.visit_count,
                "expires_at": dataset.expires_at.isoformat(),
            }
        )
    except SyntheticGenerationError as e:
        logger.warning("Self-service synthetic generation failed: %s", e)
        return JsonResponse({"ok": False, "error": str(e)}, status=500)
    except Exception:
        logger.exception("Unexpected error during synthetic generation")
        return JsonResponse({"ok": False, "error": "An unexpected error occurred."}, status=500)


@login_required
@require_POST
def self_service_clear_view(request):
    """Delete the user's synthetic dataset for the current opportunity."""
    labs_context = getattr(request, "labs_context", None) or {}
    opp_id = labs_context.get("opportunity_id")
    if not opp_id:
        return JsonResponse({"ok": False, "error": "No opportunity selected."}, status=400)

    deleted, _ = UserSyntheticDataset.objects.filter(user=request.user, opportunity_id=opp_id).delete()
    # Clear the analysis SQL cache so the pipeline re-fetches real data
    from commcare_connect.labs.analysis.backends.sql.cache import SQLCacheManager

    SQLCacheManager.delete_all_cache(opp_id)
    return JsonResponse({"ok": True, "deleted": deleted > 0})


@login_required
def self_service_status_view(request):
    """Return whether the user has active synthetic data for the current opportunity."""
    labs_context = getattr(request, "labs_context", None) or {}
    opp_id = labs_context.get("opportunity_id")
    if not opp_id:
        return JsonResponse({"active": False})

    dataset = UserSyntheticDataset.for_user_and_opp(request.user, opp_id)
    if dataset:
        return JsonResponse(
            {
                "active": True,
                "visit_count": dataset.visit_count,
                "expires_at": dataset.expires_at.isoformat(),
            }
        )
    return JsonResponse({"active": False})


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
