"""Views for the CHC mop-up feature.

Phase 1: opportunity picker (free — from the session's org/program/
opportunity tree, no data pull), ward picker (cheap — work-area case
properties only, see core/work_areas.py), and an optional coarse date range.

Phase 2 (MopupCandidatesView/MopupLockView): the live threshold-tunable
candidate list. The expensive part — pulling a whole opportunity's
work-area/visit/geometry data — runs exactly ONCE per run, in a Celery task
(`mopup.tasks.fetch_evaluation_data`; confirmed necessary this session — a
synchronous web request doing this for a real opportunity gateway-times
out). Every subsequent threshold/granularity tweak only re-runs
`evaluate_run()` over that task's already-fetched result — pure Python, no
network calls, and never re-dispatches the task. `_rows_or_progress` is the
one place that dispatches-if-needed and polls a run's fetch task; both
views go through it so "is the data ready yet" is answered identically
everywhere.
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
from connect_labs.mopup.core.candidates import summarize_candidates_by_ward
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


def _rows_or_progress(da, run, request, program_id) -> tuple[list[dict] | None, dict | None]:
    """Ensure a fetch task exists for `run` (dispatching one if it's never
    been started, or if the last one failed and was cleared), and report on
    it. Returns `(rows, None)` once the task has actually finished, or
    `(None, progress)` while it's still pending/running/failed — `progress`
    is `build_task_progress`'s own canonical shape (the same one workflow
    jobs and audit creation already use for poll-first progress), safe to
    return to the client verbatim."""
    from celery.result import AsyncResult

    from connect_labs.labs.analysis.sse_streaming import build_task_progress
    from connect_labs.mopup.tasks import fetch_evaluation_data

    task_id = run.fetch_task_id
    if not task_id:
        result = fetch_evaluation_data.delay(program_id, run.id, request.user.id)
        da.update_run(run, fetch_task_id=result.id)
        return None, build_task_progress("PENDING", None)

    task = AsyncResult(task_id)
    # Pass task.info through AS-IS — build_task_progress itself distinguishes
    # dict (PROGRESS/SUCCESS meta) from non-dict (a FAILURE's raw exception,
    # which it str()'s for the error message). Coercing non-dict to None here
    # would silently turn every real failure message into "Unknown error".
    progress = build_task_progress(task.state, task.info)

    if progress["status"] == "completed":
        return progress["result"]["rows"], None
    if progress["status"] == "failed":
        # Clear so the NEXT poll re-dispatches automatically — the reviewer
        # doesn't need a separate "retry" action, just keep polling (or
        # re-open the page).
        da.update_run(run, fetch_task_id=None)
    return None, progress


def _program_opportunities(request, program_id: int) -> list[dict]:
    """The program's own opportunities, from the session's org/program/
    opportunity data (`get_org_data`) — free, no API call of our own.

    `get_org_data(request)` is THREE FLAT top-level lists (`organizations`,
    `programs`, `opportunities`), not a nested tree — confirmed directly
    against production's `ProgramOpportunityOrganizationDataView`/
    `OpportunityDataExportSerializer` (dimagi/commcare-connect), whose
    `program` field is a `SerializerMethodField` returning `obj.program_id`
    (verified live against program 217: an earlier nested-tree assumption
    here returned zero opportunities in the browser). Mirrors the existing,
    already-working `get_org_data(self.request).get("opportunities", [])`
    pattern in `microplans/views.py`'s service-delivery opportunity picker."""
    org_data = get_org_data(request)
    return [opp for opp in org_data.get("opportunities", []) if opp.get("program") == program_id]


class MopupProgramHomeView(LoginRequiredMixin, TemplateView):
    """The bare `program/<id>/` landing page — mirrors microplans'
    `ProgramWorkspaceView` (`connect_labs/microplans/urls.py:48`) so the same
    URL-guessing convention that works for microplans also works here.
    Previously missing entirely: `/mopup/program/<id>/` 404'd, and there was
    no in-app link to mop-up at all (confirmed: no other labs template
    references the `mopup` app). Lists existing runs (own opportunity name
    resolved via `_program_opportunities`, same free/no-API-call lookup the
    setup page already uses) with a link to each run's current step, plus a
    "Start new mop-up" action into Phase 1."""

    template_name = "mopup/program_home.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs["program_id"]
        context["program_id"] = program_id
        context["setup_url"] = reverse("mopup:setup", args=[program_id])

        da = MopupRunDataAccess(program_id, request=self.request)
        opps_by_id = {opp["id"]: opp.get("name", "") for opp in _program_opportunities(self.request, program_id)}
        runs = []
        for run in da.list_runs():
            runs.append(
                {
                    "id": run.id,
                    "name": run.name,
                    "status": run.status,
                    "created_at": run.created_at,
                    "opportunity_name": opps_by_id.get(run.target_opportunity_id, ""),
                    "url": (
                        reverse("mopup:analysis", args=[program_id, run.id])
                        if run.status != "setup"
                        else reverse("mopup:setup", args=[program_id])
                    ),
                }
            )
        runs.sort(key=lambda r: r["created_at"], reverse=True)
        context["runs"] = runs
        return context


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
    (see core/work_areas.py's module docstring).

    Builds its own `AnalysisPipeline` from explicitly-refreshed tokens
    (`get_valid_access_token`/`get_valid_cchq_access_token`) rather than
    `AnalysisPipeline(request=request)` — that request-derived path reads
    the CCHQ token straight from the session with NO silent refresh (unlike
    `mopup.tasks.fetch_evaluation_data`'s pattern, which this mirrors), so a
    stale session token here means a real, fast CommCare HQ auth failure
    that the broad `except Exception` below was silently flattening into a
    generic "Could not load work areas." 502 — confirmed live against
    program 217 opportunity 2154 this session (~500ms failures, too fast to
    be an actual gateway timeout)."""

    def get(self, request, program_id):
        from connect_labs.labs.analysis.pipeline import AnalysisPipeline
        from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
        from connect_labs.labs.integrations.commcare.cchq_tokens import CCHQTokenError, get_valid_cchq_access_token

        opportunity_id = request.GET.get("opportunity_id")
        if not opportunity_id:
            return JsonResponse({"status": "error", "detail": "opportunity_id is required"}, status=400)
        try:
            opportunity_id = int(opportunity_id)
        except (TypeError, ValueError):
            return JsonResponse({"status": "error", "detail": "opportunity_id must be an integer"}, status=400)

        try:
            access_token = get_valid_access_token(request.user)
            cchq_access_token = get_valid_cchq_access_token(request.user)
        except (ConnectTokenError, CCHQTokenError) as e:
            return JsonResponse({"status": "error", "detail": f"Authorization needed: {e}"}, status=401)

        pipeline = AnalysisPipeline(access_token=access_token, cchq_access_token=cchq_access_token)
        try:
            work_areas = list_work_areas(opportunity_id, pipeline=pipeline)
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
    used onto the run, so reopening it resumes where the reviewer left off.

    The expensive data pull happens at most once per run (see
    `_rows_or_progress`) — while it's still running, this returns a
    progress snapshot instead (`{"status": "pending"|"running"|"failed",
    ...}`); once it's done, every call here is a fast, synchronous
    re-evaluation, however many times the reviewer tweaks thresholds.

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

        rows, progress = _rows_or_progress(da, run, request, program_id)
        if rows is None:
            return JsonResponse(progress)

        indicator_configs, global_config = _resolve_thresholds(run, payload)
        candidates = ind.evaluate_run(rows, indicator_configs, global_config)
        ward_summary = summarize_candidates_by_ward(candidates, rows)
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
    got frozen here. Same "at most one fetch" behavior as
    MopupCandidatesView — can't lock while the data is still loading."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        rows, progress = _rows_or_progress(da, run, request, program_id)
        if rows is None:
            return JsonResponse(progress)

        indicator_configs, global_config = _resolve_thresholds(run, payload)
        candidates = ind.evaluate_run(rows, indicator_configs, global_config)

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
                include_planning_gaps=bool(payload.get("include_planning_gaps", False)),
            )
        except HandoffError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("mopup create_plan: hand-off failed (program=%s run=%s)", program_id, run_id)
            return JsonResponse({"status": "error", "detail": "Could not create the plan."}, status=502)

        resp["status"] = "ok"
        return JsonResponse(resp)


class MopupDebugGeometryView(LoginRequiredMixin, View):
    """TEMPORARY diagnostic — remove once the "None of the locked candidates
    have boundary geometry" re-investigation is resolved. Calls
    fetch_work_area_geometry directly, via a HEADLESS pipeline (explicit
    access_token/cchq_access_token, no request=) matching
    mopup.tasks.fetch_evaluation_data's exact construction, with the
    force_refresh=True fix already applied — to isolate whether that fix
    actually resolves the live symptom in this exact code path."""

    def get(self, request, program_id):
        from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
        from connect_labs.mopup.core.geometry import fetch_work_area_geometry

        opportunity_id = request.GET.get("opportunity_id")
        if not opportunity_id:
            return JsonResponse({"status": "error", "detail": "opportunity_id is required"}, status=400)
        opportunity_id = int(opportunity_id)

        try:
            access_token = get_valid_access_token(request.user)
        except ConnectTokenError as e:
            return JsonResponse({"status": "error", "detail": f"Authorization needed: {e}"}, status=401)

        # Stage 1: the raw fetcher directly, bypassing AnalysisPipeline/the SQL
        # cache backend entirely — isolates whether Connect's own API genuinely
        # has no data for this endpoint/token, vs. something in the pipeline/
        # cache layer above it dropping to zero.
        try:
            from connect_labs.labs.analysis.backends.sql.connect_export_fetcher import (
                fetch_connect_export_as_visit_dicts,
            )
            from connect_labs.labs.analysis.config import DataSourceConfig

            raw_dicts = fetch_connect_export_as_visit_dicts(
                request=None,
                data_source=DataSourceConfig(type="connect_export", endpoint="work_areas"),
                access_token=access_token,
                opportunity_id=opportunity_id,
            )
            raw_stage = {"count": len(raw_dicts), "sample": raw_dicts[:2]}
        except Exception as e:  # noqa: BLE001 — diagnostic view, surface everything
            raw_stage = {"error": f"{type(e).__name__}: {e}"}

        try:
            from connect_labs.labs.analysis.pipeline import AnalysisPipeline

            # fetch_work_area_geometry's data_source is connect_export, which
            # only ever reads self.access_token (Connect) — no cchq_access_token
            # needed. The real mopup.tasks.fetch_evaluation_data Celery task
            # DOES fetch a CCHQ token upfront too (its OTHER two calls,
            # list_work_areas/list_approved_visits, are cchq-sourced), but
            # this diagnostic isolates the geometry call alone, so a CCHQ
            # re-auth lapse (unrelated to this specific fetch) doesn't block it.
            pipeline = AnalysisPipeline(access_token=access_token, cchq_access_token=None)
            geometry = fetch_work_area_geometry(opportunity_id, pipeline=pipeline)
            sample = list(geometry.items())[:5]
        except Exception as e:  # noqa: BLE001 — diagnostic view, surface everything
            return JsonResponse(
                {"status": "error", "detail": f"{type(e).__name__}: {e}", "raw_fetch_stage": raw_stage}, status=502
            )

        # Stage 3: check whether the write into RawVisitCache actually
        # happened for this (opportunity_id, pipeline_id=12971) despite the
        # pipeline call above returning zero rows — settles a broken WRITE
        # (store never ran / ran with 0 rows) vs. a broken READ (storage is
        # fine, execute_visit_extraction's query/extraction returns nothing).
        try:
            from connect_labs.labs.analysis.backends.sql.models import RawVisitCache

            raw_cache_stage = {
                "db_row_count": RawVisitCache.objects.filter(opportunity_id=opportunity_id, pipeline_id=12971).count(),
            }
        except Exception as e:  # noqa: BLE001 — diagnostic view, surface everything
            raw_cache_stage = {"error": f"{type(e).__name__}: {e}"}

        # Stage 4 (optional): if ward/lga/state are given, exercise the
        # planning-gap diff directly — isolates whether "0 planning-gap work
        # areas added" on a real hand-off is genuine full coverage vs. a
        # silent failure in the best-effort per-ward try/except in
        # handoff.py.
        gap_stage = None
        ward = request.GET.get("ward")
        lga = request.GET.get("lga")
        state = request.GET.get("state")
        if ward and lga and state:
            try:
                from shapely.geometry import shape

                from connect_labs.microplans.core.admin_boundaries import find_ward_boundary_geometry
                from connect_labs.mopup.core.areas import _area_id
                from connect_labs.mopup.core.gaps import buildings_not_covered, work_area_boundaries_for_ward

                existing_boundaries = work_area_boundaries_for_ward(
                    pipeline, opportunity_id, ward, lga, state, request=request
                )
                ward_boundary = find_ward_boundary_geometry(state, lga, ward)
                if ward_boundary is None:
                    gap_stage = {"error": "no ward boundary found"}
                else:
                    all_buildings = buildings_not_covered(shape(ward_boundary), [])
                    remainder = buildings_not_covered(shape(ward_boundary), existing_boundaries)
                    gap_stage = {
                        "area_id": _area_id(state, lga, ward),
                        "existing_wa_boundary_count": len(existing_boundaries),
                        "total_buildings_in_ward": len(all_buildings),
                        "buildings_not_covered_by_any_existing_wa": len(remainder),
                    }
            except Exception as e:  # noqa: BLE001 — diagnostic view, surface everything
                gap_stage = {"error": f"{type(e).__name__}: {e}"}

        return JsonResponse(
            {
                "raw_cache_db_stage": raw_cache_stage,
                "status": "ok",
                "raw_fetch_stage": raw_stage,
                "count": len(geometry),
                "with_boundary": sum(1 for g in geometry.values() if g.get("boundary")),
                "sample": [{"wa_case_id": k, "has_boundary": bool(v.get("boundary"))} for k, v in sample],
                "gap_stage": gap_stage,
            }
        )
