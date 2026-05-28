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

    template_name = "rooftop_surveys/setup.html"

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
        from commcare_connect.rooftop_surveys.sampling.frame import FrameConfig, generate_frame

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            if not areas:
                raise ValueError("no areas drawn")
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        config = FrameConfig.from_payload(payload.get("config", {}))
        try:
            result = generate_frame(areas, config)
        except Exception as e:  # noqa: BLE001 — surface the failure to the UI rather than 500
            logger.exception("rooftop preview_frame failed (opp=%s)", opp_id)
            return JsonResponse({"status": "error", "detail": str(e)}, status=502)

        return JsonResponse(
            {
                "status": "ok",
                "pins": result.pins_geojson,
                "hulls": result.hulls_geojson,
                "stats": result.stats,
            }
        )


class SaveFrameView(LoginRequiredMixin, View):
    """Persist a previewed frame (area + pins) as LabsRecords for this opp.

    The client posts the already-generated pins/hulls/stats from the preview so
    we don't recompute. Returns the new record ids.
    """

    def post(self, request, opp_id):
        from commcare_connect.rooftop_surveys.data_access import RooftopDataAccess

        try:
            payload = json.loads(request.body)
            areas = payload["areas"]
            pins = payload["pins"]
            hulls = payload.get("hulls", {"type": "FeatureCollection", "features": []})
            stats = payload.get("stats", [])
            config = payload.get("config", {})
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        da = RooftopDataAccess(opportunity_id=opp_id, request=request)
        try:
            area_record = da.save_area(areas=areas, config=config, name=payload.get("name", ""))
            frame_record = da.save_frame(area_record_id=area_record.id, pins=pins, hulls=hulls, stats=stats)
        except Exception as e:  # noqa: BLE001 — surface to UI
            logger.exception("rooftop save_frame failed (opp=%s)", opp_id)
            return JsonResponse({"status": "error", "detail": str(e)}, status=502)

        return JsonResponse({"status": "ok", "area_record_id": area_record.id, "frame_record_id": frame_record.id})


class DownloadWorkAreaCSVView(LoginRequiredMixin, View):
    """Render the previewed pins as a Connect microplanning work-area import CSV.

    Lets a frame be pushed to Connect *today* via the existing org-admin web
    importer (no prod write API needed). Each pin → one tiny WorkArea row.
    """

    def post(self, request, opp_id):
        from commcare_connect.rooftop_surveys.workarea import build_work_areas, to_csv_rows

        try:
            payload = json.loads(request.body)
            pins = payload["pins"]
        except (json.JSONDecodeError, KeyError) as e:
            return JsonResponse({"status": "error", "detail": f"Invalid request: {e}"}, status=400)

        rows = to_csv_rows(
            build_work_areas(
                pins,
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
