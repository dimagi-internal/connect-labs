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
    filter_gap_features,
    gap_feature_to_candidate_row,
    gap_summary_by_ward,
    summarize_candidates_by_ward,
)
from connect_labs.mopup.core.data_access import MopupRunDataAccess, MopupRunNotFoundError
from connect_labs.mopup.core.isolation import isolated_work_area_ids
from connect_labs.mopup.core.models import STATUS_LOCKED
from connect_labs.mopup.core.work_areas import fetch_connect_implementation_areas, list_work_areas, summarize_wards

logger = logging.getLogger(__name__)


def _merged_indicator_configs(raw: dict | None) -> dict:
    """Every CURRENT indicator key (`ind.DEFAULT_INDICATOR_CONFIGS`), each
    defaulted then overridden per-key by whatever `raw` (a payload's or a
    run's saved `indicator_configs`) provides for that same key — never a
    whole-dict swap, and never a key `raw` supplies but today's indicator set
    doesn't recognize.

    Real bug, caught live against run 20923 right after the NCF/inaccessible
    split (#1899) shipped: `MopupAnalysisView`'s old `saved or default`
    fallback (no merge) meant a run saved before the split still had
    `ncf_inaccessible_rate` as one whole key in its persisted
    `indicator_configs`, with no `threshold` field (that indicator never had
    one). `evaluate_run` now treats any key it doesn't recognize as
    presence-only (`NCF`/`INACCESSIBLE`) as a RATE indicator and reads
    `ind_cfg["threshold"]` unconditionally — a `KeyError` on that stale key,
    500ing the very first Recompute on every pre-existing run, every time,
    until its saved thresholds happened to get overwritten. Filtering to only
    `DEFAULT_INDICATOR_CONFIGS`' keys here means a legacy save can never
    reintroduce a since-removed/renamed key, and any current key it's silent
    on (like `ncf`/`inaccessible` for a run that predates them) just gets
    today's default — the same self-healing `global_config` already got via
    its own `{**DEFAULT, **saved}` merge one level up, just done per-key
    instead of whole-dict since a per-key shape actually changed here."""
    raw = raw or {}
    return {key: {**ind.DEFAULT_INDICATOR_CONFIGS[key], **raw.get(key, {})} for key in ind.DEFAULT_INDICATOR_CONFIGS}


def _resolve_thresholds(run, payload: dict) -> tuple[dict, dict]:
    """Given a request payload, the run's saved thresholds, and the built-in
    defaults, resolve which indicator/global config to evaluate with — in
    that priority order."""
    indicator_configs = _merged_indicator_configs(
        payload.get("indicator_configs") or run.thresholds.get("indicator_configs")
    )
    global_config = (
        payload.get("global_config") or run.thresholds.get("global_config") or dict(ind.DEFAULT_GLOBAL_CONFIG)
    )
    return indicator_configs, global_config


def _apply_exclusions(candidates: list[dict], run) -> list[dict]:
    """Drops any candidate whose wa_id is in `run.excluded_wa_ids` (the map
    view's "Not include" action) — called right after `evaluate_run` in
    both `MopupCandidatesView` (the live view) and `MopupLockView` (so an
    exclusion made before locking is already baked into the frozen
    `candidate_work_areas`; nothing downstream of locking needs its own
    separate exclusion check). Real work areas only — see
    `candidates.filter_gap_features` for the planning-gap-cell sibling of
    this, applied separately since gap features are GeoJSON Features, not
    candidate dicts."""
    excluded = set(run.excluded_wa_ids)
    if not excluded:
        return candidates
    return [c for c in candidates if c["wa_id"] not in excluded]


def _active_combined_rows(run) -> list[dict]:
    """Every work area currently shown in the candidate table AND the ward
    summary — Step 1's locked candidates plus Step 2's gap-fill cells
    (adapted to the same candidate-row shape via
    `candidates.gap_feature_to_candidate_row`), both already stripped of
    anything in `run.excluded_wa_ids`. This is Step 3's own input: the
    isolation filter only ever considers work areas a reviewer would
    actually see and could actually be sent to, same as the live view."""
    existing = _apply_exclusions(run.candidate_work_areas, run)
    gap_rows = [
        gap_feature_to_candidate_row(f) for f in filter_gap_features(run.planning_gap_features, run.excluded_wa_ids)
    ]
    return existing + gap_rows


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
        context["delete_runs_url"] = reverse("mopup:delete_runs", args=[program_id])
        return context


class MopupDeleteRunsView(LoginRequiredMixin, View):
    """Phase 1's "Delete selected" action — a real, unrecoverable hard
    delete (see `MopupRunDataAccess.delete_run`'s docstring for why a
    single `delete_record` call is a complete cleanup: nothing about a run
    lives anywhere except its own JSON record).

    Accepts a JSON body `{"run_ids": [...]}`. Each id is deleted
    independently — one bad id (already deleted, or belonging to another
    program) doesn't block the rest, since a multi-select delete should
    succeed for everything it validly can. Returns which ids were actually
    deleted vs. not found, so the caller can report a partial failure
    rather than silently drop it."""

    def post(self, request, program_id):
        da = MopupRunDataAccess(program_id, request=request)
        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        run_ids = payload.get("run_ids")
        if not isinstance(run_ids, list) or not run_ids:
            return JsonResponse({"status": "error", "detail": "run_ids must be a non-empty list."}, status=400)

        deleted, not_found = [], []
        for run_id in run_ids:
            try:
                da.delete_run(run_id)
                deleted.append(run_id)
            except (MopupRunNotFoundError, TypeError, ValueError):
                not_found.append(run_id)

        return JsonResponse({"status": "ok", "deleted": deleted, "not_found": not_found})


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
        context["erase_planning_gaps_url"] = reverse("mopup:erase_planning_gaps", args=[program_id, run_id])
        context["lock_planning_gaps_url"] = reverse("mopup:lock_planning_gaps", args=[program_id, run_id])
        context["isolation_preview_url"] = reverse("mopup:isolation_preview", args=[program_id, run_id])
        context["lock_isolation_filter_url"] = reverse("mopup:lock_isolation_filter", args=[program_id, run_id])
        context["upload_buildings_url"] = reverse("mopup:upload_buildings", args=[program_id, run_id])
        context["exclude_work_area_url"] = reverse("mopup:exclude_work_area", args=[program_id, run_id])
        context["planning_gap_config"] = run.planning_gap_config
        context["planning_gaps_locked"] = run.planning_gaps_locked
        context["isolation_filter_locked"] = run.isolation_filter_locked
        context["isolation_threshold_m"] = run.isolation_threshold_m
        context["uploaded_buildings_filename"] = run.uploaded_buildings_filename
        context["uploaded_buildings_row_count"] = run.uploaded_buildings_row_count
        # Merge, not replace: a run whose thresholds were saved before a
        # global_config key was introduced (every run predating this schema)
        # would otherwise render that setting's input blank instead of its
        # default, and Recompute would then collect that blank as 0/false —
        # silently different from what a fresh run gets. `_resolve_thresholds`
        # already self-heals this for the CALCULATION (same merge pattern),
        # but nothing upstream did it for what the template actually renders
        # into the form. `indicator_configs` needs the same treatment
        # per-key, not just per-whole-dict (see `_merged_indicator_configs`'s
        # docstring for the real 500 this caused before this fix).
        context["indicator_configs"] = _merged_indicator_configs(run.thresholds.get("indicator_configs"))
        context["global_config"] = {**ind.DEFAULT_GLOBAL_CONFIG, **(run.thresholds.get("global_config") or {})}
        context["indicator_defs"] = [
            {"key": ind.EVC_SHORTFALL, "label": "EVC shortfall", "tier": 1},
            {"key": ind.NCF, "label": "NCF", "tier": 1},
            {"key": ind.INACCESSIBLE, "label": "Inaccessible", "tier": 1},
            {"key": ind.DEWORMING, "label": "Deworming completion", "tier": 2},
            {"key": ind.MUAC, "label": "MUAC-recorded rate", "tier": 2},
            {"key": ind.VACCINATION, "label": "Vaccination-given rate", "tier": 2},
        ]
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        access_token = self.request.session.get("labs_oauth", {}).get("access_token")
        ward_boundaries, boundary_source_caption = self._ward_boundaries_geojson(
            run.selected_wards, run.target_opportunity_id, access_token
        )
        context["ward_boundaries"] = ward_boundaries
        context["boundary_source_caption"] = boundary_source_caption
        return context

    @staticmethod
    def _ward_boundaries_geojson(
        selected_wards: list[dict], opportunity_id: int | None, access_token: str | None
    ) -> tuple[dict, str | None]:
        """Static, fetched once at page load (mopup's wards are fixed by
        Phase 1's picker, not viewport-panned like microplans' own admin
        boundary layer) — one Feature per selected ward, skipping any that
        don't resolve. Empty `selected_wards` (no wards, i.e. "every ward in
        the opportunity") intentionally yields no boundaries — resolving
        every ward's boundary just for the map isn't worth the cost.

        Tries the opportunity's own Connect-native Implementation Area
        boundary first (ground truth — what this opportunity's microplanning
        was actually built against; live since commcare-connect#1517,
        2026-09-10), matching by name (labs itself writes
        `implementation_area = ward` on upload, so this is normally an exact
        match). Falls back to the third-party name-matched resolver
        (`microplans.core.admin_boundaries`'s labs/Overture blend) for any
        ward the Connect-native set doesn't cover — an opportunity that never
        uploaded Implementation Areas, or with no `access_token` available,
        falls back for every ward, unchanged from before this endpoint
        existed.

        Also returns a caption naming which boundary source(s) actually
        resolved (`None` if nothing did).

        The actual Connect-native-preferred matching is
        `core.work_areas.resolve_ward_boundaries` — shared with
        `mopup.tasks.preview_planning_gaps`, so Step 2's building fetch/
        gridding boundary always agrees with the boundary drawn here (see
        that function's docstring for why this matters)."""
        from connect_labs.microplans.core.admin_boundaries import SOURCE_LABELS
        from connect_labs.mopup.core.work_areas import resolve_ward_boundaries

        connect_areas = (
            fetch_connect_implementation_areas(opportunity_id, access_token) if opportunity_id and access_token else []
        )
        resolved = resolve_ward_boundaries(selected_wards, connect_areas)

        features = []
        sources_seen: set[str] = set()
        for sw in selected_wards:
            match = resolved.get(sw.get("ward", ""))
            if match is None:
                continue
            sources_seen.add(match["source"])
            features.append(
                {
                    "type": "Feature",
                    "geometry": match["geometry"],
                    "properties": {
                        "ward": sw.get("ward", ""),
                        "lga": sw.get("lga", ""),
                        "state": sw.get("state", ""),
                        "source": match["source"],
                    },
                }
            )
        caption = None
        if sources_seen:
            labels_map = {**SOURCE_LABELS, "connect": "Connect (native Implementation Area for this opportunity)"}
            labels = sorted(labels_map.get(s, s) for s in sources_seen)
            if sources_seen - {"connect"}:
                caption = (
                    f"Boundary source: {', '.join(labels)} — some ward(s) fell back to third-party "
                    "matching, pending a Connect Implementation Area upload for those wards."
                )
            else:
                caption = f"Boundary source: {', '.join(labels)}."
        return {"type": "FeatureCollection", "features": features}, caption


class MopupCandidatesView(LoginRequiredMixin, View):
    """Phase 2's live recompute: evaluate the run's scoped work areas against
    the given (or run-saved, or default) indicator/global config and return
    candidates + a per-ward summary. A POST only persists the thresholds
    used onto the run when they actually changed, so reopening it resumes
    where the reviewer left off — skipped when they match what's already
    stored (see the comparison below) since `update_run` re-uploads the
    run's ENTIRE JSON blob to Connect's production LabsRecord API (there's
    no partial-field write at that layer), not just the changed key.
    Confirmed live: this run's own `planning_gap_features`/
    `planning_gap_building_points` alone (regularly hundreds of features/
    thousands of points once Step 2 has run) make that a genuinely
    non-trivial payload to re-send on every call — real cost for something
    that's a no-op the majority of the time a "Recompute" happens, e.g. the
    auto-recompute triggered right after excluding a work area on the map,
    which never touches thresholds at all.

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
        candidates = _apply_exclusions(ind.evaluate_run(rows, indicator_configs, global_config), run)
        ward_summary = summarize_candidates_by_ward(candidates, rows)
        new_thresholds = {"indicator_configs": indicator_configs, "global_config": global_config}
        if new_thresholds != run.thresholds:
            da.update_run(run, thresholds=new_thresholds)

        # Per-indicator breakdown of the union candidate count above — how
        # many work areas each individual indicator flagged, recomputed every
        # call the same as everything else here (no separate cache/staleness
        # risk since it's a cheap pass over the already-built candidate list).
        per_indicator_counts = {key: 0 for key in ind.ALL_INDICATORS}
        for c in candidates:
            for key in c["triggered_indicators"]:
                per_indicator_counts[key] = per_indicator_counts.get(key, 0) + 1

        gap_features = filter_gap_features(run.planning_gap_features, run.excluded_wa_ids)
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
        candidates = _apply_exclusions(ind.evaluate_run(rows, indicator_configs, global_config), run)

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
        if not run.isolation_filter_locked:
            return JsonResponse(
                {"status": "error", "detail": "Lock in Step 3 (remove isolated work areas) before creating a plan."},
                status=400,
            )

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
        if run.planning_gaps_locked:
            return JsonResponse(
                {"status": "error", "detail": "Step 2 is locked in — nothing left to recompute."}, status=400
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


class MopupErasePlanningGapsView(LoginRequiredMixin, View):
    """Step 2's "Erase planning gaps" action: deletes every planning-gap work
    area this run has computed, without touching Step 1's own candidate set
    or thresholds. Clears `planning_gap_features` (the work areas
    themselves), `planning_gap_building_points` (their supporting building
    dots on the map), and `planning_gap_warnings` (per-ward failures from
    that same computation) — all three are downstream artifacts of the same
    Step 2 Recompute, so stale points/warnings left behind after the
    features are gone would be inconsistent with what's actually on the run.

    Deliberately leaves `planning_gap_config` (the last-used mode/settings)
    alone — this only clears the RESULT of a Step 2 run, not the reviewer's
    form inputs, so hitting Recompute again after erasing starts from
    whatever they had last configured rather than resetting the form too.

    Same "no separate lock" shape as `MopupPlanningGapsView`: nothing else on
    the run needs updating, and the caller (analysis.js) triggers a normal
    Recompute right after a successful response to pick up the now-smaller
    candidate/gap-candidate lists and ward summary, same as every other
    Step 2 setting change."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED:
            return JsonResponse(
                {"status": "error", "detail": "Lock the run before erasing planning gaps."}, status=400
            )
        if run.planning_gaps_locked:
            return JsonResponse(
                {"status": "error", "detail": "Step 2 is locked in — nothing left to erase."}, status=400
            )

        da.update_run(
            run,
            planning_gap_features=[],
            planning_gap_building_points=[],
            planning_gap_warnings={},
        )

        return JsonResponse({"status": "ok"})


class MopupLockPlanningGapsView(LoginRequiredMixin, View):
    """Step 2's own "Lock in Step 2" action: freezes whatever
    `planning_gap_features` the latest successful Recompute (or "skip")
    produced, and unlocks Step 3 (the isolation filter, which needs a
    stable combined candidate+gap-fill set to compute distances over — see
    `core.isolation.isolated_work_area_ids`). Sets `planning_gaps_locked`
    only; doesn't touch `planning_gap_features` itself (nothing to
    recompute — the same "latest Recompute wins" data is what locks in).
    One-way, same as Step 1's own `MopupLockView` — no unlock endpoint."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED:
            return JsonResponse(
                {"status": "error", "detail": "Lock the run (Step 1) before locking Step 2."}, status=400
            )

        da.update_run(run, planning_gaps_locked=True)

        return JsonResponse({"status": "ok"})


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


class MopupExcludeWorkAreaView(LoginRequiredMixin, View):
    """The map view's "Exclude WAs" action (item 4): manually excludes (or
    re-includes) one or more work areas AND/OR planning-gap cells from this
    run's candidate set, regardless of what the indicator thresholds would
    otherwise flag. Works identically for both since each id in `wa_ids` is
    whatever the map/table already used to identify that row — a real
    CommCare wa_id, or a gap cell's own `"{area_id}-gap-{cluster}"` id — and
    `run.excluded_wa_ids` doesn't distinguish between the two shapes.

    Takes a LIST (`wa_ids`), not one id at a time, specifically so the map's
    multi-select (shift/cmd/ctrl-click several, then one "Exclude WAs" click)
    is a single `update_run` call, not N of them — `update_run` re-uploads the
    run's entire JSON blob to Connect's production LabsRecord API (see
    `MopupCandidatesView`'s own docstring for why that's expensive), so
    looping this endpoint N times would reintroduce exactly the anti-pattern
    already fixed there.

    Persists onto `run.excluded_wa_ids` and returns immediately — it does
    NOT itself re-evaluate/return updated candidates/ward_summary/map_features.
    The caller (analysis.js) triggers a normal Recompute right after a
    successful response, which already applies `_apply_exclusions`/
    `filter_gap_features` (see `MopupCandidatesView`) and re-renders
    everything from that one response, same as any other setting change.
    Keeping this endpoint single-purpose avoids duplicating that rendering
    path here."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        wa_ids = payload.get("wa_ids")
        if not wa_ids or not isinstance(wa_ids, list):
            return JsonResponse({"status": "error", "detail": "wa_ids (a non-empty list) is required."}, status=400)
        excluded = bool(payload.get("excluded", True))

        current = set(run.excluded_wa_ids)
        if excluded:
            current.update(wa_ids)
        else:
            current.difference_update(wa_ids)
        da.update_run(run, excluded_wa_ids=sorted(current))

        return JsonResponse({"status": "ok", "excluded_wa_ids": sorted(current)})


class MopupIsolationPreviewView(LoginRequiredMixin, View):
    """Step 3's live preview (locked runs, Step 2 already locked in, only):
    given a candidate distance, returns which currently-active work areas
    (Step 1 candidates + Step 2 gap-fill cells, both already stripped of
    anything in `excluded_wa_ids` — see `_active_combined_rows`) have no
    OTHER active work area within that distance of their own centroid. Does
    NOT exclude anything itself — purely a preview so the reviewer can tune
    the threshold and see the effect (table highlighting, ward counts, map
    hatch) before committing via `MopupLockIsolationFilterView`. Per-ward
    counts/table highlighting are derived client-side from the returned id
    list against the already-rendered candidate/gap rows, so this only ever
    returns the ids themselves.

    Persists `isolation_threshold_m` only when it actually changed (same
    anti-pattern-avoidance `MopupCandidatesView` already uses for
    `thresholds` — `update_run` re-uploads the run's entire JSON blob)."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED or not run.planning_gaps_locked:
            return JsonResponse({"status": "error", "detail": "Lock in Step 2 before previewing Step 3."}, status=400)

        try:
            payload = json.loads(request.body) if request.body else {}
            distance_m = float(payload["distance_m"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        if distance_m <= 0:
            return JsonResponse({"status": "error", "detail": "distance_m must be positive."}, status=400)

        if distance_m != run.isolation_threshold_m:
            da.update_run(run, isolation_threshold_m=distance_m)

        isolated = isolated_work_area_ids(_active_combined_rows(run), distance_m)

        return JsonResponse({"status": "ok", "isolated_wa_ids": sorted(isolated)})


class MopupLockIsolationFilterView(LoginRequiredMixin, View):
    """Step 3's own "Lock in Step 3" action — the actual commit. Requires
    Step 2 to already be locked in (same gate `MopupIsolationPreviewView`
    uses). Re-computes the isolated set SERVER-SIDE from the given
    `distance_m` (never trusts a client-supplied id list for a destructive
    action — avoids staleness between what was last previewed and what
    actually gets excluded), unions it into `run.excluded_wa_ids` — the
    SAME field/mechanism the map's own "Exclude WAs" action already uses,
    per product direction: a work area Step 3 removes is excluded exactly
    like one a reviewer excludes by hand, so the candidate table/ward
    summary/map already stop showing it for free, and Phase 3's hand-off
    already honors `excluded_wa_ids` for both real and gap-fill work areas
    (see `_apply_exclusions`/`candidates.filter_gap_features`) — and sets
    `isolation_filter_locked=True` + the committed `isolation_threshold_m`,
    all in ONE `update_run` call (same batching discipline
    `MopupExcludeWorkAreaView` already uses for a multi-id exclude).
    One-way, same as Step 1/Step 2's locks — no unlock endpoint."""

    def post(self, request, program_id, run_id):
        da = MopupRunDataAccess(program_id, request=request)
        run = da.get_run(run_id)
        if run is None:
            return JsonResponse({"status": "error", "detail": "Run not found."}, status=404)
        if run.status != STATUS_LOCKED or not run.planning_gaps_locked:
            return JsonResponse({"status": "error", "detail": "Lock in Step 2 before locking Step 3."}, status=400)

        try:
            payload = json.loads(request.body) if request.body else {}
            distance_m = float(payload["distance_m"])
        except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        if distance_m <= 0:
            return JsonResponse({"status": "error", "detail": "distance_m must be positive."}, status=400)

        isolated = isolated_work_area_ids(_active_combined_rows(run), distance_m)
        current_excluded = set(run.excluded_wa_ids)
        current_excluded.update(isolated)

        da.update_run(
            run,
            excluded_wa_ids=sorted(current_excluded),
            isolation_filter_locked=True,
            isolation_threshold_m=distance_m,
        )

        return JsonResponse({"status": "ok", "excluded_count": len(isolated)})
