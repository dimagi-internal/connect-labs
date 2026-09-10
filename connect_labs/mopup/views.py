"""Views for the CHC mop-up feature.

Phase 1: opportunity picker (free — from the session's org/program/
opportunity tree, no data pull), ward picker (cheap — work-area case
properties only, see core/work_areas.py), and an optional coarse date range.

Phase 2 (MopupCandidatesView/MopupLockView): the live threshold-tunable
candidate list. The expensive part — pulling a whole opportunity's
work-area/visit/geometry data — runs exactly ONCE per run, in a Celery task
(`mopup.tasks.fetch_evaluation_data`; confirmed necessary this session — a
synchronous web request doing this for a real opportunity gateway-times
out). Every subsequent threshold tweak only re-runs
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
from connect_labs.mopup.core.candidates import (
    build_map_features,
    gap_feature_to_candidate_row,
    gap_summary_by_ward,
    summarize_candidates_by_ward,
)
from connect_labs.mopup.core.data_access import MopupRunDataAccess
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


def _create_plan_result_or_progress(da, run, request, program_id, payload) -> tuple[dict | None, dict | None]:
    """Same dispatch-once/poll pattern as `_rows_or_progress`, for
    `mopup.tasks.create_mopup_plan`. Returns `(resp, None)` once the task
    reaches a terminal state — `resp` is the task's own result dict, already
    carrying its OWN `"status"` ("ok" or, for a caught `HandoffError`,
    "error") — or `(None, progress)` while it's still pending/running.

    Unlike `_rows_or_progress`'s `fetch_task_id` (a run's ONE fetch, ever),
    `create_plan_task_id` is cleared as soon as ANY terminal state is read
    back (success included) — each "Create mop-up plan" click is its own
    attempt, so a later, separate click (e.g. after navigating back to a
    still-locked run) must dispatch a genuinely new task, not replay a
    finished one."""
    from celery.result import AsyncResult

    from connect_labs.labs.analysis.sse_streaming import build_task_progress
    from connect_labs.mopup.tasks import create_mopup_plan

    task_id = run.create_plan_task_id
    if not task_id:
        result = create_mopup_plan.delay(
            program_id,
            run.id,
            request.user.id,
            grouping=payload.get("grouping"),
            group_id=payload.get("group_id"),
        )
        da.update_run(run, create_plan_task_id=result.id)
        return None, build_task_progress("PENDING", None)

    task = AsyncResult(task_id)
    progress = build_task_progress(task.state, task.info)

    if progress["status"] == "completed":
        da.update_run(run, create_plan_task_id=None)
        return progress["result"], None
    if progress["status"] == "failed":
        da.update_run(run, create_plan_task_id=None)
        return None, progress
    return None, progress


def _planning_gaps_result_or_progress(da, run, request, program_id, payload) -> tuple[dict | None, dict | None]:
    """Same dispatch-once/poll pattern as `_create_plan_result_or_progress`,
    for `mopup.tasks.preview_planning_gaps`. On a completed run, the result
    is PERSISTED onto the run (`planning_gap_features`/`planning_gap_config`/
    `planning_gap_warnings`) before being returned — Step 2 has no separate
    "lock" gesture; whatever the latest successful Recompute produced is
    what Phase 3's hand-off carries forward. Re-running Step 2 with
    different settings simply overwrites what's stored."""
    from celery.result import AsyncResult

    from connect_labs.labs.analysis.sse_streaming import build_task_progress
    from connect_labs.mopup.tasks import preview_planning_gaps

    task_id = run.planning_gap_task_id
    if not task_id:
        mode = payload.get("mode") or "overture"
        result = preview_planning_gaps.delay(
            program_id,
            run.id,
            request.user.id,
            mode=mode,
            building_sources=payload.get("building_sources"),
            min_confidence=payload.get("min_confidence"),
            min_buildings_per_cell=int(payload.get("min_buildings_per_cell") or 1),
            cell_size_m=float(payload.get("cell_size_m") or 100.0),
        )
        da.update_run(run, planning_gap_task_id=result.id)
        return None, build_task_progress("PENDING", None)

    task = AsyncResult(task_id)
    progress = build_task_progress(task.state, task.info)

    if progress["status"] == "completed":
        resp = progress["result"]
        da.update_run(
            run,
            planning_gap_task_id=None,
            planning_gap_features=resp.get("features", []),
            planning_gap_config=resp.get("config", {}),
            planning_gap_warnings=resp.get("warnings", {}),
            planning_gap_building_points=resp.get("building_points", []),
        )
        return resp, None
    if progress["status"] == "failed":
        da.update_run(run, planning_gap_task_id=None)
        return None, progress
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
        from django.conf import settings
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
        context["planning_gaps_url"] = reverse("mopup:planning_gaps", args=[program_id, run_id])
        context["upload_buildings_url"] = reverse("mopup:upload_buildings", args=[program_id, run_id])
        context["planning_gap_config"] = run.planning_gap_config
        context["uploaded_buildings_filename"] = run.uploaded_buildings_filename
        context["uploaded_buildings_row_count"] = run.uploaded_buildings_row_count
        context["indicator_configs"] = run.thresholds.get("indicator_configs") or ind.DEFAULT_INDICATOR_CONFIGS
        # Merge, not replace: a run whose thresholds were saved before a
        # global_config key was introduced (every run predating this schema)
        # would otherwise render that setting's input blank instead of its
        # default, and Recompute would then collect that blank as 0/false —
        # silently different from what a fresh run gets. evaluate_run already
        # self-heals this for the CALCULATION (same merge pattern), but nothing
        # upstream did it for what the template actually renders into the form.
        context["global_config"] = {**ind.DEFAULT_GLOBAL_CONFIG, **(run.thresholds.get("global_config") or {})}
        context["indicator_defs"] = [
            {"key": ind.EVC_SHORTFALL, "label": "EVC shortfall", "tier": 1},
            {"key": ind.NCF_INACCESSIBLE, "label": "NCF / inaccessible", "tier": 1},
            {"key": ind.DEWORMING, "label": "Deworming completion", "tier": 2},
            {"key": ind.MUAC, "label": "MUAC-recorded rate", "tier": 2},
            {"key": ind.VACCINATION, "label": "Vaccination-given rate", "tier": 2},
        ]
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        ward_boundaries, boundary_source_caption = self._ward_boundaries_geojson(run.selected_wards)
        context["ward_boundaries"] = ward_boundaries
        context["boundary_source_caption"] = boundary_source_caption
        return context

    @staticmethod
    def _ward_boundaries_geojson(selected_wards: list[dict]) -> tuple[dict, str | None]:
        """Static, fetched once at page load (mopup's wards are fixed by
        Phase 1's picker, not viewport-panned like microplans' own admin
        boundary layer) — one Feature per selected ward, skipping any that
        don't resolve. Empty `selected_wards` (no wards, i.e. "every ward in
        the opportunity") intentionally yields no boundaries — resolving
        every ward's boundary just for the map isn't worth the cost.

        Also returns a caption naming which boundary source(s) actually
        resolved (`None` if nothing did) — Connect's own API doesn't yet
        expose a ward-level boundary at all (only per-work-area polygons),
        so every ward shape shown here comes from a name-matched public/
        curated source (`microplans.core.admin_boundaries`'s labs/Overture
        resolver), which can disagree with what's actually uploaded in
        Connect for that ward. Surfacing the real source is a deliberately
        small, paused-scope fix — see the plan this shipped under for why
        a deeper fix (an upload override, or deriving from existing work
        areas) is on hold pending a Connect API change."""
        from connect_labs.microplans.core.admin_boundaries import SOURCE_LABELS, find_ward_boundary

        features = []
        sources_seen: set[str] = set()
        for sw in selected_wards:
            boundary = find_ward_boundary(sw.get("state", ""), sw.get("lga", ""), sw.get("ward", ""))
            if boundary is None or boundary.geometry is None:
                continue
            sources_seen.add(boundary.source)
            features.append(
                {
                    "type": "Feature",
                    "geometry": json.loads(boundary.geometry.geojson),
                    "properties": {
                        "ward": sw.get("ward", ""),
                        "lga": sw.get("lga", ""),
                        "state": sw.get("state", ""),
                        "source": boundary.source,
                    },
                }
            )
        caption = None
        if sources_seen:
            labels = sorted(SOURCE_LABELS.get(s, s) for s in sources_seen)
            caption = f"Boundary source: {', '.join(labels)} — pending native Connect boundary support."
        return {"type": "FeatureCollection", "features": features}, caption


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

        # Per-indicator breakdown of the union candidate count above — how
        # many work areas each individual indicator flagged, recomputed every
        # call the same as everything else here (no separate cache/staleness
        # risk since it's a cheap pass over the already-built candidate list).
        per_indicator_counts = {key: 0 for key in ind.ALL_INDICATORS}
        for c in candidates:
            for key in c["triggered_indicators"]:
                per_indicator_counts[key] = per_indicator_counts.get(key, 0) + 1

        gap_features = run.planning_gap_features
        gap_candidates = [gap_feature_to_candidate_row(f) for f in gap_features]

        return JsonResponse(
            {
                "status": "ok",
                "candidates": candidates,
                "gap_candidates": gap_candidates,
                "ward_summary": ward_summary,
                "gap_summary_by_ward": gap_summary_by_ward(gap_features),
                "total_work_areas": len(rows),
                "candidate_count": len(candidates),
                "per_indicator_counts": per_indicator_counts,
                "map_features": build_map_features(rows, candidates, gap_features, run.planning_gap_building_points),
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
    """Phase 3: hand a locked run's candidate set (plus whatever planning-gap
    features Phase 2's Step 2 already computed — `run.planning_gap_features`)
    to the existing microplans coverage engine. Only acts on
    `run.candidate_work_areas`/`run.planning_gap_features` (both frozen
    before this point) — never re-reads live thresholds or recomputes gaps.

    Offloaded to Celery (`mopup.tasks.create_mopup_plan`) the same way
    Phase 2's data pull is — a real hand-off can still be slow with several
    wards' candidates at once, even though the expensive building-fetch part
    moved to Step 2. See `_create_plan_result_or_progress` for the
    dispatch/poll mechanics, and core/handoff.py for the actual microplans
    calls."""

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

        resp, progress = _create_plan_result_or_progress(da, run, request, program_id, payload)
        if resp is None:
            return JsonResponse(progress)
        return JsonResponse(resp)


class MopupPlanningGapsView(LoginRequiredMixin, View):
    """Phase 2 Step 2 (locked runs only): compute — and persist onto the
    run — the planning-gap work areas for every distinct ward among the
    locked candidates, per `mopup.tasks.preview_planning_gaps`. Each
    Recompute click here is its own attempt: whatever the LATEST successful
    one produced is what `MopupCreatePlanView`/`core.handoff` carries
    forward at hand-off, with no separate lock step of its own. See
    `_planning_gaps_result_or_progress` for the dispatch/poll mechanics."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED:
            return JsonResponse(
                {"status": "error", "detail": "Lock the run before previewing planning gaps."}, status=400
            )

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        if (payload.get("mode") or "overture") == "upload" and not run.uploaded_buildings_csv:
            return JsonResponse(
                {"status": "error", "detail": "Upload a building-data file before recomputing."}, status=400
            )

        resp, progress = _planning_gaps_result_or_progress(da, run, request, program_id, payload)
        if resp is None:
            return JsonResponse(progress)
        return JsonResponse(resp)


_MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # the confirmed real sample was ~28MB
_MAX_UPLOAD_ROWS = 50_000  # after shrinking to this run's own ward(s) -- see the view's docstring


class MopupUploadBuildingsView(LoginRequiredMixin, View):
    """Phase 2 Step 2's "upload your own building data" mode (locked runs
    only): accepts a multipart CSV, validates it's a plausible building
    file (extension, size, required columns), shrinks it down to just this
    run's own locked ward(s) (`core.gaps.filter_upload_to_wards` — a
    reviewer's file is allowed to cover more ground than this run reviews,
    e.g. a multi-ward source file, but only the reviewed ward(s) are worth
    keeping), and stores the result as CSV text directly on the run
    (`MopupRunRecord.uploaded_buildings_csv`) — NOT Django's file storage/S3
    (`default_storage`), which has no working bucket/IAM wiring in labs
    (confirmed live: every upload attempt through that path 500'd). This
    mirrors how `microplans`' own "upload your own boundary" feature
    persists an upload directly on its record instead of touching file
    storage at all.

    Re-uploading simply overwrites what's stored — same "latest wins, no
    separate lock" convention Step 2 already has for its Overture-mode
    settings. `_MAX_UPLOAD_ROWS` is a safety net on the POST-shrink row
    count, not the raw file — real usage reviews a handful of wards at
    most, so this should essentially never trigger; if it does, the fix is
    a smaller/more targeted upload, not a bigger cap."""

    def post(self, request, program_id, run_id):
        from connect_labs.mopup.core.areas import carry_forward_features, distinct_wards
        from connect_labs.mopup.core.gaps import filter_upload_to_wards

        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED:
            return JsonResponse(
                {"status": "error", "detail": "Lock the run before uploading building data."}, status=400
            )

        upload = request.FILES.get("file")
        if upload is None:
            return JsonResponse({"status": "error", "detail": "No file provided."}, status=400)
        if not upload.name.lower().endswith(".csv"):
            return JsonResponse({"status": "error", "detail": "File must be a CSV (.csv)."}, status=400)
        if upload.size > _MAX_UPLOAD_BYTES:
            return JsonResponse(
                {"status": "error", "detail": f"File is too large (max {_MAX_UPLOAD_BYTES // (1024 * 1024)}MB)."},
                status=400,
            )

        import pandas as pd

        try:
            df = pd.read_csv(upload)
        except (UnicodeDecodeError, pd.errors.ParserError) as e:
            return JsonResponse({"status": "error", "detail": f"Could not read this as a CSV: {e}"}, status=400)

        with_geometry = [c for c in run.candidate_work_areas if c.get("boundary")]
        wards = distinct_wards(carry_forward_features(with_geometry))

        try:
            filtered = filter_upload_to_wards(df, wards)
        except KeyError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)

        if filtered.empty:
            ward_names = ", ".join(sorted({w["ward"] for w in wards})) or "(none)"
            return JsonResponse(
                {
                    "status": "error",
                    "detail": f"No rows in this file match this run's ward(s): {ward_names}.",
                },
                status=400,
            )
        if len(filtered) > _MAX_UPLOAD_ROWS:
            return JsonResponse(
                {
                    "status": "error",
                    "detail": (
                        f"{len(filtered)} rows matched this run's ward(s) — over the {_MAX_UPLOAD_ROWS:,} limit. "
                        "Upload a file scoped to just the ward(s) being reviewed in this round."
                    ),
                },
                status=400,
            )

        da.update_run(
            run,
            uploaded_buildings_csv=filtered.to_csv(index=False),
            uploaded_buildings_filename=upload.name,
            uploaded_buildings_row_count=len(filtered),
        )

        return JsonResponse({"status": "ok", "filename": upload.name, "matched_rows": len(filtered)})
