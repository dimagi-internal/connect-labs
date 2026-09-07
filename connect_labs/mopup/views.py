"""Views for the CHC mop-up feature.

Phase 1: opportunity picker (free — from the session's org/program/
opportunity tree, no data pull), ward picker (cheap — work-area case
properties only, see core/work_areas.py), and an optional coarse date range.

Phase 2 (MopupCandidatesView): the live threshold-tunable candidate list —
takes a run's scoped opportunity/wards, evaluates the §5/§6 indicators
against whatever threshold/granularity config the reviewer currently has
set, and returns candidates + a per-ward summary. Not yet built: the actual
candidate-table/map UI (this is the JSON endpoint it will call), and locking
a candidate set into Phase 3's microplans hand-off.
"""

from __future__ import annotations

import json
import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View
from django.views.generic import TemplateView

from connect_labs.labs.context import get_org_data
from connect_labs.mopup.core import indicators as ind
from connect_labs.mopup.core.candidates import build_evaluation_input, summarize_candidates_by_ward
from connect_labs.mopup.core.data_access import MopupRunDataAccess
from connect_labs.mopup.core.handoff import HandoffError, create_plan_from_locked_run
from connect_labs.mopup.core.models import STATUS_LOCKED
from connect_labs.mopup.core.work_areas import list_work_areas, summarize_wards

logger = logging.getLogger(__name__)


def _resolve_thresholds(run, payload: dict) -> tuple[dict, dict]:
    """Given a request payload, the run's saved thresholds, and the built-in
    defaults, resolve which indicator/global config to evaluate with — in
    that priority order."""
    indicator_configs = (
        payload.get("indicator_configs")
        or run.thresholds.get("indicator_configs")
        or dict(ind.DEFAULT_INDICATOR_CONFIGS)
    )
    global_config = (
        payload.get("global_config") or run.thresholds.get("global_config") or dict(ind.DEFAULT_GLOBAL_CONFIG)
    )
    return indicator_configs, global_config


def _evaluate(run, request, payload: dict) -> tuple[list[dict], list[dict], list[dict], dict, dict]:
    """Fetch the run's scoped data and evaluate it against the resolved
    thresholds. Returns (rows, candidates, ward_summary, indicator_configs,
    global_config). Raises whatever build_evaluation_input raises — callers
    catch and translate to a 502."""
    indicator_configs, global_config = _resolve_thresholds(run, payload)
    rows = build_evaluation_input(run.target_opportunity_id, run.selected_wards, request=request)
    candidates = ind.evaluate_run(rows, indicator_configs, global_config)
    ward_summary = summarize_candidates_by_ward(candidates, rows)
    return rows, candidates, ward_summary, indicator_configs, global_config


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


class MopupAnalysisView(LoginRequiredMixin, TemplateView):
    """Phase 2: the threshold-tunable candidate-analysis screen for one run.
    Bootstraps the page with the run's saved (or default) indicator/global
    config; the JS drives further recomputes via MopupCandidatesView."""

    template_name = "mopup/analysis.html"

    def get(self, request, *args, **kwargs):
        from django.http import Http404

        da = MopupRunDataAccess(kwargs["program_id"], request=request)
        run = da.get_run(kwargs["run_id"])
        if run is None:
            raise Http404("Mop-up run not found.")
        self._run = run
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs["program_id"]
        run_id = kwargs["run_id"]
        run = self._run

        context["program_id"] = program_id
        context["run_id"] = run_id
        context["run"] = run
        context["candidates_url"] = reverse("mopup:candidates", args=[program_id, run_id])
        context["lock_url"] = reverse("mopup:lock", args=[program_id, run_id])
        context["create_plan_url"] = reverse("mopup:create_plan", args=[program_id, run_id])
        context["indicator_configs"] = run.thresholds.get("indicator_configs") or ind.DEFAULT_INDICATOR_CONFIGS
        context["global_config"] = run.thresholds.get("global_config") or ind.DEFAULT_GLOBAL_CONFIG
        context["indicator_defs"] = [
            {"key": ind.EVC_SHORTFALL, "label": "EVC shortfall", "direction": "below"},
            {"key": ind.NCF_INACCESSIBLE, "label": "NCF / inaccessible rate", "direction": "above"},
            {"key": ind.DEWORMING, "label": "Deworming completion", "direction": "below"},
            {"key": ind.MUAC, "label": "MUAC-recorded rate", "direction": "below"},
            {"key": ind.VACCINATION, "label": "Vaccination-given rate", "direction": "below"},
        ]
        return context


class MopupCandidatesView(LoginRequiredMixin, View):
    """Phase 2's live recompute: evaluate the run's scoped work areas against
    the given (or run-saved, or default) indicator/global config and return
    candidates + a per-ward summary. Every POST also persists the thresholds
    used onto the run, so reopening it resumes where the reviewer left off —
    "live" only in the sense that nothing is locked until an explicit lock
    action (not built yet); this view can be called repeatedly as thresholds
    change.

    Known gap: only ward-scoping is applied — the run's date_from/date_to
    isn't yet (see core/candidates.py's module docstring for why)."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            rows, candidates, ward_summary, indicator_configs, global_config = _evaluate(run, request, payload)
        except Exception:  # noqa: BLE001
            logger.exception(
                "mopup candidates: fetching evaluation data failed (program=%s run=%s)", program_id, run_id
            )
            return JsonResponse({"status": "error", "detail": "Could not load visit/work-area data."}, status=502)

        da.update_run(run, thresholds={"indicator_configs": indicator_configs, "global_config": global_config})

        return JsonResponse(
            {
                "status": "ok",
                "candidates": candidates,
                "ward_summary": ward_summary,
                "total_work_areas": len(rows),
                "candidate_count": len(candidates),
            }
        )


class MopupLockView(LoginRequiredMixin, View):
    """The Phase 2 -> Phase 3 boundary (design brief): freeze the CURRENT
    candidate set (evaluated against the given, or run-saved, or default
    thresholds — same resolution as MopupCandidatesView) onto the run as
    `candidate_work_areas`, and mark it locked. Nothing downstream re-reads
    live thresholds after this — Phase 3's hand-off only ever acts on what
    got frozen here."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            _rows, candidates, _ward_summary, indicator_configs, global_config = _evaluate(run, request, payload)
        except Exception:  # noqa: BLE001
            logger.exception("mopup lock: fetching evaluation data failed (program=%s run=%s)", program_id, run_id)
            return JsonResponse({"status": "error", "detail": "Could not load visit/work-area data."}, status=502)

        if not candidates:
            return JsonResponse(
                {"status": "error", "detail": "No candidates under the current thresholds — nothing to lock."},
                status=400,
            )

        run = da.update_run(
            run,
            status=STATUS_LOCKED,
            candidate_work_areas=candidates,
            thresholds={"indicator_configs": indicator_configs, "global_config": global_config},
        )

        return JsonResponse(
            {"status": "ok", "run_id": run.id, "run_status": run.status, "locked_count": len(candidates)}
        )


class MopupCreatePlanView(LoginRequiredMixin, View):
    """Phase 3: hand a locked run's candidate set to the existing microplans
    coverage engine. Only acts on `run.candidate_work_areas` (frozen at lock
    time) — never re-reads thresholds. See core/handoff.py for the actual
    microplans calls."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED:
            return JsonResponse({"status": "error", "detail": "Lock the run before creating a plan."}, status=400)

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            resp = create_plan_from_locked_run(
                run,
                program_id,
                request=request,
                grouping=payload.get("grouping"),
                group_id=payload.get("group_id"),
            )
        except HandoffError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("mopup create_plan: hand-off failed (program=%s run=%s)", program_id, run_id)
            return JsonResponse({"status": "error", "detail": "Could not create the plan."}, status=502)

        resp["status"] = "ok"
        return JsonResponse(resp)
