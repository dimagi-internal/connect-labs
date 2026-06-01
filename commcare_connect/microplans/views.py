"""Views for microplans: stateless preview/boundary/service-delivery utilities +
the program-scoped plan portfolio (create, review/edit, compare, groups). The
legacy opportunity-scoped setup/plan flow was removed once the program flow
superseded it."""

import csv
import json
import logging

from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.utils.decorators import method_decorator
from django.views import View
from django.views.decorators.csrf import ensure_csrf_cookie
from django.views.generic import TemplateView

from commcare_connect.microplans import serialization

logger = logging.getLogger(__name__)


def _float_or_none(raw):
    try:
        return float(raw) if raw not in (None, "") else None
    except (ValueError, TypeError):
        return None


class _LabsContextSyncMixin:
    """Sync the labs context picker pill to the program_id / opp_id in this view's
    URL kwargs. Without it, the picker stays on "Select Context" on every
    microplans page because the labs middleware reads context from query params
    only — but these routes use path params (e.g. /microplans/program/135/).
    Result: users had to manually pick the program in the context picker even
    though the URL already says which program they're on. Now the picker just
    reflects the page on first load.
    """

    def dispatch(self, request, *args, **kwargs):
        if getattr(request, "user", None) is not None and request.user.is_authenticated:
            from commcare_connect.labs.context import save_context_to_session, validate_context_access

            ctx = {}
            if "program_id" in kwargs:
                ctx["program_id"] = int(kwargs["program_id"])
            elif "opp_id" in kwargs:
                ctx["opportunity_id"] = int(kwargs["opp_id"])
            if ctx:
                validated = validate_context_access(request, ctx) or {}
                # If the cached OAuth org_data didn't include this program/opp,
                # validate gives us the id but no display object — synthesize a
                # minimal one so the picker pill at least shows the URL's program
                # rather than leaving the previous selection stuck on screen.
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
        return super().dispatch(request, *args, **kwargs)


def _sd_urls(opp_id=123):
    """Service-delivery layer endpoints for the unified page.

    opp_id is a routing placeholder (same trick as the area-definition URLs): the
    POST body's ``opp_ids`` drive the actual fetch and are validated against the
    user's accessible opportunities, so the URL's opp_id is irrelevant.
    """
    from django.urls import reverse

    return {
        "preview_service_delivery_url": reverse("microplans:preview_service_delivery", args=[opp_id]),
        "service_delivery_pipelines_url": reverse("microplans:service_delivery_pipelines", args=[opp_id]),
        "derive_boundary_url": reverse("microplans:derive_boundary", args=[opp_id]),
    }


def _program_map_seed(plans) -> dict | None:
    """Where to open the new-plan map so the Boundaries layer actually loads.

    The new-plan map otherwise opens zoomed out to a whole country with nothing
    drawn — so no boundary is clickable AND the country never auto-detects (the
    global Overture source needs an iso, which is itself inferred from loaded
    boundaries: a cold-start chicken-and-egg). Seeding the map over the program's
    existing footprint breaks it: boundaries load there, the country detects, and
    the by-name search starts working.

    Scans the program's plans newest-first for an admin-area country (carried on
    ``input_areas``) and a centroid (mean of work-area centroids). Returns
    ``{iso, lng, lat, zoom}`` or ``None`` when the program has no usable footprint
    yet (a brand-new program — the map keeps its default and the user pans)."""
    for p in sorted(plans, key=lambda x: x.data.get("created_at", ""), reverse=True):
        data = p.data
        iso = ""
        for area in data.get("input_areas") or []:
            if isinstance(area, dict) and area.get("country"):
                iso = str(area["country"])
                break
        pts = [w.get("centroid") for w in (data.get("work_areas") or []) if w.get("centroid")]
        if pts:
            lng = sum(pt[0] for pt in pts) / len(pts)
            lat = sum(pt[1] for pt in pts) / len(pts)
            return {"iso": iso, "lng": round(lng, 5), "lat": round(lat, 5), "zoom": 10}
        if iso:
            return {"iso": iso, "lng": None, "lat": None, "zoom": None}
    return None


def _queued(task):
    """202 envelope for an enqueued generation task: id + where to poll it."""
    from django.urls import reverse

    return JsonResponse(
        {
            "status": "queued",
            "task_id": task.id,
            "poll_url": reverse("microplans:preview_status", args=[task.id]),
        },
        status=202,
    )


class PreviewFrameView(LoginRequiredMixin, View):
    """Enqueue the sampling preview for the drawn area(s) and return a task id.

    The first fetch for an area hits Overture S3 (~tens of seconds), which used
    to block a web worker for the whole request. Generation now runs on the
    Celery worker (see ``microplans/tasks.py``); this view validates the request
    synchronously (cheap) and enqueues — the client polls
    ``microplans:preview_status`` for the sampled pins + cluster hulls.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.sampling.frame import FrameConfig
        from commcare_connect.microplans.tasks import generate_frame_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config_payload = payload.get("config", {})
            FrameConfig.from_payload(config_payload)  # validate now → 400, not a failed task
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(generate_frame_task.delay(areas, config_payload))


class PreviewCoverageView(LoginRequiredMixin, View):
    """Enqueue the coverage-mode preview (balanced/grid clusters → polygons).

    Same offload contract as :class:`PreviewFrameView`: validate synchronously,
    enqueue the cold Overture fetch + clustering onto the Celery worker, return a
    task id for the client to poll.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.coverage.frame import CoverageConfig
        from commcare_connect.microplans.tasks import generate_coverage_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config_payload = payload.get("config", {})
            CoverageConfig.from_payload(config_payload)  # validate now → 400, not a failed task
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(generate_coverage_task.delay(areas, config_payload))


class PreviewFootprintsView(LoginRequiredMixin, View):
    """Enqueue a building-footprints fetch (as point features) for the area(s).

    Used by the "Show building footprints" toggle to sanity-check an area before
    generating cells. Reuses the PG-cached `fetch_buildings` path, but the cold
    fetch is offloaded to Celery like the other previews; the client polls
    ``microplans:preview_status``.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.tasks import fetch_footprints_task

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        return _queued(fetch_footprints_task.delay(areas))


class PreviewStatusView(LoginRequiredMixin, View):
    """Poll a queued preview/generation task.

    Lifecycle is reported in ``state`` (queued | running | completed | failed).
    On completion the task's own response envelope — which carries its own
    ``status`` of ``ok`` / ``error`` plus the data — is returned under
    ``result``. A failed task returns a generic message (no internal leak); the
    full traceback is logged server-side. Task ids are unguessable uuids and the
    payloads are non-sensitive (public building footprints / cluster polygons),
    so login is the only gate.
    """

    def get(self, request, task_id):
        from celery.result import AsyncResult

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


class CountriesView(LoginRequiredMixin, View):
    """List ISO countries for the area picker, flagging those with bespoke data.

    `bespoke` = countries that have curated `labs.admin_boundaries` polygons
    loaded (so the resolver will prefer them over Overture). The UI can badge
    these as higher-quality.
    """

    def get(self, request):
        from commcare_connect.labs.admin_boundaries.models import AdminBoundary
        from commcare_connect.microplans.core import iso

        # .order_by() clears AdminBoundary's Meta.ordering — without it Django adds
        # the ordering columns to the SELECT, so .distinct() dedupes whole rows
        # (12k) instead of iso_code (returning one entry per country).
        bespoke = sorted(AdminBoundary.objects.order_by().values_list("iso_code", flat=True).distinct())
        return JsonResponse({"status": "ok", "countries": iso.all_countries(), "bespoke": bespoke})


class AdminAreasView(LoginRequiredMixin, View):
    """List admin areas for a country/level via the boundary resolver.

    POST body: {country, level, q?, parent?, source?}. `parent` is an AdminArea
    (as returned by a previous call) used to narrow children; `source` lets the
    user pick a specific boundary source (falls back to the default if it can't
    serve the level). Response reports the source used + the pickable sources so
    the UI can offer a source dropdown.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.core import iso as iso_codes
        from commcare_connect.microplans.core.admin_boundaries import SOURCE_LABELS, AdminArea, get_resolver

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
            return JsonResponse(
                {"status": "error", "detail": "Boundary lookup failed. Check server logs."}, status=502
            )

        return JsonResponse(
            {
                "status": "ok",
                "source": used,
                "available_sources": available,
                "source_labels": {n: SOURCE_LABELS.get(n, n) for n in available},
                "areas": [a.to_json() for a in areas],
            }
        )


class AdminAreaGeometryView(LoginRequiredMixin, View):
    """Resolve one chosen admin area to its GeoJSON geometry (the sampling boundary)."""

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.admin_boundaries import AdminArea, get_resolver

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
        from commcare_connect.microplans.core.admin_boundaries import SOURCE_LABELS, get_resolver

        bbox = self._parse_bbox(request.GET.get("bbox"))
        if bbox is None:
            return JsonResponse(
                {"status": "error", "detail": "bbox=minLng,minLat,maxLng,maxLat is required."}, status=400
            )
        from commcare_connect.microplans.core import iso as iso_codes

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
class ProgramWorkspaceView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Program workspace: the portfolio of candidate plans + plan groups."""

    template_name = "microplans/program_workspace.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        # Seed the new-plan map over the program's existing footprint so boundaries
        # load + the country auto-detects (see _program_map_seed). Absent any plans,
        # the template keeps its default view and the user navigates.
        try:
            from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

            seed = _program_map_seed(ProgramPlanDataAccess(program_id, request=self.request).list_plans())
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


class ProgramPlansAPIView(LoginRequiredMixin, View):
    """JSON: the program's plans (+ headline KPIs) and groups, for the workspace."""

    def get(self, request, program_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plans = [serialization.plan_summary_row(p) for p in da.list_plans()]
            groups = [
                {
                    "group_id": g.id,
                    "name": g.name,
                    "plan_ids": g.plan_ids,
                    "offered_to": g.offered_to,
                    "shared": g.shared,
                }
                for g in da.list_groups()
            ]
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
            }
        )


class ProgramCreatePlanView(LoginRequiredMixin, View):
    """Create a Draft plan in the program from a generated frame (region + geometry)."""

    def post(self, request, program_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

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
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = ProgramPlanDataAccess(program_id, request=request)
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
            )
        except Exception:  # noqa: BLE001
            logger.exception("microplans create_plan failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not create the plan."}, status=502)
        return JsonResponse({"status": "ok", "plan_id": plan.id})


class ProgramPlanTransitionView(LoginRequiredMixin, View):
    """Advance a plan's lifecycle status (Deploy binds the live opportunity_id)."""

    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError

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


class ProgramPlanDeleteView(LoginRequiredMixin, View):
    """Hard-delete a plan record. POST → 204. Used for wiping sample data; the
    safer default for normal lifecycle is the Archive status transition."""

    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess, RecordNotInProgramError

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_plan(plan_id)
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans delete_plan failed (program=%s plan=%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "plan_id": int(plan_id)})


class ProgramGroupDeleteView(LoginRequiredMixin, View):
    """Hard-delete a plan group record (sample-data wipe)."""

    def post(self, request, program_id, group_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess, RecordNotInProgramError

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_group(group_id)
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Group not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans delete_group failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "group_id": int(group_id)})


class ProgramGroupsAPIView(LoginRequiredMixin, View):
    """Create a plan group (a shareable subset offered to an LLO)."""

    def post(self, request, program_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        try:
            payload = json.loads(request.body)
            name = str(payload["name"]).strip()[:255]
            plan_ids = [int(p) for p in payload.get("plan_ids", [])]
            offered_to = str(payload.get("offered_to", "")).strip()[:255]
            if not name or not plan_ids:
                raise ValueError("name and at least one plan are required")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            group = da.create_group(name=name, plan_ids=plan_ids, offered_to=offered_to)
        except Exception:  # noqa: BLE001
            logger.exception("microplans create_group failed (program=%s)", program_id)
            return JsonResponse({"status": "error", "detail": "Could not create the group."}, status=502)
        return JsonResponse({"status": "ok", "group_id": group.id})


class ProgramGroupUpdateView(LoginRequiredMixin, View):
    """Update a plan group (rename, change plans, set offered_to, share)."""

    def post(self, request, program_id, group_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        try:
            payload = json.loads(request.body)
        except json.JSONDecodeError as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)
        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            group = da.update_group(
                int(group_id),
                name=payload.get("name"),
                plan_ids=payload.get("plan_ids"),
                offered_to=payload.get("offered_to"),
                shared=payload.get("shared"),
            )
        except Exception:  # noqa: BLE001
            logger.exception("microplans update_group failed (program=%s group=%s)", program_id, group_id)
            return JsonResponse({"status": "error", "detail": "Could not update the group."}, status=502)
        return JsonResponse({"status": "ok", "group_id": group.id, "shared": group.shared})


class ProgramGroupShareView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """LLO-facing page for a plan group: its subset of plans + their KPIs."""

    template_name = "microplans/group_share.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

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
                kpis = plan_lib.plan_kpis(p.work_areas, input_areas=p.data.get("input_areas") or [])
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
        context["csv_url"] = reverse("microplans:program_plan_csv", args=[program_id, plan_id])
        context["footprints_url"] = reverse("microplans:program_plan_footprints", args=[program_id, plan_id])
        context["regroup_url"] = reverse("microplans:program_plan_regroup", args=[program_id, plan_id])
        context["reassign_url"] = reverse("microplans:program_plan_reassign", args=[program_id, plan_id])
        # Area-definition URLs are program-scoped via a placeholder opp_id (123),
        # exactly as the setup page does. The endpoints don't actually require
        # a real opp; the path arg is just historical.
        context["preview_coverage_url"] = reverse("microplans:preview_coverage", args=[123])
        context["preview_frame_url"] = reverse("microplans:preview_frame", args=[123])
        context["arm_comparability_url"] = reverse("microplans:arm_comparability", args=[123])
        context["admin_areas_url"] = reverse("microplans:admin_areas", args=[123])
        context["admin_area_geometry_url"] = reverse("microplans:admin_area_geometry", args=[123])
        context["countries_url"] = reverse("microplans:countries")
        context["boundary_viewport_url"] = reverse("microplans:boundary_viewport")
        context["regenerate_url"] = reverse("microplans:program_plan_regenerate", args=[program_id, plan_id])
        from django.conf import settings as _s

        context["mapbox_token"] = _s.MAPBOX_TOKEN or context.get("mapbox_token", "")
        context["compare_url"] = reverse("microplans:program_compare_page", args=[program_id]) + f"?plans={plan_id}"
        context.update(_sd_urls())
        context["back_url"] = reverse("microplans:program_workspace", args=[program_id])
        return context


class ProgramPlanView(LoginRequiredMixin, View):
    def get(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan get failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        return JsonResponse(serialization.plan_to_json(plan))

    def delete(self, request, program_id, plan_id):
        """Hard-delete a draft plan. Archive (a status transition) is the normal
        way to retire a candidate region; this is the explicit remove for plans
        the owner wants gone for good (e.g. demo/sample plans). Program-scoped:
        ``delete_plan`` refuses ids that aren't in this program."""
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess, RecordNotInProgramError

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            da.delete_plan(int(plan_id))
        except RecordNotInProgramError:
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)
        except Exception:  # noqa: BLE001
            logger.exception("microplans program plan delete failed (%s/%s)", program_id, plan_id)
            return JsonResponse({"status": "error", "detail": "Delete failed."}, status=502)
        return JsonResponse({"status": "ok", "deleted": int(plan_id)})


class ProgramPlanEditView(LoginRequiredMixin, View):
    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess, StalePlanError
        from commcare_connect.microplans.core.plan import ACTIONS

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


class ProgramPlanFootprintsView(LoginRequiredMixin, View):
    """Building footprints (polygons + centroids) inside a saved plan's area.

    Used by the review-page 'Show footprints' toggle. Prefers the plan's stored
    `input_areas` (the original ward boundary from setup, already PG-cached from
    generation) over re-deriving from cell unions (which would be a different
    cache hash → expensive cold Overture query)."""

    def get(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.footprints import fetch_buildings

        da = ProgramPlanDataAccess(program_id, request=request)
        try:
            plan = da.get_plan(int(plan_id))
        except Exception:  # noqa: BLE001
            return JsonResponse({"status": "error", "detail": "Plan not found."}, status=404)

        area = serialization.plan_lookup_geometry(plan)
        if area is None:
            return JsonResponse(
                {"status": "ok", "footprints": {"type": "FeatureCollection", "features": []}, "count": 0}
            )
        try:
            df = fetch_buildings(area, min_confidence=None, with_geom=True)
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


def _enqueue_plan_mutation(request, op, program_id, plan_id, params):
    """Offload a heavy plan mutation (regroup/reassign/regenerate) to Celery and
    return 202 + a pollable task id. The client polls ``microplans:preview_status``
    and reads the task's result (``plan_to_json`` on success, or a
    ``{status: conflict|error}`` envelope). The worker writes via the LabsRecord
    API, so it needs the caller's OAuth token (same pattern as bulk-create)."""
    from django.urls import reverse

    from commcare_connect.microplans.tasks import apply_plan_mutation_task

    access_token = (request.session.get("labs_oauth") or {}).get("access_token")
    if not access_token:
        return JsonResponse(
            {"status": "error", "detail": "Not authenticated with Connect — sign in again."}, status=401
        )
    task = apply_plan_mutation_task.delay(
        op, int(program_id), int(plan_id), params, request.user.get_username(), access_token
    )
    return JsonResponse(
        {"status": "queued", "task_id": task.id, "poll_url": reverse("microplans:preview_status", args=[task.id])},
        status=202,
    )


class ProgramPlanReassignView(LoginRequiredMixin, View):
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


class ProgramPlanRegenerateView(LoginRequiredMixin, View):
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
                "revision": payload.get("revision"),
            },
        )


class ProgramPlanRegroupView(LoginRequiredMixin, View):
    """Phase-1 op: re-apply the grouping strategy to a plan's cells (Celery-offloaded).

    Body: ``{"strategy", "max_buildings", "buffer_distance_m", "target_size", "revision"}``.
    All params optional; see `core.grouping.GroupingConfig` for defaults.
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


class ProgramPlanFootprintsRefreshView(LoginRequiredMixin, View):
    """Force a refresh of the footprint cache for this plan's area.

    Used to upgrade plans cached in the centroid-only era (pre-`geom_json`) to
    the new polygon-aware cache: deletes the matching `FootprintArea` row so the
    next footprints toggle re-fetches from Overture and stores polygons too.
    """

    def post(self, request, program_id, plan_id):
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.footprints import _area_cache_key
        from commcare_connect.microplans.models import FootprintArea

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


class ProgramPlanCSVView(LoginRequiredMixin, View):
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
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess
        from commcare_connect.microplans.core.workarea import to_csv_rows

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
class ProgramSetupView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
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
        context["program_url"] = reverse("microplans:program_workspace", args=[program_id])
        context["back_url"] = context["program_url"]
        context.update(_sd_urls())
        context["preview_coverage_url"] = reverse("microplans:preview_coverage", args=[123])
        context["preview_frame_url"] = reverse("microplans:preview_frame", args=[123])
        context["arm_comparability_url"] = reverse("microplans:arm_comparability", args=[123])
        context["admin_areas_url"] = reverse("microplans:admin_areas", args=[123])
        context["admin_area_geometry_url"] = reverse("microplans:admin_area_geometry", args=[123])
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


class ProgramComparePlansView(LoginRequiredMixin, View):
    """Compare N program plans' KPIs side by side. Returns per-plan KPIs so the UI
    can stack them with deltas; no composite score (the metrics themselves are the
    comparison)."""

    def get(self, request, program_id):
        from commcare_connect.microplans.core import plan as plan_lib
        from commcare_connect.microplans.core.data_access import ProgramPlanDataAccess

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
            entries.append(
                {
                    "plan_id": p.id,
                    "name": p.name or f"Plan {p.id}",
                    "region": p.region,
                    "mode": p.mode,
                    "created_at": p.created_at,
                    "kpis": plan_lib.plan_kpis(p.work_areas, input_areas=p.data.get("input_areas") or []),
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
        from commcare_connect.labs.context import get_org_data

        ids = set()
        for o in get_org_data(request).get("opportunities", []):
            if o.get("id") is not None:
                ids.add(int(o["id"]))
        return ids


class PreviewServiceDeliveryView(_ServiceDeliveryMixin, LoginRequiredMixin, View):
    """Fetch service-delivery GPS points for one or more opportunities.

    POST {opp_ids: [int], pipeline_id?: int}. Each opp's points are fetched via
    the ServiceDeliveryPoints provider (default GPS pipeline, or the given
    pipeline_id), colored per opp, and merged into one FeatureCollection. Mirrors
    PreviewFootprintsView's request/response shape so the FE layer code is uniform.
    """

    def post(self, request, opp_id):
        from commcare_connect.labs.context import get_org_data
        from commcare_connect.microplans.service_delivery.points import (
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


class ServiceDeliveryPipelinesView(LoginRequiredMixin, View):
    """List visit-level pipelines the user can pick instead of the default GPS one."""

    def get(self, request, opp_id):
        from commcare_connect.workflow.data_access import PipelineDataAccess

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


class DeriveBoundaryView(LoginRequiredMixin, View):
    """Derive a boundary polygon from a posted service-delivery point cloud.

    POST {coords: [[lon,lat],...], method?, concavity?, buffer_m?}. Returns the
    boundary as a GeoJSON Feature so the FE can drop it straight into the draw
    layer / area-input path used by sampling and coverage.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.service_delivery.hull import derive_boundary

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


class ArmComparabilityView(LoginRequiredMixin, View):
    """Compare two study arms so the control reads as a fair counterfactual.

    POST {areas: [{arm, geometry}, ...], building_counts: {arm: int}}. Unions the
    geometries per arm, computes accurate area (UTM) + building density from the
    counts the sample already produced (no second Overture fetch), and returns a
    matched flag when the arms are within tolerance on building count and density.
    """

    RATIO_TOLERANCE = 1.5

    def post(self, request, opp_id):
        from shapely.geometry import shape
        from shapely.ops import transform, unary_union

        from commcare_connect.microplans.core.geo import utm_epsg_for

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            counts = payload.get("building_counts", {}) or {}
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        by_arm: dict[str, list] = {}
        for a in areas:
            try:
                by_arm.setdefault(a.get("arm", "intervention"), []).append(shape(a["geometry"]))
            except (KeyError, TypeError, ValueError):
                continue

        out = []
        for arm, geoms in by_arm.items():
            try:
                from pyproj import Transformer

                geom = unary_union(geoms)
                c = geom.centroid
                tf = Transformer.from_crs(4326, utm_epsg_for(c.x, c.y), always_xy=True).transform
                area_km2 = transform(tf, geom).area / 1e6
            except Exception:  # noqa: BLE001
                logger.exception("arm_comparability area failed (opp=%s arm=%s)", opp_id, arm)
                area_km2 = 0.0
            bc = int(counts.get(arm) or 0)
            density = round(bc / area_km2, 1) if area_km2 > 0 else 0.0
            out.append({"arm": arm, "building_count": bc, "area_km2": round(area_km2, 3), "density_per_km2": density})

        matched = None
        reasons: list[str] = []
        if len(out) >= 2:

            def _ratio(x: float, y: float) -> float:
                lo, hi = sorted((float(x), float(y)))
                return (hi / lo) if lo > 0 else float("inf")

            interv = next((x for x in out if x["arm"] == "intervention"), out[0])
            comp = next((x for x in out if x["arm"] == "comparison"), out[1])
            bc_r = _ratio(interv["building_count"], comp["building_count"])
            d_r = _ratio(interv["density_per_km2"], comp["density_per_km2"])
            matched = bc_r <= self.RATIO_TOLERANCE and d_r <= self.RATIO_TOLERANCE
            if bc_r > self.RATIO_TOLERANCE:
                reasons.append(f"building counts differ {bc_r:.1f}×")
            if d_r > self.RATIO_TOLERANCE:
                reasons.append(f"densities differ {d_r:.1f}×")

        return JsonResponse({"status": "ok", "arms": out, "matched": matched, "reasons": reasons})


# Bulk-create flow — the "paste a ward list" form + the server-side
# materializer that turns confirmed boundary IDs into N draft plans in one
# call. See spec: docs/walkthroughs/microplans-10-wards.yaml scene 2/3, spine
# items `name-match-and-confirm` + `bulk-input-ui`.
# ---------------------------------------------------------------------------


@method_decorator(ensure_csrf_cookie, name="dispatch")
class ProgramBulkCreatePlanPageView(_LabsContextSyncMixin, LoginRequiredMixin, TemplateView):
    """Render the bulk-create form (paste textarea + resolution preview + Create Plans)."""

    template_name = "microplans/bulk_create.html"

    def get_context_data(self, **kwargs):
        from django.urls import reverse

        from commcare_connect.labs.admin_boundaries.models import AdminBoundary

        context = super().get_context_data(**kwargs)
        program_id = kwargs.get("program_id")
        context["program_id"] = program_id
        context["program_url"] = reverse("microplans:program_workspace", args=[program_id])
        context["bulk_create_url"] = reverse("microplans:program_bulk_create", args=[program_id])
        context["resolve_many_url"] = "/labs/explorer/boundaries/resolve_many/"
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


class ProgramBulkCreatePlansView(LoginRequiredMixin, View):
    """Enqueue a batch create of N draft plans from confirmed admin boundaries.

    Body: ``{"plans": [{"boundary_id", "name"}], "mode", "grouping", "cell_size_m"}``.
    Each ward is gridded on the Celery worker — coverage plans tile the boundary via
    the Overture coverage generator, so a plan is a real grid of work areas rather
    than one cell covering the whole ward. This validates + enqueues and returns
    ``202 {task_id, poll_url}``; the client polls ``microplans:bulk_create_status``
    for incremental per-ward results."""

    def post(self, request, program_id):
        from commcare_connect.microplans.tasks import bulk_create_plans_task

        try:
            payload = json.loads(request.body)
        except (json.JSONDecodeError, ValueError):
            return JsonResponse({"status": "error", "detail": "invalid JSON"}, status=400)

        plans_input = payload.get("plans") or []
        mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
        grouping = payload.get("grouping") if isinstance(payload.get("grouping"), dict) else {}
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

        task = bulk_create_plans_task.delay(int(program_id), plans_input, mode, grouping, cell_size_m, access_token)
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
    failed). Mirrors PreviewStatusView's lifecycle mapping."""

    def get(self, request, task_id):
        from celery.result import AsyncResult

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
