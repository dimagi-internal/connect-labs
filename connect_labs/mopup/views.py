"""Views for the CHC mop-up feature.

Phase 1 (setup) only, for now: opportunity picker (free — from the session's
org/program/opportunity tree, no data pull), ward picker (cheap — work-area
case properties only, see core/work_areas.py), and an optional coarse date
range. Only once all three are picked does anything touch visit-form data —
and that's Phase 2, not built yet.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.context import get_org_data
from connect_labs.mopup.core.data_access import MopupRunDataAccess
from connect_labs.mopup.core.work_areas import list_work_areas, summarize_wards

logger = logging.getLogger(__name__)


def _program_opportunities(request, program_id: int) -> list[dict]:
    """The program's own opportunities, from the session's org/program/
    opportunity tree (`get_org_data`) — free, no API call of our own. Mirrors
    the exact nested shape `mcp__connect_labs__labs_context` exposes
    (organizations -> programs -> opportunities), since `get_org_data` is the
    same data, just read from the session instead of fetched live."""
    org_data = get_org_data(request)
    for org in org_data.get("organizations", []):
        for program in org.get("programs", []):
            if program.get("id") == program_id:
                return program.get("opportunities", [])
    return []


class MopupSetupView(LoginRequiredMixin, TemplateView):
    """Phase 1: pick opportunity, ward(s), optional date range."""

    template_name = "mopup/setup.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs["program_id"]
        context["program_id"] = program_id
        context["opportunities"] = _program_opportunities(self.request, program_id)
        context["ward_list_url"] = reverse("mopup:ward_list", args=[program_id])
        context["create_run_url"] = reverse("mopup:create_run", args=[program_id])
        return context


class MopupWardListView(LoginRequiredMixin, View):
    """JSON: work-area-case-derived ward summary for one opportunity — the
    cheap pull (§4). Append ``?refresh=1`` to bypass the pipeline's cache
    (see core/work_areas.py's module docstring)."""

    def get(self, request, program_id):
        opportunity_id = request.GET.get("opportunity_id")
        if not opportunity_id:
            return JsonResponse({"status": "error", "detail": "opportunity_id is required"}, status=400)
        try:
            opportunity_id = int(opportunity_id)
        except (TypeError, ValueError):
            return JsonResponse({"status": "error", "detail": "opportunity_id must be an integer"}, status=400)

        try:
            work_areas = list_work_areas(opportunity_id, request=request)
        except Exception:  # noqa: BLE001
            logger.exception(
                "mopup ward_list: fetching work areas failed (program=%s opportunity=%s)",
                program_id,
                opportunity_id,
            )
            return JsonResponse({"status": "error", "detail": "Could not load work areas."}, status=502)

        wards = summarize_wards(work_areas)
        return JsonResponse({"status": "ok", "wards": wards})


class MopupCreateRunView(LoginRequiredMixin, View):
    """Phase 1's "start analysis" action: create a run record and record the
    picked ward(s)/date range on it. Phase 2 (the analysis screen this hands
    off to) doesn't exist yet — this just persists the setup choices."""

    def post(self, request, program_id):
        try:
            payload = json.loads(request.body)
            target_opportunity_id = int(payload["opportunity_id"])
            wards = payload.get("wards") or []
            if not isinstance(wards, list):
                raise ValueError("wards must be a list")
            date_from = payload.get("date_from") or None
            date_to = payload.get("date_to") or None
            name = str(payload.get("name", "") or "").strip()[:255]
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = MopupRunDataAccess(program_id, request=request)
        run = da.create_run(target_opportunity_id=target_opportunity_id, name=name)
        run = da.set_ward_selection(run, wards=wards, date_from=date_from, date_to=date_to)

        return JsonResponse(
            {
                "status": "ok",
                "run_id": run.id,
                "run_status": run.status,
                "target_opportunity_id": run.target_opportunity_id,
                "selected_wards": run.selected_wards,
                "date_from": run.date_from,
                "date_to": run.date_to,
            }
        )
