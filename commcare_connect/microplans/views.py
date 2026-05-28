"""Views for the Rooftop Surveys setup flow (Stage A: area → frame → push)."""

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

logger = logging.getLogger(__name__)


@method_decorator(ensure_csrf_cookie, name="dispatch")
class SetupView(LoginRequiredMixin, TemplateView):
    """Area picker → frame config → preview → push-to-Connect.

    Stage A entry point. Renders a Mapbox GL JS map (matching Connect's
    microplanning display tooling) with draw controls for the intervention
    and optional comparison polygons.
    """

    template_name = "microplans/setup.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["opp_id"] = kwargs.get("opp_id")
        context["mapbox_token"] = settings.MAPBOX_TOKEN or ""
        if not settings.MAPBOX_TOKEN:
            context["error"] = "MAPBOX_TOKEN is not configured; the map cannot load."
        return context


class PreviewFrameView(LoginRequiredMixin, View):
    """Fetch building footprints for the drawn area(s) and run the sampling preview.

    Synchronous: the first fetch for an area hits Overture S3 (~tens of seconds);
    subsequent runs are served from cache. Returns the sampled pins + cluster
    hulls as GeoJSON plus per-arm stats for the map to render.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.sampling.frame import FrameConfig, generate_frame

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config = FrameConfig.from_payload(payload.get("config", {}))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            result = generate_frame(areas, config)
        except ValueError as e:
            # Expected, actionable user errors (e.g. area too large) — safe to surface.
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            # Unexpected — log server-side, return a generic message (no internal leak).
            logger.exception("rooftop preview_frame failed (opp=%s)", opp_id)
            return JsonResponse(
                {"status": "error", "detail": "Frame generation failed. Check server logs."},
                status=502,
            )

        return JsonResponse(
            {
                "status": "ok",
                "pins": result.pins_geojson,
                "hulls": result.hulls_geojson,
                "stats": result.stats,
            }
        )


class PreviewCoverageView(LoginRequiredMixin, View):
    """Coverage-mode preview: balanced/grid clusters → cluster polygons.

    Same footprint fetch as sampling, but instead of PPS-sampling pins it returns
    the cluster hulls (each = one WorkArea covering every household within it).
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.coverage.frame import CoverageConfig, generate_coverage_frame

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
            config = CoverageConfig.from_payload(payload.get("config", {}))
        except (json.JSONDecodeError, KeyError, ValueError, TypeError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        try:
            result = generate_coverage_frame(areas, config)
        except ValueError as e:
            return JsonResponse({"status": "error", "detail": str(e)}, status=400)
        except Exception:  # noqa: BLE001
            logger.exception("microplans preview_coverage failed (opp=%s)", opp_id)
            return JsonResponse(
                {"status": "error", "detail": "Coverage generation failed. Check server logs."},
                status=502,
            )

        return JsonResponse({"status": "ok", "areas": result.areas_geojson, "stats": result.stats})


class SaveFrameView(LoginRequiredMixin, View):
    """Persist a previewed frame (area + pins) as LabsRecords for this opp.

    The client posts the already-generated pins/hulls/stats from the preview so
    we don't recompute. Returns the new record ids.
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.data_access import RooftopDataAccess

        empty_fc = {"type": "FeatureCollection", "features": []}
        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            # Coverage stores its cluster polygons in `hulls` (pins stays empty).
            if mode == "coverage":
                pins = empty_fc
                hulls = payload.get("coverage_areas") or payload.get("hulls", empty_fc)
            else:
                pins = payload["pins"]
                hulls = payload.get("hulls", empty_fc)
            stats = payload.get("stats", [])
            config = payload.get("config", {})
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            area_record = da.save_area(areas=areas, config=config, name=payload.get("name", ""), mode=mode)
            frame_record = da.save_frame(area_record_id=area_record.id, pins=pins, hulls=hulls, stats=stats, mode=mode)
        except Exception:  # noqa: BLE001
            logger.exception("microplans save_frame failed (opp=%s)", opp_id)
            return JsonResponse(
                {"status": "error", "detail": "Saving the frame failed. Check server logs."},
                status=502,
            )

        return JsonResponse({"status": "ok", "area_record_id": area_record.id, "frame_record_id": frame_record.id})


class CountriesView(LoginRequiredMixin, View):
    """List ISO countries for the area picker, flagging those with bespoke data.

    `bespoke` = countries that have curated `labs.admin_boundaries` polygons
    loaded (so the resolver will prefer them over Overture). The UI can badge
    these as higher-quality.
    """

    def get(self, request):
        from commcare_connect.labs.admin_boundaries.models import AdminBoundary
        from commcare_connect.microplans.core import iso

        bespoke = sorted(AdminBoundary.objects.values_list("iso_code", flat=True).distinct())
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
        from commcare_connect.microplans.core.admin_boundaries import SOURCE_LABELS, AdminArea, get_resolver

        try:
            payload = json.loads(request.body)
            country = payload["country"]
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


class DownloadWorkAreaCSVView(LoginRequiredMixin, View):
    """Render the previewed frame as a Connect microplanning work-area import CSV.

    Lets a frame be pushed to Connect *today* via the existing org-admin web
    importer (no prod write API needed). Sampling: each pin → one tiny WorkArea.
    Coverage: each cluster polygon → one WorkArea (visit every household).
    """

    def post(self, request, opp_id):
        from commcare_connect.microplans.core.workarea import build_coverage_work_areas, build_work_areas, to_csv_rows

        try:
            payload = json.loads(request.body)
            mode = "coverage" if payload.get("mode") == "coverage" else "sampling"
            geojson = payload["coverage_areas"] if mode == "coverage" else payload["pins"]
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        builder = build_coverage_work_areas if mode == "coverage" else build_work_areas
        rows = to_csv_rows(
            builder(
                geojson,
                ward_for_arm=payload.get("ward_for_arm"),
                lga=payload.get("lga", ""),
                state=payload.get("state", ""),
            )
        )
        response = HttpResponse(content_type="text/csv")
        response["Content-Disposition"] = f'attachment; filename="rooftop_work_areas_opp{opp_id}.csv"'
        if rows:
            writer = csv.DictWriter(response, fieldnames=list(rows[0].keys()))
            writer.writeheader()
            writer.writerows(rows)
        return response
