"""Views for microplans: stateless preview/boundary/service-delivery utilities +
the program-scoped plan portfolio (create, review/edit, compare, groups). The
legacy opportunity-scoped setup/plan flow was removed once the program flow
superseded it."""

import csv
import json
import logging
from urllib.parse import urlencode

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404, HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from connect_labs.microplans import serialization
from connect_labs.microplans.view_helpers import (
    _adm1_state_for,
    _enqueue_plan_mutation,
    _filter_demo_junk_opps,
    _float_or_none,
    _plan_scoped_urls,
    _program_map_seed,
    _queued,
    _sd_urls,
)

logger = logging.getLogger(__name__)


class _ProgramScopeMixin:
    """Refuse a labs-only program (or opportunity) in the URL that this user may
    not use, before the view runs.

    A labs-only program's plans live in the labs DB with no Connect membership
    check behind them, so this is that check (``microplans.access``). Every view
    whose URL carries ``program_id`` or ``opp_id`` uses this mixin -- a test walks
    the URLconf to keep it that way. Real programs and opportunities pass straight
    through: Connect enforces those per request, as before.

    Answers 404, never 403, so a refusal does not confirm the program exists --
    except on the pages of a workspace program (``labs.workspaces``), which tell a
    refused person why, since the usual cause there is a Connect organisation
    that is not (yet) part of the program.

    In a workspace program it also refuses a Network Manager a ``plan_id`` or
    ``group_id`` that belongs to another team -- the same 404 as a record that is
    not there. That covers the views that hand a plan to a Celery task without
    reading it first (regroup / reassign / regenerate / group generate).
    """

    # The labs context picker follows the URL's program/opportunity on pages
    # (see _LabsContextSyncMixin); JSON endpoints leave the session alone.
    sync_labs_context = False

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            refusal = self._refuse_denied_scope(request, kwargs) or self._refuse_other_teams_record(request, kwargs)
            if refusal is not None:
                return refusal
            if self.sync_labs_context:
                self._sync_labs_context(request, kwargs)
        return super().dispatch(request, *args, **kwargs)

    def _refuse_denied_scope(self, request, kwargs):
        from connect_labs.microplans.access import record_denial, scope_denied_reason

        scope = {"program_id": kwargs.get("program_id"), "opportunity_id": kwargs.get("opp_id")}
        denied = scope_denied_reason(request.user, **scope)
        if not denied:
            return None
        record_denial(reason=denied, user=request.user, **scope)
        if isinstance(self, TemplateView):
            from connect_labs.labs.workspaces import workspace_for_program

            if workspace_for_program(scope["program_id"]) is not None:
                return _workspace_refused(request)
            raise Http404("Not found.")
        return JsonResponse({"status": "error", "detail": "Not found."}, status=404)

    def _refuse_other_teams_record(self, request, kwargs):
        if "program_id" not in kwargs or not ({"plan_id", "group_id"} & set(kwargs)):
            return None
        from connect_labs.labs.workspaces import workspace_for_program

        if workspace_for_program(kwargs["program_id"]) is None:
            return None
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        try:
            da = ProgramPlanDataAccess(kwargs["program_id"], request=request)
        except ValueError:  # no Connect token in the session: the view answers that itself
            return None
        if not da.restricted_to_teams:
            return None
        hidden = ("plan_id" in kwargs and da.get_plan(int(kwargs["plan_id"])) is None) or (
            "group_id" in kwargs and da.get_group(int(kwargs["group_id"])) is None
        )
        if not hidden:
            return None
        if isinstance(self, TemplateView):
            raise Http404("Not found.")
        return JsonResponse({"status": "error", "detail": "Not found."}, status=404)

    def _sync_labs_context(self, request, kwargs):
        from connect_labs.labs.context import save_context_to_session, validate_context_access

        ctx = {}
        if "program_id" in kwargs:
            ctx["program_id"] = int(kwargs["program_id"])
        elif "opp_id" in kwargs:
            ctx["opportunity_id"] = int(kwargs["opp_id"])
        if not ctx:
            return
        validated = validate_context_access(request, ctx) or {}
        # If the cached OAuth org_data didn't include this program/opp,
        # validate gives us the id but no display object — synthesize a
        # minimal one so the picker pill at least shows the URL's program
        # rather than leaving the previous selection stuck on screen. (A
        # labs-only scope this user may not use never gets here: dispatch
        # refused it first.)
        if "program_id" in ctx and "program" not in validated:
            validated["program_id"] = ctx["program_id"]
            validated["program"] = {"id": ctx["program_id"], "name": f"Program #{ctx['program_id']}"}
        if "opportunity_id" in ctx and "opportunity" not in validated:
            validated["opportunity_id"] = ctx["opportunity_id"]
            validated["opportunity"] = {
                "id": ctx["opportunity_id"],
                "name": f"Opportunity #{ctx['opportunity_id']}",
            }
        request.labs_context = validated
        save_context_to_session(request, ctx)


class _LabsContextSyncMixin(_ProgramScopeMixin):
    """Sync the labs context picker pill to the program_id / opp_id in this view's
    URL kwargs. Without it, the picker stays on "Select Context" on every
    microplans page because the labs middleware reads context from query params
    only — but these routes use path params (e.g. /microplans/program/135/).
    Result: users had to manually pick the program in the context picker even
    though the URL already says which program they're on. Now the picker just
    reflects the page on first load.

    Runs the program access check first (``_ProgramScopeMixin``), so the picker is
    only ever synced to a program the user may use.
    """

    sync_labs_context = True


class _SimpleMicroplanOnlyInWorkspaceMixin:
    """A CHC page a workspace program does not offer: send everyone (Program
    Managers too) to its Simple Microplan list instead
    (``access.workspace_simple_microplan_url``). Listed after the scope mixin, so
    the program access check has already refused anyone the program does not admit."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            from django.http import HttpResponseRedirect

            from connect_labs.microplans.access import workspace_simple_microplan_url

            url = workspace_simple_microplan_url(kwargs.get("program_id"))
            if url is not None:
                return HttpResponseRedirect(url)
        return super().dispatch(request, *args, **kwargs)


def _workspace_refused(request):
    """The page a workspace program shows someone it does not admit."""
    from django.template.response import TemplateResponse

    return TemplateResponse(request, "microplans/workspace_refused.html", status=404)


def _new_record_team(da, requested=None, group_id=None):
    """The team a new plan belongs to: its group's, when it is filed into one,
    else the one asked for (``ProgramPlanDataAccess.team_for_new_records`` decides
    what the caller may ask for). None outside a workspace program."""
    if da.workspace is None:
        return None
    if group_id is not None:
        try:
            group = da.get_group(int(group_id))
        except (TypeError, ValueError):
            group = None
        if group is not None:
            requested = (group.data or {}).get("team") or requested
    return da.team_for_new_records(requested)


def _workspace_context(da) -> dict:
    """What a page in a workspace program needs to know about the person (role,
    teams, may they delete). Empty outside a workspace."""
    if da.workspace is None:
        return {}
    from connect_labs.labs.workspaces import can_manage_access, team_choices

    viewer = da.viewer
    is_pm = bool(viewer and viewer.is_program_manager)
    return {
        "workspace": {
            "role": viewer.role if viewer else "",
            "role_label": viewer.label if viewer else "",
            "teams": team_choices(da.workspace, viewer, user=da.caller, records=[*da.list_plans(), *da.list_groups()]),
            "can_delete": da.can_delete,
            "is_program_manager": is_pm,
            # The access page (``ProgramWorkspaceSettingsView``) is Dimagi Program Managers' only.
            "can_manage_access": is_pm and can_manage_access(da.caller),
        }
    }


class PreviewFrameView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Enqueue the sampling preview for the drawn area(s) and return a task id.

    The first fetch for an area hits Overture S3 (~tens of seconds), which used
    to block a web worker for the whole request. Generation now runs on the
    Celery worker (see ``microplans/tasks.py``); this view validates the request
    synchronously (cheap) and enqueues — the client polls
    ``microplans:preview_status`` for the sampled pins + cluster hulls.
    """

    def post(self, request, opp_id):
        from connect_labs.microplans.sampling.frame import FrameConfig
        from connect_labs.microplans.tasks import generate_frame_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config_payload = payload.get("config", {})
            FrameConfig.from_payload(config_payload)  # validate now → 400, not a failed task
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(generate_frame_task.delay(areas, config_payload), request)


class PreviewCoverageView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Enqueue the coverage-mode preview (balanced/grid clusters → polygons).

    Same offload contract as :class:`PreviewFrameView`: validate synchronously,
    enqueue the cold Overture fetch + clustering onto the Celery worker, return a
    task id for the client to poll.
    """

    def post(self, request, opp_id):
        from connect_labs.microplans.coverage.frame import CoverageConfig
        from connect_labs.microplans.tasks import generate_coverage_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config_payload = payload.get("config", {})
            CoverageConfig.from_payload(config_payload)  # validate now → 400, not a failed task
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(generate_coverage_task.delay(areas, config_payload), request)


class PreviewFootprintsView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Enqueue a building-footprints fetch (as point features) for the area(s).

    Used by the "Show building footprints" toggle to sanity-check an area before
    generating cells. Reuses the PG-cached `fetch_buildings` path, but the cold
    fetch is offloaded to Celery like the other previews; the client polls
    ``microplans:preview_status``.
    """

    def post(self, request, opp_id):
        from connect_labs.microplans.tasks import fetch_footprints_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(fetch_footprints_task.delay(areas), request)


class PreviewAreaStatsView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Enqueue per-area building counts (by provider) for the setup planning table.

    Same offload/poll contract as the other previews. The table's name/population/
    U5 columns are computed client-side (free); only the building count needs this
    cold-fetch, so it's driven by an explicit "Update building counts" button."""

    def post(self, request, opp_id):
        from connect_labs.microplans.tasks import preview_area_stats_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            conf = payload.get("min_confidence")
            min_confidence = None if conf in (None, "") else float(conf)
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(preview_area_stats_task.delay(areas, min_confidence), request)


def _not_your_task(request, task_id):
    """The 404 a status poll gets for a task this user did not queue -- the same
    answer as an unknown id, so a guessed id reveals nothing. None when it is theirs."""
    from connect_labs.microplans.models import TaskOwner

    if TaskOwner.owned_by(task_id, request.user):
        return None
    return JsonResponse({"state": "failed", "detail": "Not found."}, status=404)


class PreviewStatusView(LoginRequiredMixin, View):
    """Poll a queued preview/generation task.

    Lifecycle is reported in ``state`` (queued | running | completed | failed).
    On completion the task's own response envelope — which carries its own
    ``status`` of ``ok`` / ``error`` plus the data — is returned under
    ``result``. A failed task returns a generic message (no internal leak); the
    full traceback is logged server-side. Only the user who queued the task can
    read it (plan mutations return the whole plan); anyone else gets the same 404
    as an unknown id.
    """

    def get(self, request, task_id):
        from celery.result import AsyncResult

        if (denied := _not_your_task(request, task_id)) is not None:
            return denied
        result = AsyncResult(task_id)
        state = result.state
        info = result.info if isinstance(result.info, dict) else {}

        if state == "PENDING":
            return JsonResponse({"state": "queued", "message": "Waiting to start…"})
        if state in ("RECEIVED", "STARTED", "PROGRESS"):
            return JsonResponse({"state": "running", "message": info.get("message", "Working…")})
        if state == "SUCCESS":
            payload = result.result if isinstance(result.result, dict) else {}
            return JsonResponse({"state": "completed", "result": payload})
        if state == "FAILURE":
            logger.error("microplans preview task %s failed: %s", task_id, result.info)
            return JsonResponse({"state": "failed", "detail": "Generation failed. Check server logs."})
        return JsonResponse({"state": state.lower(), "message": f"Status: {state}"})


class CompareSurroundingView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Enqueue the surrounding-ward control finder for the selected boundary.

    Given the intervention ward the planner has selected, score every same-level
    neighbour by how well its settlement-density distribution matches — so a
    well-matched control can be chosen *before* sampling, not discovered mismatched
    after. The work is one cold Overture fetch per ward, so (like the previews) it
    runs on the Celery worker; the client polls ``compare_surrounding_status`` for
    the ranked list as it fills in. ``opp_id`` is a routing placeholder — the work
    is keyed entirely on the posted boundary, and building footprints are public.
    """

    def post(self, request, opp_id):
        from connect_labs.microplans.tasks import compare_surrounding_wards_task

        try:
            payload = json.loads(request.body)
            selected = payload.get("selected") or {}
            if not (selected.get("boundary_id") or (selected.get("ref") or {}).get("boundary_id")):
                raise ValueError("no boundary selected")
            config_payload = payload.get("config", {})
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        from django.urls import reverse

        from connect_labs.microplans.models import TaskOwner

        task = compare_surrounding_wards_task.delay(selected, config_payload)
        TaskOwner.record(task.id, request.user)
        return JsonResponse(
            {
                "status": "queued",
                "task_id": task.id,
                "poll_url": reverse("microplans:compare_surrounding_status", args=[task.id]),
            },
            status=202,
        )


class CompareSurroundingStatusView(LoginRequiredMixin, View):
    """Poll a queued surrounding-ward comparison.

    Unlike ``PreviewStatusView`` (message only), this forwards the growing ranked
    ``results`` + ``reference`` from the task meta while it runs, so the panel can
    render each neighbour's match the moment it's scored — the same incremental
    pattern as the bulk-create status view. Only the user who queued it can read it."""

    def get(self, request, task_id):
        from celery.result import AsyncResult

        if (denied := _not_your_task(request, task_id)) is not None:
            return denied
        result = AsyncResult(task_id)
        state = result.state
        info = result.info if isinstance(result.info, dict) else {}

        if state == "PENDING":
            return JsonResponse({"state": "queued", "message": "Waiting to start…"})
        if state in ("RECEIVED", "STARTED", "PROGRESS"):
            return JsonResponse(
                {
                    "state": "running",
                    "message": info.get("message", "Working…"),
                    "results": info.get("results", []),
                    "reference": info.get("reference"),
                    "total": info.get("total"),
                }
            )
        if state == "SUCCESS":
            payload = result.result if isinstance(result.result, dict) else {}
            return JsonResponse({"state": "completed", "result": payload})
        if state == "FAILURE":
            logger.error("microplans compare-surrounding task %s failed: %s", task_id, result.info)
            return JsonResponse({"state": "failed", "detail": "Comparison failed. Check server logs."})
        return JsonResponse({"state": state.lower(), "message": f"Status: {state}"})


class CountriesView(LoginRequiredMixin, View):
    """List ISO countries for the area picker, flagging those with bespoke data.

    `bespoke` = countries that have curated `labs.admin_boundaries` polygons
    loaded (so the resolver will prefer them over Overture). The UI can badge
    these as higher-quality.
    """

    def get(self, request):
        from connect_labs.labs.admin_boundaries.models import AdminBoundary
        from connect_labs.microplans.core import iso

        # .order_by() clears AdminBoundary's Meta.ordering — without it Django adds
        # the ordering columns to the SELECT, so .distinct() dedupes whole rows
        # (12k) instead of iso_code (returning one entry per country).
        bespoke = sorted(AdminBoundary.objects.order_by().values_list("iso_code", flat=True).distinct())
        return JsonResponse({"status": "ok", "countries": iso.all_countries(), "bespoke": bespoke})


def _admin_areas_response(request):
    """List admin areas for a country/level via the boundary resolver.

    POST body: {country, level, q?, parent?, source?}. `parent` is an AdminArea
    (as returned by a previous call) used to narrow children; `source` lets the
    user pick a specific boundary source (falls back to the default if it can't
    serve the level). Response reports the source used + the pickable sources so
    the UI can offer a source dropdown.
    """
    from connect_labs.microplans.core import iso as iso_codes
    from connect_labs.microplans.core.admin_boundaries import SOURCE_LABELS, AdminArea, get_resolver

    try:
        payload = json.loads(request.body)
        # Accept alpha-2 or alpha-3 (the place-search flow supplies alpha-2 from
        # a geocode result); the resolver keys on alpha-3.
        country = iso_codes.to_alpha3(payload["country"]) or payload["country"]
        level = int(payload["level"])
    except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
        return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

    parent = AdminArea.from_json(payload["parent"]) if isinstance(payload.get("parent"), dict) else None
    prefer = payload.get("source") or None
    resolver = get_resolver()
    try:
        areas = resolver.list_areas(
            country,
            level,
            name_contains=(payload.get("q") or None),
            parent=parent,
            source=prefer,
            limit=int(payload.get("limit", 500)),
        )
        used = resolver.source_for(country, level, prefer=prefer).name
        available = resolver.sources_for(country, level)
    except Exception:  # noqa: BLE001
        logger.exception("microplans admin areas lookup failed (country=%s level=%s)", country, level)
        return JsonResponse({"status": "error", "detail": "Boundary lookup failed. Check server logs."}, status=502)

    return JsonResponse(
        {
            "status": "ok",
            "source": used,
            "available_sources": available,
            "source_labels": {n: SOURCE_LABELS.get(n, n) for n in available},
            "areas": [a.to_json() for a in areas],
        }
    )


def _admin_area_geometry_response(request):
    """Resolve one chosen admin area to its GeoJSON geometry."""
    from connect_labs.microplans.core.admin_boundaries import AdminArea, get_resolver

    try:
        area = AdminArea.from_json(json.loads(request.body)["area"])
    except (json.JSONDecodeError, KeyError, TypeError) as e:
        return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

    try:
        geom = get_resolver().geometry(area)
    except Exception:  # noqa: BLE001
        logger.exception("microplans admin area geometry failed (%s/%s)", area.country, area.name)
        return JsonResponse({"status": "error", "detail": "Boundary geometry lookup failed."}, status=502)

    if not geom:
        return JsonResponse({"status": "error", "detail": "Area not found."}, status=404)
    return JsonResponse({"status": "ok", "name": area.name, "geometry": geom})


class AdminAreasView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Admin areas for a country/level, on an opportunity's planning pages (`_admin_areas_response`)."""

    def post(self, request, opp_id):
        return _admin_areas_response(request)


class AdminAreaGeometryView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Resolve one chosen admin area to its GeoJSON geometry (the sampling boundary)."""

    def post(self, request, opp_id):
        return _admin_area_geometry_response(request)


class BoundaryAreasView(LoginRequiredMixin, View):
    """Admin areas for a country/level, for any signed-in page that picks areas.

    Admin boundaries are public geography, not program data, so like the
    viewport endpoint this needs only a login. Solicitations use it: a call's
    owner and its respondents pick areas with no opportunity in hand.
    """

    def post(self, request):
        return _admin_areas_response(request)


class BoundaryGeometryView(LoginRequiredMixin, View):
    """One admin area's geometry, for any signed-in page that picks areas (see `BoundaryAreasView`)."""

    def post(self, request):
        return _admin_area_geometry_response(request)


class PopulationByNameView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Best-effort population lookup for wards known only by name.

    Used by an uploaded boundary file (GeoJSON carrying wardname/lganame/
    statename properties, e.g. GRID3-format) — it has no AdminBoundary id, so
    the normal geometry/id-based boundary picking never attaches a population
    bag to it. POST body: ``{"wards": [{"ward","lga","state"}, ...]}``.
    Response mirrors the input order: ``{"populations": [dict|null, ...]}`` —
    ``null`` where nothing matched confidently (see
    ``resolve_population_by_name``); never a guess.
    """

    def post(self, request, opp_id):
        from connect_labs.microplans.core.admin_boundaries import (
            resolve_population_by_name,
            ward_population_candidates,
        )

        try:
            wards = json.loads(request.body)["wards"]
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        # Fetch the ~9,300-row NGA ward candidate set once for the whole batch
        # rather than once per ward (matching is name-based, not narrowable in SQL).
        candidates = ward_population_candidates()
        populations = [
            resolve_population_by_name(w.get("state", ""), w.get("lga", ""), w.get("ward", ""), candidates=candidates)
            for w in wards
        ]
        return JsonResponse({"status": "ok", "populations": populations})


class BoundaryViewportView(LoginRequiredMixin, View):
    """Admin boundaries intersecting the map viewport, for the 'Boundaries' layer.

    GET query params:
      * ``bbox`` (required) — ``minLng,minLat,maxLng,maxLat`` (WGS84).
      * ``zoom`` — map zoom; coarser zoom → more outline simplification.
      * ``source`` — pick a boundary system (``labs`` / ``overture``); falls back to
        the country default. Exactly one source renders at a time.
      * ``iso`` — alpha-3 country; optional for labs (a filter), **required for
        Overture** (parquet partition pruning).
      * ``level`` — restrict to one canonical admin level (1/2/3).

    Returns a GeoJSON FeatureCollection plus the source actually used and the
    pickable-source list (so the layer can offer a single-select source dropdown).
    """

    LIMIT = 1500

    def get(self, request):
        from connect_labs.microplans.core.admin_boundaries import SOURCE_LABELS, get_resolver

        bbox = self._parse_bbox(request.GET.get("bbox"))
        if bbox is None:
            return JsonResponse(
                {"status": "error", "detail": "bbox=minLng,minLat,maxLng,maxLat is required."}, status=400
            )
        from connect_labs.microplans.core import iso as iso_codes

        zoom = _float_or_none(request.GET.get("zoom"))
        source = request.GET.get("source") or None
        # Accept alpha-2 or alpha-3; the resolver keys on alpha-3. The place-search
        # flow sets the country from a Mapbox geocode result, which is alpha-2.
        raw_iso = request.GET.get("iso") or None
        iso = (iso_codes.to_alpha3(raw_iso) or raw_iso) if raw_iso else None
        levels = [int(request.GET["level"])] if (request.GET.get("level") or "").isdigit() else None

        resolver = get_resolver()
        try:
            features, truncated = resolver.boundaries_in_bbox(
                bbox, source=source, iso=iso, levels=levels, zoom=zoom, limit=self.LIMIT
            )
            used = resolver.bbox_source_name(source, iso)
        except Exception:  # noqa: BLE001
            logger.exception("microplans boundary viewport failed (iso=%s source=%s)", iso, source)
            return JsonResponse({"status": "error", "detail": "Boundary viewport lookup failed."}, status=502)

        available = self._available_sources(resolver, iso)
        return JsonResponse(
            {
                "status": "ok",
                "type": "FeatureCollection",
                "features": [f.to_feature() for f in features],
                "truncated": truncated,
                "source": used,
                "available_sources": available,
                "source_labels": {n: SOURCE_LABELS.get(n, n) for n in available},
            }
        )

    # Snap the viewport bbox to a coarse grid before querying. The cache (and the
    # DuckDB/PostGIS scan) key on the bbox, so raw continuous pan/zoom floats made
    # every frame a unique, never-reused entry. Snapping outward to a ~5.5 km tile
    # means nearby pans reuse one cached result; the layer renders the small
    # superset fine. Snapping the *query* (not just the key) keeps it correct.
    SNAP_DEG = 0.05

    @classmethod
    def _parse_bbox(cls, raw):
        import math

        from django.contrib.gis.geos import Polygon

        if not raw:
            return None
        try:
            minx, miny, maxx, maxy = (float(v) for v in raw.split(","))
        except (ValueError, TypeError):
            return None
        if minx >= maxx or miny >= maxy:
            return None
        s = cls.SNAP_DEG
        minx = round(math.floor(minx / s) * s, 4)
        miny = round(math.floor(miny / s) * s, 4)
        maxx = round(math.ceil(maxx / s) * s, 4)
        maxy = round(math.ceil(maxy / s) * s, 4)
        poly = Polygon.from_bbox((minx, miny, maxx, maxy))
        poly.srid = 4326
        return poly

    @staticmethod
    def _available_sources(resolver, iso):
        """Sources with data for this region (preference order), for the picker.
        Without an iso we can't scope to a country, so offer all known sources."""
        if not iso:
            return resolver.source_names()
        seen, out = set(), []
        for level in (1, 2, 3):
            for name in resolver.sources_for(iso, level):
                if name not in seen:
                    seen.add(name)
                    out.append(name)
        return out or resolver.source_names()


# --- Planning-phase plan review/edit (the LLO validation layer; pre-upload) ---


# ============================================================================
# Program layer: a program owns a portfolio of candidate plans + plan groups.
# Plans are program-scoped; an opportunity is bound only at Deploy.
#
# Authorization: ProgramPlanDataAccess sends program_id on every read/write, so
# the production LabsRecord API enforces program membership (a non-member gets an
# empty/404 result — see CLAUDE.md "Permission Model"). Labs has no local program
# membership table to check against, so this is the only auth boundary; the HTML
# shell views render for any logged-in user, but their data fetches return nothing
# unless the user is a member. No program data leaks from rendering the shell.
# ============================================================================


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramWorkspaceView(
    _LabsContextSyncMixin, _SimpleMicroplanOnlyInWorkspaceMixin, LoginRequiredMixin, TemplateView
):
    """Program workspace: the portfolio of candidate plans + plan groups.

    Not for a workspace program, which makes its microplans in Simple Microplan."""

    template_name = "microplans/program_workspace.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        # Seed the new-plan map over the program's existing footprint so boundaries
        # load + the country auto-detects (see _program_map_seed). Absent any plans,
        # the template keeps its default view and the user navigates.
        context["can_delete"] = True
        try:
            from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

            da = ProgramPlanDataAccess(program_id, request=self.request)
            # Workspace programs: who this person is there, which teams they can
            # file new work under, and whether the Delete buttons show at all.
            context.update(_workspace_context(da))
            context["can_delete"] = da.can_delete
            if da.workspace is None:
                # A labs-only program Dimagi staff could turn workspace roles on for.
                from connect_labs.labs.synthetic.local_records_backend import is_labs_only_program_id
                from connect_labs.utils.dimagi_user import is_dimagi_staff

                context["can_turn_on_workspace"] = is_dimagi_staff(self.request.user) and is_labs_only_program_id(
                    da.program_id
                )
            seed = _program_map_seed(da.list_plans())
        except Exception:  # noqa: BLE001
            logger.exception("microplans new-plan map seed failed (program=%s)", program_id)
            seed = None
        if seed:
            context["map_country_iso"] = seed.get("iso") or ""
            if seed.get("lng") is not None:
                context["map_center_lng"] = seed["lng"]
                context["map_center_lat"] = seed["lat"]
                context["map_zoom"] = seed["zoom"]
        return context


class ProgramPlansAPIView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """JSON: the program's plans (+ headline KPIs) and groups, for the workspace."""

    def get(self, request, program_id):
        from connect_labs.microplans.core import plan as plan_lib
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan_records = da.list_plans()
            plans = [serialization.plan_summary_row(p) for p in plan_records]
            if da.workspace is not None:
                for row, p in zip(plans, plan_records):
                    row["team"] = (p.data or {}).get("team") or ""
            groups = [
                {
                    "group_id": g.id,
                    "name": g.name,
                    "plan_ids": g.plan_ids,
                    "offered_to": g.offered_to,
                    "shared": g.shared,
                    "kind": g.kind,  # "bundle" | "study" — drives the workspace card affordances
                    "status": g.status,
                    # Per-member arm map ({plan_id: "intervention"|"control"}) so the
                    # workspace can label a two-arm study by its arm pair and show the
                    # field-blind marker. Labs-side only — never written to a plan/work
                    # area (execution stays blind). Always a dict for bundles (empty).
                    "arms": g.arms,
                }
                for g in da.list_groups()
            ]
            if da.workspace is not None:
                teams = {g.id: (g.data or {}).get("team") or "" for g in da.list_groups()}
                for row in groups:
                    row["team"] = teams.get(row["group_id"], "")
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plans failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not load program plans."}, status=502)
        return JsonResponse(
            {
                "status": "ok",
                "plans": plans,
                "groups": groups,
                "statuses": list(plan_lib.PLAN_STATUSES),
                "status_labels": plan_lib.PLAN_STATUS_LABELS,
                "transitions": {k: sorted(v) for k, v in plan_lib.PLAN_TRANSITIONS.items()},
                "can_delete": da.can_delete,
            }
        )


class ProgramCreatePlanView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Create a Draft plan in the program from a generated frame (region + geometry)."""

    def post(self, request, program_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        empty_fc = {"type": "FeatureCollection", "features": []}
        try:
            payload = json.loads(request.body)
            region = str(payload.get("region", "")).strip()[:255]
            name = str(payload.get("name", "") or region or "Untitled plan").strip()[:255]
            # Administrative labels Connect's work-area importer requires non-empty
            # (see microplans/CONNECT_IMPORT_CONTRACT.md). lga falls back to region.
            lga = str(payload.get("lga", "") or region).strip()[:255]
            state = str(payload.get("state", "")).strip()[:255]
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            pins = payload.get("pins") or empty_fc
            hulls = (payload.get("coverage_areas") or payload.get("hulls")) or empty_fc
            # Optional: the original draw/admin/pin areas from setup. Stored on the
            # plan so the review-page footprints overlay can re-fetch by THAT geometry
            # (which is already PG-cached from generation) instead of by the unioned
            # cells (a different hash → expensive cold Overture query).
            input_areas = payload.get("input_areas") or []
            if not isinstance(input_areas, list):
                input_areas = []
            # Optional: Phase-1 grouping strategy + params. Defaults to BFS
            # adjacency (Connect-GIS parity).
            grouping = payload.get("grouping") or {}
            if not isinstance(grouping, dict):
                grouping = {}
            # Optional: per-arm sampling stats, stored so the review page can redraw
            # the Sample details panel on load (parity with regenerate).
            stats = payload.get("stats")
            if not isinstance(stats, list):
                stats = None
            # Optional: drop the new plan straight into a group (the group-page
            # "add a plan in the editor" path).
            group_id = payload.get("group_id")
            # Optional: per-ward expected-visit targets from the setup planning table
            # (the U5-for-calc column), spread across each ward's buildings at creation.
            area_targets = payload.get("area_targets")
            if not isinstance(area_targets, dict):
                area_targets = None
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        # State is the one Connect-importer field the boundary picker can't supply
        # from an LGA search result. Derive it from the ADM1 boundary containing the
        # plan so the exported CSV is import-ready without the owner re-typing it.
        if not state:
            state = _adm1_state_for(input_areas, hulls)

        da = ProgramPlanDataAccess(program_id, request=request)
        # Workspace programs only: the team this plan is for (the setup page
        # carries the portfolio's chosen team as ?team=).
        team = _new_record_team(da, payload.get("team") or request.GET.get("team"), group_id)
        try:
            plan = da.create_plan(
                region=region,
                name=name,
                mode=mode,
                pins=pins,
                hulls=hulls,
                input_areas=input_areas,
                grouping=grouping,
                lga=lga,
                state=state,
                stats=stats,
                area_targets=area_targets,
                team=team,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("microplans create_plan failed (program=%s)", program_id)
            # Surface the real error: this generic message has repeatedly hidden
            # actionable failures (e.g. boundary/geometry issues on a single ward).
            return JsonResponse({"status": "error", "detail": f"Could not create the plan: {e}"}, status=502)
        group_warning = None
        if group_id is not None:
            try:
                da.add_plan_to_group(int(group_id), plan.id)
            except Exception:  # noqa: BLE001
                # The plan was created; failing to file it into the group shouldn't
                # lose the plan. Log + report so the UI can surface a soft warning.
                logger.exception(
                    "microplans create_plan: add to group failed (program=%s group=%s plan=%s)",
                    program_id,
                    group_id,
                    plan.id,
                )
                group_warning = "added plan but not to group"
        # Return the full plan payload + its plan-scoped URLs so the client can
        # hydrate the page in place (create-in-place) — no reload, identical to a
        # freshly-opened plan. plan_id stays at the top level for older callers.
        resp = serialization.plan_to_json(plan)
        resp["plan_id"] = plan.id
        resp["urls"] = _plan_scoped_urls(program_id, plan.id)
        if group_warning:
            resp["group_warning"] = group_warning
        return JsonResponse(resp)


class ProgramGroupBulkCreateFromBoundariesView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Create one boundary-only ward-plan per selected admin boundary, filed into a
    study (group), from the map "Add wards from map" surface.

    Each item is a selected admin boundary: ``{name, lga, state, boundary_id,
    geometry}``. We create a boundary-only sampling plan (``input_areas`` set, no
    work areas → ``phase: "boundary"``) via the same ``create_plan`` core the
    single-plan editor uses, then file it into the group. Arms are assigned later
    on the study page and sampling is the study's bulk action — so this endpoint
    never touches arms or work areas, keeping the study-groups model (one plan per
    ward, arm on the group, blind by construction) intact."""

    def post(self, request, program_id, group_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        empty_fc = {"type": "FeatureCollection", "features": []}
        try:
            payload = json.loads(request.body)
            boundaries = payload.get("boundaries")
        except (json.JSONDecodeError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        if not isinstance(boundaries, list) or not boundaries:
            return JsonResponse({"status": "error", "detail": "Select at least one boundary to add."}, status=400)

        da = ProgramPlanDataAccess(program_id, request=request)
        # Plans filed into a study take the study's team (workspace programs only).
        team = _new_record_team(da, group_id=group_id)
        plan_ids: list[int] = []
        warnings: list[str] = []
        for item in boundaries:
            if not isinstance(item, dict):
                continue
            geometry = item.get("geometry")
            if not geometry:
                continue
            region = str(item.get("name", "") or "").strip()[:255]
            name = region or "Untitled ward"
            lga = str(item.get("lga", "") or region).strip()[:255]
            state = str(item.get("state", "")).strip()[:255]
            # State is the one Connect-importer field the boundary picker can't always
            # supply; derive from the ADM1 boundary containing the area when blank.
            if not state:
                state = _adm1_state_for([geometry], empty_fc)
            try:
                from connect_labs.microplans.tasks import create_boundary_plan

                plan = create_boundary_plan(
                    da,
                    mode="sampling",
                    name=name,
                    region=region,
                    geometry=geometry,
                    boundary_id=str(item.get("boundary_id") or "").strip(),
                    lga=lga,
                    state=state,
                    team=team,
                )
            except Exception:  # noqa: BLE001
                logger.exception("microplans bulk_create_from_boundaries: create_plan failed (program=%s)", program_id)
                warnings.append(f"failed to create plan for {name!r}")
                continue
            try:
                da.add_plan_to_group(int(group_id), plan.id)
            except Exception:  # noqa: BLE001
                # The plan exists; failing to file it into the group shouldn't lose it.
                logger.exception(
                    "microplans bulk_create_from_boundaries: add to group failed (program=%s group=%s plan=%s)",
                    program_id,
                    group_id,
                    plan.id,
                )
                warnings.append(f"created {name!r} but did not file it into the study")
            plan_ids.append(plan.id)

        if not plan_ids:
            return JsonResponse(
                {"status": "error", "detail": "Could not create any plans from the selection."}, status=502
            )
        resp = {"status": "ok", "plan_ids": plan_ids}
        if warnings:
            resp["warnings"] = warnings
        return JsonResponse(resp)


class ProgramPlanTransitionView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Advance a plan's lifecycle status (Deploy binds the live opportunity_id)."""

    def post(self, request, program_id, plan_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError

        try:
            payload = json.loads(request.body)
            to = payload["to"]
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.transition_plan(
                int(plan_id),
                to,
                request.user.get_username(),
                opportunity_id=payload.get("opportunity_id"),
                base_revision=payload.get("revision"),
            )
        except StalePlanError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=409)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans transition failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Transition failed."}, status=502)
        return JsonResponse(
            {
                "status": "ok",
                "plan_id": plan.id,
                "plan_status": plan.status,
                "opportunity_id": plan.data.get("opportunity_id"),
            }
        )


class ProgramPlanDeleteView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Hard-delete a plan record. POST → 204. Used for wiping sample data; the
    safer default for normal lifecycle is the Archive status transition."""

    def post(self, request, program_id, plan_id):
        from connect_labs.microplans.core.data_access import (
            ProgramPlanDataAccess,
            RecordNotInProgramError,
            WorkspaceDeleteForbidden,
        )

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_plan(plan_id)
        except WorkspaceDeleteForbidden as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=403)
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans delete_plan failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "plan_id": int(plan_id)})


class ProgramGroupDeleteView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Hard-delete a plan group record (sample-data wipe)."""

    def post(self, request, program_id, group_id):
        from connect_labs.microplans.core.data_access import (
            ProgramPlanDataAccess,
            RecordNotInProgramError,
            WorkspaceDeleteForbidden,
        )

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_group(group_id)
        except WorkspaceDeleteForbidden as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=403)
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Group not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans delete_group failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "group_id": int(group_id)})


class ProgramGroupsAPIView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Create a plan group (a shareable subset offered to an LLO)."""

    def post(self, request, program_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, RecordNotInProgramError

        try:
            payload = json.loads(request.body)
            name = str(payload["name"]).strip()[:255]
            plan_ids = [int(p) for p in payload.get("plan_ids", [])]
            offered_to = str(payload.get("offered_to", "")).strip()[:255]
            kind = "study" if payload.get("kind") == "study" else "bundle"
            arms = payload.get("arms") if isinstance(payload.get("arms"), dict) else {}
            # A bundle is a curated subset of EXISTING plans (needs ≥1). A study
            # starts empty — you add its ward plans afterwards from the group page.
            if not name or (kind != "study" and not plan_ids):
                raise ValueError("name and at least one plan are required")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            group = da.create_group(
                name=name,
                plan_ids=plan_ids,
                offered_to=offered_to,
                kind=kind,
                arms=arms,
                team=da.team_for_new_records(payload.get("team")),
            )
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans create_group failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not create the group."}, status=502)
        return JsonResponse({"status": "ok", "group_id": group.id})


class ProgramGroupUpdateView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Update a plan group (rename, change plans, set offered_to, share)."""

    def post(self, request, program_id, group_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, RecordNotInProgramError

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            # Membership edits use the dedicated helpers so a study's arm map stays
            # consistent (removing a plan also drops its arm assignment).
            if payload.get("remove_plan_id") is not None:
                group = da.remove_plan_from_group(int(group_id), int(payload["remove_plan_id"]))
                return JsonResponse({"status": "ok", "group_id": group.id, "plan_ids": group.plan_ids})
            if payload.get("add_plan_id") is not None:
                group = da.add_plan_to_group(int(group_id), int(payload["add_plan_id"]))
                return JsonResponse({"status": "ok", "group_id": group.id, "plan_ids": group.plan_ids})
            group = da.update_group(
                int(group_id),
                name=payload.get("name"),
                plan_ids=payload.get("plan_ids"),
                offered_to=payload.get("offered_to"),
                shared=payload.get("shared"),
                kind=payload.get("kind"),
                arms=payload.get("arms"),  # study arm assignment (labs-side only)
                status=payload.get("status"),
                team=payload.get("team"),  # workspace programs, Program Managers only
            )
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans update_group failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Could not update the group."}, status=502)
        return JsonResponse({"status": "ok", "group_id": group.id, "shared": group.shared})


class ProgramGroupShareView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """LLO-facing page for a plan group: its subset of plans + their KPIs."""

    template_name = "microplans/group_share.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        from connect_labs.microplans.core import plan as plan_lib
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        group_id = kwargs.get("group_id")
        context["program_id"] = program_id
        context["group_id"] = group_id
        da = ProgramPlanDataAccess(program_id, request=self.request)
        try:
            group = da.get_group(int(group_id))
            plans_by_id = {p.id: p for p in da.list_plans()}
            # Preserve the group's plan order — no synthetic "fit score" ranking.
            # The LLO reads the actual metrics (worst travel, imbalance, coverage)
            # and decides for themselves.
            entries = []
            for pid in group.plan_ids:
                p = plans_by_id.get(pid)
                if p is None:
                    continue
                _ss = (p.data.get("sampling_stats") or [{}])[0]
                _area_bld = _ss.get("after_filters") or _ss.get("fetched") or None
                kpis = plan_lib.plan_kpis(
                    p.work_areas, input_areas=p.data.get("input_areas") or [], area_buildings=_area_bld
                )
                entries.append(
                    {
                        "plan_id": pid,
                        "name": p.name,
                        "region": p.region,
                        "kpis": kpis,
                        "assigned": kpis["dimension"] == "worker",
                        "work_areas": len(p.work_areas),
                        "status": p.status,
                        "status_label": plan_lib.PLAN_STATUS_LABELS.get(p.status, p.status),
                        "review_url": reverse("microplans:program_review", args=[program_id, pid]),
                    }
                )
            context["group_name"] = group.name
            context["offered_to"] = group.offered_to
            context["entries"] = entries
        except Exception:  # noqa: BLE001
            logger.exception("microplans group share failed (program=%s group=%s)", program_id, group_id)
            context["error"] = "Could not load the group."
        return context


# Arm → fill colour (matches review.html's ARM_COLOR); plain bundles fall back to a
# per-plan palette so each plan still reads as its own layer on the overlay.
_ARM_COLORS = {"intervention": "#22c55e", "control": "#3b82f6"}
_PLAN_PALETTE = ["#6366f1", "#f59e0b", "#ef4444", "#14b8a6", "#a855f7", "#84cc16"]


class ProgramGroupMapView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """View-only overlay of every plan in a group on one map, coloured by arm.

    The 'go look at them' surface — reuses the same Mapbox + map-panel machinery as
    the single-plan review page, but read-only (no draw, no edits) and over N plans
    at once. Each plan's work areas become one toggleable, arm-coloured layer."""

    template_name = "microplans/group_map.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings
        from django.urls import reverse

        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        group_id = int(kwargs.get("group_id"))
        context["program_id"] = program_id
        context["group_id"] = group_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        context["back_url"] = reverse("microplans:program_group_page", args=[program_id, group_id])
        da = ProgramPlanDataAccess(program_id, request=self.request)
        try:
            group = da.get_group(group_id)
            plans_by_id = {p.id: p for p in da.list_plans()}
            layers = []
            for i, pid in enumerate(group.plan_ids):
                p = plans_by_id.get(pid)
                if p is None:
                    continue
                arm = group.arm_for(pid)
                color = _ARM_COLORS.get(arm) or _PLAN_PALETTE[i % len(_PLAN_PALETTE)]
                # The SELECTED settlements (PSU hulls) saved at sampling time, drawn as
                # translucent arm-coloured polygons UNDER the pins so the surveyed
                # settlements the comparability check compares are visible on the map.
                settlement_feats = [
                    {
                        "type": "Feature",
                        "geometry": f["geometry"],
                        "properties": {"plan_id": pid, "arm": arm, "feature": "settlement"},
                    }
                    for f in (p.data.get("psu_hulls") or {}).get("features", [])
                    if f.get("geometry")
                ]
                pin_feats = [
                    {"type": "Feature", "geometry": wa["geometry"], "properties": {"plan_id": pid, "arm": arm}}
                    for wa in p.work_areas
                    if wa.get("geometry")
                ]
                feats = settlement_feats + pin_feats  # hulls first → pins render on top
                layers.append(
                    {
                        "plan_id": pid,
                        "name": p.name or f"Plan {pid}",
                        "arm": arm,
                        "color": color,
                        "phase": p.phase,
                        "work_areas": len(pin_feats),
                        "geojson": {"type": "FeatureCollection", "features": feats},
                    }
                )
            context["group_name"] = group.name
            context["is_study"] = group.kind == "study"
            context["plan_layers"] = layers
            context["plan_layers_json"] = json.dumps(layers)
        except Exception:  # noqa: BLE001
            logger.exception("microplans group map failed (program=%s group=%s)", program_id, group_id)
            context["error"] = "Could not load the group map."
        return context


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramGroupAddFromMapView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Map-based "Add wards from map" surface for a study.

    A full-page map that reuses the standalone service-delivery overlay
    (`MicroplansServiceDelivery`, multi-opp, visual context) + the admin-boundaries
    layer (`MicroplansAdminBoundaries`, multi-select) and adds a "Create N plans"
    action that files one boundary-only ward-plan per selected boundary into this
    study via ``bulk_create_from_boundaries``. No draw/sampling/arms here — the
    study page owns those — so the study-groups model stays intact."""

    template_name = "microplans/add_from_map.html"

    def get_context_data(self, **kwargs):
        from django.conf import settings
        from django.urls import reverse

        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        group_id = int(kwargs.get("group_id"))
        context["program_id"] = program_id
        context["group_id"] = group_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        if not settings.MAPBOX_TOKEN:
            context["error"] = "MAPBOX_TOKEN is not configured; the map cannot load."
        # Service-delivery layer endpoints (opp_id is a routing placeholder — the POST
        # body's opp_ids drive the fetch, validated against the user's opportunities).
        context.update(_sd_urls())
        # Admin-boundaries layer endpoints.
        context["boundary_viewport_url"] = reverse("microplans:boundary_viewport")
        context["admin_area_geometry_url"] = reverse("microplans:admin_area_geometry", args=[123])
        context["admin_areas_url"] = reverse("microplans:admin_areas", args=[123])
        # The bulk create-into-study endpoint + where to return on success.
        context["bulk_create_url"] = reverse(
            "microplans:program_group_bulk_create_from_boundaries", args=[program_id, group_id]
        )
        context["manage_url"] = reverse("microplans:program_group_page", args=[program_id, group_id])
        context["back_url"] = context["manage_url"]
        # Service-delivery opportunity picker: the user's full opportunity list is
        # noisy with test/QA/throwaway entries that clutter this picker (the planner
        # only wants real delivery footprints). Filter obvious junk by name; the
        # template dumps THIS list (sd_opportunities), not the raw user_opportunities.
        from connect_labs.labs.context import get_org_data

        all_opps = get_org_data(self.request).get("opportunities", []) or []
        context["sd_opportunities"] = _filter_demo_junk_opps(all_opps)
        # Open the map over the program's existing footprint so the Boundaries layer
        # actually loads (and the country auto-detects) instead of a cold whole-country
        # view where the by-name ward search falls back to a place geocoder. Same seed
        # the new-plan map uses; carries the iso so the boundary search works on entry.
        try:
            seed = _program_map_seed(ProgramPlanDataAccess(program_id, request=self.request).list_plans())
        except Exception:  # noqa: BLE001
            logger.exception("microplans add-from-map seed failed (program=%s)", program_id)
            seed = None
        if seed:
            context["map_country_iso"] = seed.get("iso") or ""
            if seed.get("lng") is not None:
                context["map_center_lng"] = seed["lng"]
                context["map_center_lat"] = seed["lat"]
                context["map_zoom"] = seed["zoom"]
        try:
            group = ProgramPlanDataAccess(program_id, request=self.request).get_group(group_id)
            context["group_name"] = group.name
            context["is_study"] = group.kind == "study"
        except Exception:  # noqa: BLE001
            logger.exception("microplans add-from-map: load group failed (program=%s group=%s)", program_id, group_id)
            context["group_name"] = "Study"
            context["is_study"] = True
        return context


class ProgramGroupGenerateView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Run the rooftop sampling engine across all of a group's member plans (bulk).

    Enqueues ``generate_group_samples_task`` with the group's shared sampling
    config so every arm is sampled identically. Returns ``202 {task_id, poll_url}``;
    the client polls the same status endpoint bulk-create uses."""

    def post(self, request, program_id, group_id):
        from django.urls import reverse

        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
        from connect_labs.microplans.tasks import generate_group_samples_task

        access_token = (request.session.get("labs_oauth") or {}).get("access_token")
        if not access_token:
            return JsonResponse(
                {"status": "error", "detail": "Not authenticated with Connect — sign in again."}, status=401
            )
        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            payload = {}
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            group = da.get_group(int(group_id))
        except Exception:  # noqa: BLE001
            logger.exception("microplans group generate: load failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Group not found."}, status=404)
        config = dict(payload.get("config") or group.sampling_config or {})
        if group.kind == "study":
            # Two-arm studies sample with size-stratified PPS by default so the arms
            # are comparable on cluster size by construction (see sample.select_psus).
            config.setdefault("size_balance_bands", 3)
        from connect_labs.microplans.models import TaskOwner

        task = generate_group_samples_task.delay(int(program_id), int(group_id), config, access_token)
        TaskOwner.record(task.id, request.user)
        return JsonResponse(
            {
                "status": "queued",
                "task_id": task.id,
                "poll_url": reverse("microplans:bulk_create_status", args=[task.id]),
            },
            status=202,
        )


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramGroupPageView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Planner-facing plan-group management landing page.

    The holistic hub for a group: its member plans (each with mode/phase/arm),
    add/remove, and entry points to the multi-plan map, compare, and (for studies)
    arm assignment. A *study* group (``kind="study"``) shows each plan's arm —
    arm lives only here, never on the plans, so execution stays blind."""

    template_name = "microplans/group.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        from connect_labs.microplans.core import plan as plan_lib
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        group_id = int(kwargs.get("group_id"))
        context["program_id"] = program_id
        context["group_id"] = group_id
        da = ProgramPlanDataAccess(program_id, request=self.request)
        try:
            group = da.get_group(group_id)
            plans_by_id = {p.id: p for p in da.list_plans()}
            entries = []
            for pid in group.plan_ids:
                p = plans_by_id.get(pid)
                if p is None:
                    continue
                entries.append(
                    {
                        "plan_id": pid,
                        "name": p.name,
                        "region": p.region,
                        "mode": p.mode,
                        "phase": p.phase,  # boundary | sampled
                        "arm": group.arm_for(pid),  # study only; None otherwise
                        "work_areas": len(p.work_areas),
                        "status": p.status,
                        "status_label": plan_lib.PLAN_STATUS_LABELS.get(p.status, p.status),
                        "review_url": reverse("microplans:program_review", args=[program_id, pid]),
                        # Each plan exports to its OWN opportunity's work-area CSV (one opp
                        # per plan) — and the CSV carries no arm (blind by construction).
                        "csv_url": reverse("microplans:program_plan_csv", args=[program_id, pid]),
                    }
                )
            context["group"] = {
                "name": group.name,
                "kind": group.kind,
                "is_study": group.kind == "study",
                "status": group.status,
                "offered_to": group.offered_to,
                "plan_count": len(entries),
            }
            context["entries"] = entries
            # Whether every member ward is already sampled — drives the state-aware
            # "Generate / Re-generate samples" label so the action never reads as
            # pending against rows that already show "sampled".
            context["all_sampled"] = bool(entries) and all(e["phase"] == "sampled" for e in entries)
            # The exact columns each ward's Connect CSV carries — surfaced so the
            # "blind export" claim is observable on screen (the reader can see there is
            # no Arm column), not merely asserted in a caption.
            from connect_labs.microplans.core.workarea import CSV_HEADERS

            context["csv_columns"] = list(CSV_HEADERS.values())
            # Study comparability — is the control a fair counterfactual? Compared on
            # the SELECTED PSUs / surveyed buildings (settlement density, PSU size,
            # building footprint) via standardized mean differences — NOT whole-ward
            # geography, which the PPS survey never visits. Whole-ward density is kept
            # only as a context line so the old (wrong) signal is visible, not used.
            if group.kind == "study":
                from connect_labs.microplans.core.comparability import arm_comparability_psu, psu_arms_from_stats
                from connect_labs.microplans.core.plan import plan_sample_areas

                def _resolve_boundary(bid):
                    from connect_labs.labs.admin_boundaries.models import AdminBoundary

                    b = AdminBoundary.objects.filter(boundary_id=bid).first()
                    return json.loads(b.geometry.geojson) if (b and b.geometry) else None

                def _ward_density(pl, arm):
                    # Context only: buildings per WARD km² (the metric we no longer gate on).
                    try:
                        from pyproj import Transformer
                        from shapely.geometry import shape
                        from shapely.ops import transform, unary_union

                        from connect_labs.microplans.core.geo import utm_epsg_for

                        wards = plan_sample_areas(pl.data.get("input_areas") or [], arm, _resolve_boundary)
                        geom = unary_union([shape(w["geometry"]) for w in wards])
                        c = geom.centroid
                        tf = Transformer.from_crs(4326, utm_epsg_for(c.x, c.y), always_xy=True).transform
                        km2 = transform(tf, geom).area / 1e6
                        st = (pl.data.get("sampling_stats") or [{}])[0]
                        bc = int(st.get("after_filters") or st.get("fetched") or 0)
                        return round(bc / km2, 1) if km2 > 0 else 0.0
                    except Exception:  # noqa: BLE001
                        return 0.0

                # Assemble one stats entry per member plan (tagged with its arm), plus
                # the per-arm display name + whole-ward context density, then hand to the
                # SHARED builder + engine — the same path the single-plan endpoint uses.
                stat_entries, names, ward_density = [], {}, {}
                for pid in group.plan_ids:
                    pl = plans_by_id.get(pid)
                    arm = group.arm_for(pid)
                    if pl is None or not arm or not pl.work_areas:
                        continue
                    st = dict((pl.data.get("sampling_stats") or [{}])[0])
                    st["arm"] = arm
                    stat_entries.append(st)
                    names[arm] = pl.name or f"Plan {pl.id}"
                    ward_density[arm] = _ward_density(pl, arm)
                if len({s["arm"] for s in stat_entries}) >= 2:
                    arms_data = psu_arms_from_stats(stat_entries, names=names, ward_density=ward_density)
                    context["comparability"] = arm_comparability_psu(arms_data)
            # Action URLs (reuse existing surfaces; map/generate land in later steps)
            ids_csv = ",".join(str(e["plan_id"]) for e in entries)
            context["compare_url"] = reverse("microplans:program_compare_page", args=[program_id]) + (
                f"?plans={ids_csv}" if ids_csv else ""
            )
            # Carry the group id so plans created via either add-path file into THIS group.
            context["bulk_create_url"] = (
                reverse("microplans:program_bulk_create_page", args=[program_id]) + f"?group={group_id}"
            )
            context["new_plan_url"] = (
                reverse("microplans:program_create_plan_page", args=[program_id]) + f"?group={group_id}"
            )
            context["add_from_map_url"] = reverse("microplans:program_group_add_from_map", args=[program_id, group_id])
            context["remove_plan_url"] = reverse("microplans:program_group_update", args=[program_id, group_id])
            context["map_url"] = reverse("microplans:program_group_map", args=[program_id, group_id])
            context["generate_url"] = reverse("microplans:program_group_generate", args=[program_id, group_id])
            context["back_url"] = reverse("microplans:program_workspace", args=[program_id])
            context["create_solicitation_url"] = (
                reverse("solicitations:create") + f"?source_program_id={program_id}&source_group_id={group_id}"
            )
        except Exception:  # noqa: BLE001
            logger.exception("microplans group page failed (program=%s group=%s)", program_id, group_id)
            context["error"] = "Could not load the group."
        return context


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramReviewView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program-scoped per-plan review page (reuses review.html via context URLs)."""

    template_name = "microplans/review.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id, plan_id = kwargs.get("program_id"), kwargs.get("plan_id")
        context["program_id"] = program_id
        context["plan_id"] = plan_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        context["plan_url"] = reverse("microplans:program_plan", args=[program_id, plan_id])
        context["edit_url"] = reverse("microplans:program_plan_edit", args=[program_id, plan_id])
        context["area_targets_url"] = reverse("microplans:program_plan_area_targets", args=[program_id, plan_id])
        context["csv_url"] = reverse("microplans:program_plan_csv", args=[program_id, plan_id])
        context["footprints_url"] = reverse("microplans:program_plan_footprints", args=[program_id, plan_id])
        context["regroup_url"] = reverse("microplans:program_plan_regroup", args=[program_id, plan_id])
        context["reassign_url"] = reverse("microplans:program_plan_reassign", args=[program_id, plan_id])
        # Area-definition URLs are program-scoped via a placeholder opp_id (123),
        # exactly as the setup page does. The endpoints don't actually require
        # a real opp; the path arg is just historical.
        context["preview_coverage_url"] = reverse("microplans:preview_coverage", args=[123])
        context["preview_footprints_url"] = reverse("microplans:preview_footprints", args=[123])
        context["preview_area_stats_url"] = reverse("microplans:preview_area_stats", args=[123])
        context["preview_frame_url"] = reverse("microplans:preview_frame", args=[123])
        context["arm_comparability_url"] = reverse("microplans:arm_comparability", args=[123])
        context["compare_surrounding_url"] = reverse("microplans:compare_surrounding", args=[123])
        context["admin_areas_url"] = reverse("microplans:admin_areas", args=[123])
        context["admin_area_geometry_url"] = reverse("microplans:admin_area_geometry", args=[123])
        context["population_by_name_url"] = reverse("microplans:population_by_name", args=[123])
        context["countries_url"] = reverse("microplans:countries")
        context["boundary_viewport_url"] = reverse("microplans:boundary_viewport")
        context["regenerate_url"] = reverse("microplans:program_plan_regenerate", args=[program_id, plan_id])
        from django.conf import settings as _s

        context["mapbox_token"] = _s.MAPBOX_TOKEN or context.get("mapbox_token", "")
        context["compare_url"] = reverse("microplans:program_compare_page", args=[program_id]) + f"?plans={plan_id}"
        context.update(_sd_urls())
        context["back_url"] = reverse("microplans:program_workspace", args=[program_id])
        return context


class ProgramPlanView(_ProgramScopeMixin, LoginRequiredMixin, View):
    def get(self, request, program_id, plan_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan get failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        # get_plan returns None (not an exception) for an id that isn't in this
        # program — e.g. a stale/deleted plan id (a study re-seed renumbers plans).
        # Guard so the review page gets a clean 404 instead of plan_to_json(None)
        # raising AttributeError → 500.
        if plan is None:
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        return JsonResponse(serialization.plan_to_json(plan))

    def delete(self, request, program_id, plan_id):
        """Hard-delete a draft plan. Archive (a status transition) is the normal
        way to retire a candidate region; this is the explicit remove for plans
        the owner wants gone for good (e.g. demo/sample plans). Program-scoped:
        ``delete_plan`` refuses ids that aren't in this program."""
        from connect_labs.microplans.core.data_access import (
            ProgramPlanDataAccess,
            RecordNotInProgramError,
            WorkspaceDeleteForbidden,
        )

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_plan(int(plan_id))
        except WorkspaceDeleteForbidden as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=403)
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan delete failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "deleted": int(plan_id)})


class ProgramPlanEditView(_ProgramScopeMixin, LoginRequiredMixin, View):
    def post(self, request, program_id, plan_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError
        from connect_labs.microplans.core.plan import ACTIONS

        try:
            payload = json.loads(request.body)
            action = payload["action"]
            wa_ids = payload.get("wa_ids") or ([payload["wa_id"]] if payload.get("wa_id") else [])
            if action not in ACTIONS:
                raise ValueError(f"unknown action {action}")
            if not wa_ids:
                raise ValueError("no work area specified")
            if len(wa_ids) > 5000:
                raise ValueError("too many work areas in one request")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        params = {k: v for k, v in payload.items() if k not in ("action", "wa_id", "wa_ids", "revision")}
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.apply_plan_edits(
                int(plan_id),
                [str(w) for w in wa_ids],
                action,
                params,
                request.user.get_username(),
                base_revision=payload.get("revision"),
            )
        except StalePlanError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=409)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan edit failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Edit failed."}, status=502)
        return JsonResponse(serialization.plan_to_json(plan))


class ProgramPlanAreaTargetsView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Set per-area expected-visit targets {ward: total_expected_visits} and re-spread
    them across each area's retained buildings (#9/#15)."""

    def post(self, request, program_id, plan_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError

        try:
            payload = json.loads(request.body)
            targets = payload.get("targets") or {}
            if not isinstance(targets, dict):
                raise ValueError("targets must be an object {ward: number}")
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.set_area_targets(
                int(plan_id), targets, request.user.get_username(), base_revision=payload.get("revision")
            )
        except StalePlanError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=409)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans area targets failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Could not set visit targets."}, status=502)
        return JsonResponse(serialization.plan_to_json(plan))


class ProgramPlanFootprintsView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Building footprints (polygons + centroids) inside a saved plan's area.

    Used by the review-page 'Show footprints' toggle. Prefers the plan's stored
    `input_areas` (the original ward boundary from setup, already PG-cached from
    generation) over re-deriving from cell unions (which would be a different
    cache hash → expensive cold Overture query)."""

    def get(self, request, program_id, plan_id):
        import pandas as pd

        from connect_labs.microplans.core.area_input import resolve_area
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
        from connect_labs.microplans.core.footprints import fetch_buildings

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)

        # Fetch each input area on its OWN bbox and concat — unioning scattered
        # wards (e.g. GRID3 wards in different LGAs) makes one giant bbox that
        # trips the area-size guard. Fall back to the union geometry (cells) only
        # when the plan has no stored input_areas.
        inputs = plan.data.get("input_areas") or []
        geoms = []
        if inputs:
            for a in inputs:
                try:
                    geoms.append(resolve_area(a))
                except Exception:  # noqa: BLE001
                    logger.exception("plan footprints: input area resolve failed")
        if not geoms:
            union = serialization.plan_lookup_geometry(plan)
            if union is not None:
                geoms = [union]
        if not geoms:
            return JsonResponse(
                {"status": "ok", "footprints": {"type": "FeatureCollection", "features": []}, "count": 0}
            )
        try:
            frames = [fetch_buildings(g, min_confidence=None, with_geom=True) for g in geoms]
            df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=["lon", "lat"])
        except Exception:  # noqa: BLE001
            logger.exception("plan footprints fetch failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Footprints fetch failed."}, status=502)

        # Prefer the stored polygon when present; fall back to a centroid Point
        # for rows from before the polygon-on-cache rollout.
        features = []
        for _, row in df.iterrows():
            geom_json = row.get("geom_json") if hasattr(row, "get") else None
            if isinstance(geom_json, dict) and geom_json.get("type"):
                features.append({"type": "Feature", "geometry": geom_json, "properties": {}})
            else:
                features.append(
                    {
                        "type": "Feature",
                        "geometry": {"type": "Point", "coordinates": [float(row["lon"]), float(row["lat"])]},
                        "properties": {},
                    }
                )
        resp = JsonResponse(
            {"status": "ok", "footprints": {"type": "FeatureCollection", "features": features}, "count": len(features)}
        )
        # Footprints for a plan's area are derived from immutable PG-cached building
        # data — re-serializing the whole FeatureCollection on every page load is
        # wasted work. Let the browser cache it (private: it's auth-gated).
        resp["Cache-Control"] = "private, max-age=600"
        return resp


class ProgramPlanReassignView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Phase-2 op: re-apply the assignment strategy to a plan's groups (Celery-offloaded).

    Body: ``{"strategy", "workers", "restarts", "seed", "revision"}``. See
    ``core.assignment.AssignmentConfig`` for defaults.
    """

    def post(self, request, program_id, plan_id):
        try:
            payload = json.loads(request.body or "{}")
            payload = payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        base_revision = payload.pop("revision", None)
        return _enqueue_plan_mutation(
            request, "reassign", program_id, plan_id, {"assignment": payload, "revision": base_revision}
        )


class ProgramPlanRegenerateView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Destructive regenerate: wipe + rebuild a plan's work areas (Celery-offloaded).
    Same body shape as ProgramCreatePlanView sans name/region. Keeps the plan id;
    CHW assignments + per-area edits are reset."""

    def post(self, request, program_id, plan_id):
        empty_fc = {"type": "FeatureCollection", "features": []}
        try:
            payload = json.loads(request.body)
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            pins = payload.get("pins") or empty_fc
            hulls = (payload.get("coverage_areas") or payload.get("hulls")) or empty_fc
            input_areas = payload.get("input_areas") if isinstance(payload.get("input_areas"), list) else []
            grouping = payload.get("grouping") if isinstance(payload.get("grouping"), dict) else {}
            stats = payload.get("stats") if isinstance(payload.get("stats"), list) else None
            area_targets = payload.get("area_targets") if isinstance(payload.get("area_targets"), dict) else None
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _enqueue_plan_mutation(
            request,
            "regenerate",
            program_id,
            plan_id,
            {
                "mode": mode,
                "pins": pins,
                "hulls": hulls,
                "input_areas": input_areas,
                "grouping": grouping,
                "stats": stats,
                "area_targets": area_targets,
                "revision": payload.get("revision"),
            },
        )


class ProgramPlanRegroupView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Phase-1 op: re-apply the grouping strategy to a plan's cells (Celery-offloaded).

    Body: ``{"strategy", "target_buildings", "buffer_distance_m", "target_size", "revision",
    "max_buildings", "min_buildings", "max_reach_m"}``. All params optional; see
    `core.grouping.GroupingConfig` for defaults (the last three drive the undersized-
    group top-up pass — "max_buildings" is also accepted as a legacy alias for
    "target_buildings" when that key is absent, for plans saved before 2026-07).
    """

    def post(self, request, program_id, plan_id):
        try:
            payload = json.loads(request.body or "{}")
            payload = payload if isinstance(payload, dict) else {}
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        base_revision = payload.pop("revision", None)
        return _enqueue_plan_mutation(
            request, "regroup", program_id, plan_id, {"grouping": payload, "revision": base_revision}
        )


class ProgramPlanFootprintsRefreshView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Force a refresh of the footprint cache for this plan's area.

    Used to upgrade plans cached in the centroid-only era (pre-`geom_json`) to
    the new polygon-aware cache: deletes the matching `FootprintArea` row so the
    next footprints toggle re-fetches from Overture and stores polygons too.
    """

    def post(self, request, program_id, plan_id):
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
        from connect_labs.microplans.core.footprints import _area_cache_key
        from connect_labs.microplans.models import FootprintArea

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        area = serialization.plan_lookup_geometry(plan)
        if area is None:
            return JsonResponse({"status": "ok", "deleted": 0})
        deleted, _ = FootprintArea.objects.filter(area_hash=_area_cache_key(area.wkt)).delete()
        return JsonResponse({"status": "ok", "deleted": deleted})


class ProgramPlanCSVView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Serve the plan's work areas as a Connect-import CSV.

    Connect's WorkAreaCSVImporter REQUIRES non-empty LGA + State on every row (see
    microplans/CONNECT_IMPORT_CONTRACT.md) — a blank value gets the whole file
    rejected. LGA/State default from the plan (captured at creation; LGA falls back
    to the plan's region) so the "Download Connect import CSV" button — which POSTs
    an empty body — produces an importable file. An explicit lga/state in the
    request body overrides the plan values. State has no safe fallback: if the plan
    has none, the response carries an ``X-Microplan-Connect-Ready: false`` header +
    ``X-Microplan-Missing`` so the caller can warn before handing the file to
    Connect, rather than shipping a file that will be rejected.
    """

    def post(self, request, program_id, plan_id):
        from connect_labs.microplans.core import plan as plan_lib
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess
        from connect_labs.microplans.core.workarea import to_csv_rows

        try:
            payload = json.loads(request.body) if request.body else {}
        except json.JSONDecodeError:
            payload = {}
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        # Default LGA/State from the plan (LGA falls back to region); body overrides.
        plan_lga, plan_state = plan_lib.derive_lga_state(plan.data)
        lga = str(payload.get("lga") or plan_lga or "").strip()
        state = str(payload.get("state") or plan_state or "").strip()
        rows = to_csv_rows(plan_lib.to_workarea_payloads(plan.work_areas, lga=lga, state=state))
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="microplan_program{program_id}_plan{plan_id}.csv"'
        # Signal whether the file will be accepted by Connect (both labels present).
        missing = [k for k, v in (("LGA", lga), ("State", state)) if not v]
        response["X-Microplan-Connect-Ready"] = "false" if missing else "true"
        if missing:
            response["X-Microplan-Missing"] = ", ".join(missing)
        if rows:
            writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return response


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramSetupView(_LabsContextSyncMixin, _SimpleMicroplanOnlyInWorkspaceMixin, LoginRequiredMixin, TemplateView):
    """New-plan landing page. Renders the same template as the per-plan review
    page (review.html) but with plan_id=None — the template branches once on
    that to show the area-definition section open by default, no work areas,
    no result viewers. The first "Apply geographic frame" creates the plan via
    /plan/create/ and the page redirects to /plan/<id>/review/.
    """

    template_name = "microplans/review.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["plan_id"] = None
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        if not settings.MAPBOX_TOKEN:
            context["error"] = "MAPBOX_TOKEN is not configured; the map cannot load."
        # URLs shared with the review page. plan_url/edit/csv/etc. don't exist
        # yet (no plan), so leave them empty; the JS guards on null.
        context["create_plan_url"] = reverse("microplans:program_create_plan", args=[program_id])
        if self.request.GET.get("team"):
            # Workspace programs: the team the portfolio was creating for.
            context["create_plan_url"] += "?" + urlencode({"team": self.request.GET["team"]})
        context["program_url"] = reverse("microplans:program_workspace", args=[program_id])
        context["back_url"] = context["program_url"]
        context.update(_sd_urls())
        context["preview_coverage_url"] = reverse("microplans:preview_coverage", args=[123])
        context["preview_footprints_url"] = reverse("microplans:preview_footprints", args=[123])
        context["preview_area_stats_url"] = reverse("microplans:preview_area_stats", args=[123])
        context["preview_frame_url"] = reverse("microplans:preview_frame", args=[123])
        context["arm_comparability_url"] = reverse("microplans:arm_comparability", args=[123])
        context["compare_surrounding_url"] = reverse("microplans:compare_surrounding", args=[123])
        context["admin_areas_url"] = reverse("microplans:admin_areas", args=[123])
        context["admin_area_geometry_url"] = reverse("microplans:admin_area_geometry", args=[123])
        context["population_by_name_url"] = reverse("microplans:population_by_name", args=[123])
        context["countries_url"] = reverse("microplans:countries")
        context["boundary_viewport_url"] = reverse("microplans:boundary_viewport")
        # Review-only URLs that don't apply pre-create. JS will skip the
        # buttons that depend on them.
        for k in (
            "plan_url",
            "edit_url",
            "csv_url",
            "footprints_url",
            "regroup_url",
            "reassign_url",
            "regenerate_url",
            "compare_url",
        ):
            context[k] = ""
        return context


class ProgramComparePlansView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Compare N program plans' KPIs side by side. Returns per-plan KPIs so the UI
    can stack them with deltas; no composite score (the metrics themselves are the
    comparison)."""

    def get(self, request, program_id):
        from connect_labs.microplans.core import plan as plan_lib
        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        try:
            ids = [int(x) for x in (request.GET.get("plans", "")).split(",") if x.strip()]
        except ValueError:
            return JsonResponse({"status": "error", "detail": "plans must be comma-separated ids"}, status=400)
        if not ids:
            return JsonResponse({"status": "error", "detail": "no plans selected"}, status=400)

        da = ProgramPlanDataAccess(program_id, request=request)
        # One API round-trip for the whole program, then filter — instead of one
        # get_plan() per id (the comparison set is a handful of a program's plans).
        try:
            by_id = {p.id: p for p in da.list_plans()}
        except Exception:  # noqa: BLE001
            logger.exception("microplans program compare: list_plans failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not load plans."}, status=502)
        entries = []
        for pid in ids:  # preserve the requested order
            p = by_id.get(pid)
            if p is None:
                continue
            _ss = (p.data.get("sampling_stats") or [{}])[0]
            entries.append(
                {
                    "plan_id": p.id,
                    "name": p.name or f"Plan {p.id}",
                    "region": p.region,
                    "mode": p.mode,
                    "created_at": p.created_at,
                    "kpis": plan_lib.plan_kpis(
                        p.work_areas,
                        input_areas=p.data.get("input_areas") or [],
                        area_buildings=_ss.get("after_filters") or _ss.get("fetched") or None,
                    ),
                }
            )
        if not entries:
            return JsonResponse({"status": "error", "detail": "no plans found."}, status=404)
        return JsonResponse({"status": "ok", "plans": entries})


class MetricGlossaryView(LoginRequiredMixin, TemplateView):
    """Definitions for every metric shown on the compare page.

    Program-scope-agnostic — the vocabulary is the same across programs, so
    there's one glossary page everyone links to. Linked from compare.html via
    a "What do these metrics mean?" link in the sidebar; future plan-review
    surfaces can link the same URL.
    """

    template_name = "microplans/metric_glossary.html"


class ProgramWorkspaceSettingsView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program Managers' access page for a workspace program (``labs.workspaces``).

    Two lists, each searchable and paged in the browser, every delete confirmed
    in a dialog first:

      * **LLOs** -- the workspace's partner organisations, by Connect organisation
        slug. Every member of one is a Network Manager of it here. Deleting an LLO
        takes that away and hands its Simple Microplans to Program Managers (team
        cleared; nothing is deleted).
      * **Users** -- explicit access by email address (any address, signed in yet
        or not): Program Manager, or Network Manager of an LLO, switchable in
        place. An entry wins over every other rule; deleting one returns that
        person to the other rules. Nobody may change their own entry in a way
        that would stop them getting back to Program Manager by themselves.

    404 for anyone but a Program Manager who signs in with a Dimagi address
    (``labs.workspaces.can_manage_access``), on every action: a Program Manager by
    a Users entry or as an organisation admin, outside Dimagi, does not manage
    access. For a labs-only program
    whose organisation is not a workspace yet, Dimagi staff -- and only they --
    get a "Turn on workspace roles" button instead: the browser route to what
    ``setup_autoconnect_workspace --program-id`` does, by the same function
    (``labs.workspaces.adopt_program``). Everyone else gets a 404 for the page and
    a 403 for the button.

    The workspace's older ``connect_program_ids`` (Network Managers by delivering
    in a linked Connect program) still count but are no longer edited here; the
    management command still sets them."""

    template_name = "microplans/workspace_settings.html"
    LIST_PAGE_SIZE = 10

    def _can_turn_on(self, program_id) -> bool:
        """Dimagi staff, on a labs-only program that is not a workspace yet."""
        from connect_labs.labs.synthetic.local_records_backend import is_labs_only_program_id
        from connect_labs.labs.workspaces import workspace_for_program
        from connect_labs.utils.dimagi_user import is_dimagi_staff

        return (
            is_dimagi_staff(self.request.user)
            and is_labs_only_program_id(program_id)
            and workspace_for_program(program_id) is None
        )

    def _workspace_and_role(self, program_id):
        from connect_labs.labs.workspaces import can_manage_access, role_in, workspace_for_program

        if not can_manage_access(self.request.user):
            raise Http404("Not found.")
        workspace = workspace_for_program(program_id)
        role = role_in(self.request.user, workspace)
        if workspace is None or role is None or not role.is_program_manager:
            raise Http404("Not found.")
        return workspace, role

    def _known_orgs(self, workspace) -> dict:
        """Connect organisations this Program Manager's own tree names: slug -> name."""
        from connect_labs.labs.workspaces import connect_org_data, delivering_orgs, member_slugs

        org_data = connect_org_data(self.request.user) or {}
        known = {
            str(o["slug"]): o.get("name") or str(o["slug"])
            for o in org_data.get("organizations") or []
            if o.get("slug") and str(o["slug"]) in member_slugs(org_data)
        }
        for slug, name in delivering_orgs(org_data, workspace.connect_program_ids).items():
            known.setdefault(slug, name)
        return known

    def get_context_data(self, **kwargs):
        from collections import defaultdict

        from django.db.models import Count
        from django.urls import reverse

        from connect_labs.labs.models import WorkspaceAccess, WorkspaceUser
        from connect_labs.labs.workspaces import NETWORK_MANAGER, entry_is_for, is_dimagi_email
        from connect_labs.microplans.models import SimpleMicroplan

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        if self._can_turn_on(program_id):
            back_url = reverse("microplans:program_workspace", args=[program_id])
            context.update({"program_id": program_id, "workspace": None, "can_turn_on": True, "back_url": back_url})
            return context
        workspace, _role = self._workspace_and_role(program_id)
        user = self.request.user
        known = self._known_orgs(workspace)
        plan_counts = dict(
            SimpleMicroplan.objects.filter(program_id=program_id)
            .values("team")
            .annotate(n=Count("pk"))
            .values_list("team", "n")
        )
        # The people each LLO has brought in: Network Managers by membership, as
        # noted when their role resolved (labs.workspaces.note_access).
        people = defaultdict(list)
        for seen in (
            WorkspaceAccess.objects.filter(workspace=workspace, role=NETWORK_MANAGER, explicit=False)
            .select_related("user")
            .order_by("-last_seen")
        ):
            for team in seen.teams or []:
                people[team].append(seen)
        llos = []
        for llo in workspace.llos.select_related("added_by").order_by("created_at", "pk"):
            name = llo.name or known.get(llo.slug) or ""
            members = people.get(llo.slug, [])
            llos.append(
                {
                    "slug": llo.slug,
                    "name": name,
                    "people": members,
                    "microplans": plan_counts.get(llo.slug, 0),
                    "added_by": llo.added_by,
                    "created_at": llo.created_at,
                    "search": " ".join(
                        [llo.slug, name]
                        + [f"{getattr(p.user, 'name', '')} {p.user.email} {p.user.username}" for p in members]
                    ),
                }
            )
        llo_names = {r["slug"]: r["name"] or r["slug"] for r in llos}
        users = []
        for entry in workspace.users.select_related("user", "added_by").order_by("created_at", "pk"):
            is_nm = entry.role == WorkspaceUser.NETWORK_MANAGER
            team_name = llo_names.get(entry.team) or entry.team
            users.append(
                {
                    "id": entry.pk,
                    "email": entry.email,
                    "user": entry.user,
                    "role": entry.role,
                    "is_nm": is_nm,
                    "team": entry.team,
                    "team_name": team_name,
                    "team_is_llo": entry.team in llo_names,
                    "access": f"{entry.role}:{entry.team}" if is_nm else entry.role,
                    "label": f"Network Manager of {team_name}" if is_nm else "Program Manager",
                    "is_self": entry_is_for(entry, user),
                    "is_dimagi": is_dimagi_email(entry.email),
                    "added_by": entry.added_by,
                    "created_at": entry.created_at,
                    "search": " ".join(
                        [
                            entry.email,
                            getattr(entry.user, "name", "") if entry.user else "",
                            "network manager" if is_nm else "program manager",
                            entry.team,
                            team_name,
                        ]
                    ),
                }
            )
        context.update(
            {
                "program_id": program_id,
                "workspace": workspace,
                "llos": llos,
                "users": users,
                "llo_choices": [{"slug": r["slug"], "name": r["name"] or r["slug"]} for r in llos],
                "known_orgs": sorted(
                    ({"slug": s, "name": n} for s, n in known.items() if s not in llo_names),
                    key=lambda o: o["name"].lower(),
                ),
                "page_size": self.LIST_PAGE_SIZE,
                "back_url": reverse("microplans:program_simple_list", args=[program_id]),
            }
        )
        return context

    def post(self, request, program_id):
        from django.contrib import messages
        from django.shortcuts import redirect

        action = request.POST.get("action")
        if action == "turn_on":
            return self._turn_on(request, program_id)
        workspace, _role = self._workspace_and_role(program_id)
        handler = {
            "add_llo": self._add_llo,
            "remove_llo": self._remove_llo,
            "add_user": self._set_user,
            "change_user": self._set_user,
            "remove_user": self._remove_user,
        }.get(action)
        if handler is None:
            messages.error(request, "Nothing was changed.")
            return redirect(request.path)
        return handler(request, workspace, int(program_id))

    # ---- LLOs ----

    def _add_llo(self, request, workspace, program_id):
        from django.contrib import messages
        from django.shortcuts import redirect

        from connect_labs.labs.workspaces import AccessRefused, add_llo

        try:
            llo = add_llo(
                workspace,
                request.POST.get("slug", ""),
                request.POST.get("name", ""),
                by=request.user,
                program_id=program_id,
                known_names=self._known_orgs(workspace),
            )
        except AccessRefused as e:
            messages.error(request, f"LLO not added: {e}.")
        else:
            messages.success(request, f"{llo.name or llo.slug} is an LLO: its members are Network Managers here.")
        return redirect(f"{request.path}#llos")

    def _remove_llo(self, request, workspace, program_id):
        from django.contrib import messages
        from django.shortcuts import redirect

        from connect_labs.labs.workspaces import clean_slug, remove_llo
        from connect_labs.microplans.models import SimpleMicroplan

        slug = clean_slug(request.POST.get("slug"))
        if not workspace.llos.filter(slug=slug).exists():
            raise Http404("Not found.")
        # Its microplans stay, for Program Managers only: nothing is deleted.
        cleared = SimpleMicroplan.objects.filter(program_id=program_id, team=slug).update(team="")
        remove_llo(
            workspace, slug, by=request.user, program_id=program_id, metadata={"microplans_made_pm_only": cleared}
        )
        messages.success(
            request,
            f"{slug} is no longer an LLO: its members lost access, and its {cleared} microplan(s) "
            "are Program Managers' only.",
        )
        return redirect(f"{request.path}#llos")

    # ---- Users ----

    def _would_lock_out(self, request, workspace, *, keeps_pm: bool) -> bool:
        """Whether changing the caller's own entry would leave them unable to get
        back to Program Manager by themselves: fine if the entry keeps them one,
        or if without it they are one anyway."""
        from connect_labs.labs.workspaces import fallback_role

        if keeps_pm:
            return False
        usual = fallback_role(request.user, workspace)
        return usual is None or not usual.is_program_manager

    def _set_user(self, request, workspace, program_id):
        from django.contrib import messages
        from django.shortcuts import redirect
        from django.urls import reverse

        from connect_labs.labs.models import WorkspaceUser
        from connect_labs.labs.workspaces import AccessRefused, clean_email, entry_is_for, set_user

        role, _, team = (request.POST.get("access") or "").partition(":")
        existing = None
        if request.POST.get("action") == "change_user":
            try:
                existing = workspace.users.get(pk=int(request.POST.get("entry_id", "")))
            except (ValueError, WorkspaceUser.DoesNotExist):
                raise Http404("Not found.") from None
            email = existing.email
        else:
            email = request.POST.get("email", "")
        try:
            email = clean_email(email)
        except AccessRefused as e:
            messages.error(request, f"User not added: {e}.")
            return redirect(f"{request.path}#users")
        if existing is None:
            existing = workspace.users.filter(email=email).first()
        is_self = entry_is_for(existing, request.user) if existing else email == (request.user.email or "").lower()
        keeps_pm = role == WorkspaceUser.PROGRAM_MANAGER
        if is_self and self._would_lock_out(request, workspace, keeps_pm=keeps_pm):
            messages.error(
                request,
                "You can't make your own entry Network Manager: you would lose Program Manager access here "
                "and could not change it back. Ask another Program Manager.",
            )
            return redirect(f"{request.path}#users")
        try:
            entry, created = set_user(
                workspace,
                email,
                role,
                team,
                by=request.user,
                program_id=program_id,
                keep_team=existing.team if existing else "",
            )
        except AccessRefused as e:
            messages.error(request, f"User not {'added' if existing is None else 'changed'}: {e}.")
            return redirect(f"{request.path}#users")
        label = "a Program Manager" if keeps_pm else f"a Network Manager of {entry.team}"
        messages.success(request, f"{entry.email} {'is now' if created else 'changed to'} {label}.")
        if is_self and not keeps_pm:
            # This page is Program Managers' only: land where they can now go.
            return redirect(reverse("microplans:program_simple_list", args=[program_id]))
        return redirect(f"{request.path}#users")

    def _remove_user(self, request, workspace, program_id):
        from django.contrib import messages
        from django.shortcuts import redirect

        from connect_labs.labs.models import WorkspaceUser
        from connect_labs.labs.workspaces import entry_is_for, remove_user

        try:
            entry = workspace.users.get(pk=int(request.POST.get("entry_id", "")))
        except (ValueError, WorkspaceUser.DoesNotExist):
            raise Http404("Not found.") from None
        if entry_is_for(entry, request.user) and self._would_lock_out(request, workspace, keeps_pm=False):
            messages.error(
                request,
                "You can't delete your own entry: without it you would not be a Program Manager here. "
                "Ask another Program Manager.",
            )
            return redirect(f"{request.path}#users")
        remove_user(workspace, entry.pk, by=request.user, program_id=program_id)
        messages.success(request, f"{entry.email} removed from Users: the usual rules apply to them again.")
        return redirect(f"{request.path}#users")

    def _turn_on(self, request, program_id):
        from django.contrib import messages
        from django.core.exceptions import PermissionDenied
        from django.shortcuts import redirect

        from connect_labs.labs.synthetic.local_records_backend import is_labs_only_program_id
        from connect_labs.labs.workspaces import AdoptRefused, adopt_program
        from connect_labs.utils.dimagi_user import is_dimagi_staff

        if not is_dimagi_staff(request.user):
            raise PermissionDenied("Only Dimagi staff can turn on workspace roles.")
        if not is_labs_only_program_id(program_id):
            raise Http404("Not found.")
        try:
            _workspace, _opp, created = adopt_program(program_id, user=request.user)
        except AdoptRefused as e:
            messages.error(request, f"Workspace roles were not turned on: {e}.")
            return redirect(request.path)
        if created:
            messages.success(request, "Workspace roles are on. Add the LLOs and Users below.")
        else:
            messages.info(request, "Workspace roles were already on.")
        return redirect(request.path)


class ProgramWorkspaceStopTestingView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """POST: a Dimagi user a Users entry makes a Network Manager switches back to
    Program Manager by deleting their own entry -- only when, without it, they ARE
    one (so they could set it again). 404 for anyone outside Dimagi
    (``labs.workspaces.can_manage_access``): their entry is simply their role."""

    def post(self, request, program_id):
        from django.contrib import messages
        from django.http import HttpResponseRedirect
        from django.urls import reverse

        from connect_labs.labs.models import WorkspaceUser
        from connect_labs.labs.workspaces import (
            can_manage_access,
            fallback_role,
            remove_user,
            user_entry,
            workspace_for_program,
        )

        workspace = workspace_for_program(program_id)
        if workspace is None or not can_manage_access(request.user):
            raise Http404("Not found.")
        entry = user_entry(request.user, workspace)
        usual = fallback_role(request.user, workspace)
        if entry is None or entry.role != WorkspaceUser.NETWORK_MANAGER:
            pass
        elif usual is not None and usual.is_program_manager:
            remove_user(workspace, entry.pk, by=request.user, program_id=int(program_id))
            messages.success(request, "You are a Program Manager here again.")
        else:
            messages.error(request, "Ask a Program Manager to change your access.")
        return HttpResponseRedirect(reverse("microplans:program_simple_list", args=[program_id]))


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramComparePageView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program-scoped plan comparison page (reuses compare.html via context URLs)."""

    template_name = "microplans/compare.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["scope_label"] = f"Program #{program_id}"
        context["list_url"] = reverse("microplans:program_plans", args=[program_id])
        context["compare_url"] = reverse("microplans:program_plan_compare", args=[program_id])
        context["back_url"] = reverse("microplans:program_workspace", args=[program_id])
        return context


# ---------------------------------------------------------------------------
# Service-delivery GPS overlay (reusable layer; see microplans/service_delivery/)
# ---------------------------------------------------------------------------
class _ServiceDeliveryMixin:
    """Restrict posted opp_ids to opportunities the user can actually see."""

    def _allowed_opp_ids(self, request) -> set[int]:
        from connect_labs.labs.context import get_org_data

        ids = set()
        for o in get_org_data(request).get("opportunities", []):
            if o.get("id") is not None:
                ids.add(int(o["id"]))
        return ids


class PreviewServiceDeliveryView(_ProgramScopeMixin, _ServiceDeliveryMixin, LoginRequiredMixin, View):
    """Fetch service-delivery GPS points for one or more opportunities.

    POST {opp_ids: [int], pipeline_id?: int}. Each opp's points are fetched via
    the ServiceDeliveryPoints provider (default GPS pipeline, or the given
    pipeline_id), colored per opp, and merged into one FeatureCollection. Mirrors
    PreviewFootprintsView's request/response shape so the FE layer code is uniform.
    """

    def post(self, request, opp_id):
        from connect_labs.labs.context import get_org_data
        from connect_labs.microplans.service_delivery.points import (
            color_for,
            downsample_features,
            fetch_points,
            points_to_geojson,
        )

        try:
            payload = json.loads(request.body)
            opp_ids = [int(x) for x in payload.get("opp_ids") or []]
            pipeline_id = payload.get("pipeline_id")
            pipeline_id = int(pipeline_id) if pipeline_id not in (None, "", "default") else None
        except (json.JSONDecodeError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        if not opp_ids:
            opp_ids = [int(opp_id)]
        allowed = self._allowed_opp_ids(request)
        opp_ids = [oid for oid in opp_ids if oid in allowed]
        if not opp_ids:
            return JsonResponse({"status": "error", "detail": "No accessible opportunities selected."}, status=403)

        names = {
            int(o["id"]): (o.get("name") or f"Opportunity #{o['id']}")
            for o in get_org_data(request).get("opportunities", [])
            if o.get("id") is not None
        }

        all_features, layers, auth_error = [], [], None
        for i, oid in enumerate(opp_ids):
            color = color_for(i)
            result = fetch_points(oid, request=request, pipeline_id=pipeline_id)
            if result.get("auth_error") and auth_error is None:
                auth_error = {
                    "auth_error": result["auth_error"],
                    "auth_error_domain": result.get("auth_error_domain"),
                    "auth_authorize_url": result.get("auth_authorize_url"),
                }
            fc = points_to_geojson(result["points"], opportunity_id=oid, color=color)
            all_features.extend(fc["features"])
            layers.append(
                {
                    "opportunity_id": oid,
                    "name": names.get(oid, f"Opportunity #{oid}"),
                    "color": color,
                    "stats": result["stats"],
                    "error": result.get("error"),
                }
            )

        shown_features, sampled, total = downsample_features(all_features)
        if sampled:
            logger.warning("microplans SD overlay capped: %s→%s points (opps=%s)", total, len(shown_features), opp_ids)
        body = {
            "status": "ok",
            "points": {"type": "FeatureCollection", "features": shown_features},
            "layers": layers,
            "count": len(shown_features),
            "total": total,
            "sampled": sampled,
        }
        if auth_error:
            body.update(auth_error)
        return JsonResponse(body)


class ServiceDeliveryPipelinesView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """List visit-level pipelines the user can pick instead of the default GPS one."""

    def get(self, request, opp_id):
        from connect_labs.workflow.data_access import PipelineDataAccess

        pipelines = [{"id": "default", "name": "Default — device GPS (any app)"}]
        try:
            pda = PipelineDataAccess(opportunity_id=opp_id, request=request)
            for d in pda.list_definitions(include_shared=True):
                schema = getattr(d, "schema", None) or {}
                if schema.get("terminal_stage", "aggregated") == "visit_level":
                    pipelines.append({"id": d.id, "name": d.name})
        except Exception:  # noqa: BLE001 — dropdown is best-effort
            logger.exception("microplans service_delivery_pipelines failed (opp=%s)", opp_id)
        return JsonResponse({"status": "ok", "pipelines": pipelines})


class DeriveBoundaryView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Derive a boundary polygon from a posted service-delivery point cloud.

    POST {coords: [[lon,lat],...], method?, concavity?, buffer_m?}. Returns the
    boundary as a GeoJSON Feature so the FE can drop it straight into the draw
    layer / area-input path used by sampling and coverage.
    """

    def post(self, request, opp_id):
        from connect_labs.microplans.service_delivery.hull import derive_boundary

        try:
            payload = json.loads(request.body)
            coords = payload["coords"]
            points = [{"lon": float(c[0]), "lat": float(c[1])} for c in coords]
            method = payload.get("method", "concave")
            concavity = float(payload.get("concavity", 0.3))
            buffer_m = float(payload.get("buffer_m", 25.0))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError, IndexError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            geometry = derive_boundary(points, method=method, concavity=concavity, buffer_m=buffer_m)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans derive_boundary failed (opp=%s)", opp_id)
            return JsonResponse({"status": "error", "detail": "Boundary derivation failed."}, status=502)

        return JsonResponse(
            {
                "status": "ok",
                "boundary": {"type": "Feature", "geometry": geometry, "properties": {"source": "service_delivery"}},
                "point_count": len(points),
            }
        )


class ArmComparabilityView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Comparability for a SINGLE two-arm plan — the same PSU/SMD engine + panel the
    study-group page uses, so both surfaces render one identical comparison.

    POST {stats: [<per-arm sampling_stats>, ...], names?: {arm: label}}. Each entry is
    a sampled arm's summary (``psu_size``/``psu_density``/``bldg_area`` as (mean, sd),
    ``n_psus``, ``arm``) — exactly what ``frame.py`` produced and the client already
    holds. Delegates to the shared ``psu_arms_from_stats`` + ``arm_comparability_psu``
    and returns the rendered ``_arm_comparability.html`` partial so the plan review page
    injects the same markup the group page includes server-side.
    """

    def post(self, request, opp_id):
        from django.template.loader import render_to_string

        from connect_labs.microplans.core.comparability import arm_comparability_psu, psu_arms_from_stats

        try:
            payload = json.loads(request.body)
            stats = payload.get("stats") or []
            names = payload.get("names") or {}
        except (json.JSONDecodeError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        arms = psu_arms_from_stats(stats, names=names)
        comparability = arm_comparability_psu(arms)
        # Only render the panel when there are two arms to compare; otherwise hand back
        # empty HTML so the client hides the (otherwise stale) panel.
        html = (
            render_to_string("microplans/_arm_comparability.html", {"comparability": comparability})
            if comparability.get("metrics")
            else ""
        )
        return JsonResponse({"status": "ok", "matched": comparability.get("matched"), "html": html})


# Bulk-create flow — the "paste a ward list" form + the server-side
# materializer that turns confirmed boundary IDs into N draft plans in one
# call. See spec: docs/walkthroughs/microplans-10-wards.yaml scene 2/3, spine
# items `name-match-and-confirm` + `bulk-input-ui`.
# ---------------------------------------------------------------------------


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramBulkCreatePlanPageView(
    _LabsContextSyncMixin, _SimpleMicroplanOnlyInWorkspaceMixin, LoginRequiredMixin, TemplateView
):
    """Render the bulk-create form (paste textarea + resolution preview + Create Plans)."""

    template_name = "microplans/bulk_create.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        from connect_labs.labs.admin_boundaries.models import AdminBoundary

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["program_url"] = reverse("microplans:program_workspace", args=[program_id])
        context["bulk_create_url"] = reverse("microplans:program_bulk_create", args=[program_id])
        if self.request.GET.get("team"):
            # Workspace programs: the team the portfolio was creating for.
            context["bulk_create_url"] += "?" + urlencode({"team": self.request.GET["team"]})
        context["resolve_many_url"] = "/labs/admin/boundaries/resolve_many/"
        # Sensible defaults for the Kano RCT demo arm.
        context["default_iso"] = "NGA"
        context["default_admin_level"] = 3
        context["default_source"] = "geopode"
        context["default_mode"] = "coverage"
        # Freshness stamp: the latest boundary load date for the default
        # iso/source. Surfaced in the resolved-wards subhead so the lead knows
        # which Nigeria shape set the matches came from. Skipped silently if
        # no boundaries are loaded for the default — UI just hides the line.
        latest = (
            AdminBoundary.objects.filter(iso_code="NGA", source="geopode")
            .order_by("-downloaded_at")
            .values_list("downloaded_at", flat=True)
            .first()
        )
        context["boundary_freshness"] = latest.date().isoformat() if latest else ""
        return context


class ProgramBulkCreatePlansView(_ProgramScopeMixin, LoginRequiredMixin, View):
    """Enqueue a batch create of N draft plans from confirmed admin boundaries.

    Body: ``{"plans": [{"boundary_id", "name"}], "mode", "grouping", "cell_size_m"}``.
    Coverage plans are gridded into work areas on the Celery worker (the Overture
    coverage generator). Sampling plans are created boundary-only and sampled later
    via the study "Generate" action — see ``bulk_create_plans_task`` and
    ``PlanRecord.phase``. Validates + enqueues and returns ``202 {task_id, poll_url}``;
    the client polls ``microplans:bulk_create_status`` for incremental per-ward results."""

    def post(self, request, program_id):
        from connect_labs.microplans.tasks import bulk_create_plans_task

        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"status": "error", "detail": "invalid JSON"}, status=400)

        plans_input = payload.get("plans") or []
        mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
        grouping = payload.get("grouping") if isinstance(payload.get("grouping"), dict) else {}
        # Full coverage parameter surface (confidence gate, sources, area/cell filters,
        # population weighting) — everything on CoverageConfig beyond cell_size_m. Passed
        # straight through to CoverageConfig.from_payload so the plan captures it.
        coverage_config = payload.get("coverage_config") if isinstance(payload.get("coverage_config"), dict) else {}
        # Optional: file every created plan into this group (group-page "Add wards").
        group_id = payload.get("group_id")
        try:
            cell_size_m = float(payload.get("cell_size_m")) if payload.get("cell_size_m") is not None else 100.0
        except (TypeError, ValueError):
            cell_size_m = 100.0
        if not isinstance(plans_input, list) or not plans_input:
            return JsonResponse({"status": "error", "detail": "`plans` must be a non-empty list"}, status=400)

        access_token = (request.session.get("labs_oauth") or {}).get("access_token")
        if not access_token:
            return JsonResponse(
                {"status": "error", "detail": "Not authenticated with Connect — sign in again."}, status=401
            )

        from django.urls import reverse

        from connect_labs.microplans.core.data_access import ProgramPlanDataAccess

        # Workspace programs: the worker has no caller to take a team from, so it
        # is decided here -- and a group to file into must be one this caller sees.
        da = ProgramPlanDataAccess(program_id, request=request)
        if group_id is not None and da.workspace is not None and da.get_group(int(group_id)) is None:
            return JsonResponse({"status": "error", "detail": "Group not found."}, status=404)
        team = _new_record_team(da, payload.get("team") or request.GET.get("team"), group_id)

        task = bulk_create_plans_task.delay(
            int(program_id),
            plans_input,
            mode,
            grouping,
            cell_size_m,
            access_token,
            group_id=int(group_id) if group_id is not None else None,
            coverage_config=coverage_config,
            actor=request.user.get_username() if hasattr(request.user, "get_username") else str(request.user),
            team=team,
        )
        from connect_labs.microplans.models import TaskOwner

        TaskOwner.record(task.id, request.user)
        return JsonResponse(
            {
                "status": "queued",
                "task_id": task.id,
                "poll_url": reverse("microplans:bulk_create_status", args=[task.id]),
            },
            status=202,
        )


class ProgramBulkCreateStatusView(LoginRequiredMixin, View):
    """Poll a bulk-create task — returns incremental per-ward results so the page can
    flip each row's pill as its ward finishes (queued | running | completed |
    failed). Mirrors PreviewStatusView's lifecycle mapping. Only the user who started
    the task can read it; anyone else gets the same 404 as an unknown id."""

    def get(self, request, task_id):
        from celery.result import AsyncResult

        if (denied := _not_your_task(request, task_id)) is not None:
            return denied
        result = AsyncResult(task_id)
        state = result.state
        info = result.info if isinstance(result.info, dict) else {}
        if state == "PENDING":
            return JsonResponse({"state": "queued", "results": [], "created": 0, "total": 0})
        if state in ("RECEIVED", "STARTED", "PROGRESS"):
            return JsonResponse(
                {
                    "state": "running",
                    "results": info.get("results", []),
                    "created": info.get("created", 0),
                    "total": info.get("total", 0),
                }
            )
        if state == "SUCCESS":
            payload = result.result if isinstance(result.result, dict) else {}
            return JsonResponse(
                {
                    "state": "completed",
                    "results": payload.get("results", []),
                    "created": payload.get("created", 0),
                    "total": payload.get("total", 0),
                }
            )
        if state == "FAILURE":
            logger.error("microplans bulk_create task %s failed: %s", task_id, result.info)
            return JsonResponse({"state": "failed", "detail": "Bulk create failed. Check server logs."})
        return JsonResponse({"state": state.lower(), "results": [], "created": 0, "total": 0})


class MicroplansHubView(LoginRequiredMixin, TemplateView):
    """`/microplans/`: the overview's Microplans tile opens here, onto CHC, CHC WA Revisit (R1 and R2)
    and Simple Microplan."""

    template_name = "microplans/hub.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        labs_context = getattr(self.request, "labs_context", {}) or {}
        # The program picked in the context picker; 217 when none is picked.
        program_id = labs_context.get("program_id") or 217
        context["chc_url"] = f"/microplans/program/{program_id}/"
        context["wa_revisit_url"] = f"/wa-revisit/program/{program_id}/"
        context["wa_revisit_r2_url"] = f"/wa-revisit/r2/program/{program_id}/"
        # A workspace program (labs.workspaces) makes its microplans in Simple
        # Microplan only, filed under the program and the person's team: the hub
        # offers just that tile, opening the program's list (not the global test
        # plans everyone can see).
        from connect_labs.microplans.access import workspace_simple_microplan_url

        context["workspace_simple_url"] = workspace_simple_microplan_url(labs_context.get("program_id"))
        return context
