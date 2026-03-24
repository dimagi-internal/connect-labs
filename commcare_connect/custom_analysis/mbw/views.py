"""
Views for MBW GPS analysis.

Provides GPS-based analysis with date range filtering.
Uses the standard pipeline approach with AnalysisPipelineSSEMixin.
"""

import logging
import time
from collections.abc import Generator
from datetime import date, timedelta

import sentry_sdk
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.labs.analysis.data_access import get_flw_names_for_opportunity
from commcare_connect.labs.analysis.pipeline import AnalysisPipeline
from commcare_connect.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, BaseSSEStreamView, send_sse_event
from commcare_connect.workflow.templates.mbw_monitoring.gps_analysis import (
    DailyTravel,
    FLWSummary,
    GPSAnalysisResult,
    VisitWithGPS,
    analyze_gps_metrics,
    build_result_from_analyzed_visits,
)
from commcare_connect.workflow.templates.mbw_monitoring.pipeline_config import MBW_GPS_PIPELINE_CONFIG

logger = logging.getLogger(__name__)


def get_default_date_range() -> tuple[date, date]:
    """Get default date range (last 30 days)."""
    end_date = date.today()
    start_date = end_date - timedelta(days=30)
    return start_date, end_date


def parse_date_param(date_str: str | None, default: date) -> date:
    """Parse date from query param or return default."""
    if not date_str:
        return default
    try:
        return date.fromisoformat(date_str)
    except ValueError:
        return default


def filter_visits_by_date(visits: list, start_date: date, end_date: date) -> list:
    """
    Filter visits by date range.

    This is fast in-memory filtering after pipeline has cached the data.
    For 37k visits, this takes ~50ms.
    """
    filtered = []
    for visit in visits:
        visit_date = visit.visit_date
        if visit_date and start_date <= visit_date <= end_date:
            filtered.append(visit)
    return filtered


def serialize_visit(visit: VisitWithGPS) -> dict:
    """Serialize a visit for JSON response."""
    return {
        "visit_id": visit.visit_id,
        "username": visit.username,
        "case_id": visit.case_id,
        "mother_case_id": visit.mother_case_id,
        "entity_name": visit.entity_name,
        "form_name": visit.form_name,
        "visit_date": visit.visit_date.isoformat() if visit.visit_date else None,
        "gps": {
            "latitude": visit.gps.latitude,
            "longitude": visit.gps.longitude,
            "accuracy": visit.gps.accuracy,
        }
        if visit.gps
        else None,
        "distance_from_prev_km": round(visit.distance_from_prev_case_visit / 1000, 2)
        if visit.distance_from_prev_case_visit
        else None,
        "is_flagged": visit.is_flagged,
        "flag_reason": visit.flag_reason,
    }


def serialize_daily_travel(dt: DailyTravel) -> dict:
    """Serialize daily travel for JSON response."""
    return {
        "date": dt.travel_date.isoformat(),
        "distance_km": round(dt.total_distance_km, 2),
        "visit_count": dt.visit_count,
    }


def serialize_flw_summary(flw: FLWSummary) -> dict:
    """Serialize FLW summary for JSON response."""
    return {
        "username": flw.username,
        "display_name": flw.display_name,
        "total_visits": flw.total_visits,
        "visits_with_gps": flw.visits_with_gps,
        "flagged_visits": flw.flagged_visits,
        "unique_cases": flw.unique_cases,
        "avg_case_distance_km": round(flw.avg_case_distance_km, 2) if flw.avg_case_distance_km else None,
        "max_case_distance_km": round(flw.max_case_distance_km, 2) if flw.max_case_distance_km else None,
        "trailing_7_days": [serialize_daily_travel(dt) for dt in flw.trailing_7_days],
        "avg_daily_travel_km": round(flw.avg_daily_travel_km, 2) if flw.avg_daily_travel_km else None,
    }


def serialize_result(result: GPSAnalysisResult, include_visits: bool = False) -> dict:
    """Serialize GPS analysis result for JSON response."""
    data = {
        "total_visits": result.total_visits,
        "total_flagged": result.total_flagged,
        "date_range_start": result.date_range_start.isoformat() if result.date_range_start else None,
        "date_range_end": result.date_range_end.isoformat() if result.date_range_end else None,
        "flw_summaries": [serialize_flw_summary(flw) for flw in result.flw_summaries],
    }
    if include_visits:
        data["visits"] = [serialize_visit(v) for v in result.visits]
    return data


class MBWGPSAnalysisView(LoginRequiredMixin, TemplateView):
    """
    Main GPS analysis view with date range picker.

    Page loads with default date range, data loaded via SSE streaming.
    """

    template_name = "custom_analysis/mbw/gps_analysis.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Check labs context
        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        context["opportunity_id"] = opportunity_id
        context["opportunity_name"] = labs_context.get("opportunity_name")
        context["has_context"] = bool(opportunity_id)

        if not opportunity_id:
            context["error"] = "No opportunity selected. Please select an opportunity from the labs context."
            return context

        # Get date range from params or defaults
        default_start, default_end = get_default_date_range()
        start_date = parse_date_param(self.request.GET.get("start_date"), default_start)
        end_date = parse_date_param(self.request.GET.get("end_date"), default_end)

        context["start_date"] = start_date.isoformat()
        context["end_date"] = end_date.isoformat()

        # API URLs
        context["stream_api_url"] = reverse("mbw:gps_stream")
        context["data_api_url"] = reverse("mbw:gps_data")

        # Check OAuth
        labs_oauth = self.request.session.get("labs_oauth", {})
        context["has_oauth"] = bool(labs_oauth.get("access_token"))

        return context


class MBWGPSStreamView(AnalysisPipelineSSEMixin, BaseSSEStreamView):
    """
    SSE streaming endpoint for GPS analysis using the standard pipeline mixin.

    Uses AnalysisPipelineSSEMixin for proper caching and progress streaming.
    """

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream GPS analysis progress via SSE."""
        try:
            # Check labs context
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id")

            if not opportunity_id:
                yield send_sse_event("Error", error="No opportunity selected")
                return

            # Check OAuth
            labs_oauth = request.session.get("labs_oauth", {})
            if not labs_oauth.get("access_token"):
                yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                return

            # Parse date range
            default_start, default_end = get_default_date_range()
            start_date = parse_date_param(request.GET.get("start_date"), default_start)
            end_date = parse_date_param(request.GET.get("end_date"), default_end)

            yield send_sse_event(f"Loading visits for {start_date} to {end_date}...")

            # Use standard pipeline streaming approach
            pipeline = AnalysisPipeline(request)
            pipeline_stream = pipeline.stream_analysis(MBW_GPS_PIPELINE_CONFIG, opportunity_id=opportunity_id)

            logger.info(f"[MBW GPS] Starting pipeline stream for opportunity {opportunity_id}")

            # Stream all pipeline events using the mixin
            yield from self.stream_pipeline_events(pipeline_stream)

            # Get result from mixin
            result = self._pipeline_result
            from_cache = self._pipeline_from_cache

            if not result:
                yield send_sse_event("Error", error="No data returned from pipeline")
                return

            logger.info(f"[MBW GPS] Pipeline returned {len(result.rows)} visits")

            # Convert pipeline rows to visit dicts for GPS analysis
            yield send_sse_event(f"Processing {len(result.rows)} visits...")

            # Debug: Log GPS coverage stats
            rows_with_gps = sum(1 for r in result.rows if r.latitude is not None)
            logger.info(f"[MBW GPS] GPS coverage: {rows_with_gps}/{len(result.rows)} rows have GPS")
            if result.rows:
                sample = result.rows[0]
                logger.info(f"[MBW GPS] Sample computed keys: {list(sample.computed.keys())}")
                logger.info(f"[MBW GPS] Sample case_id: {sample.computed.get('case_id')}")

            visits_for_analysis = []
            for row in result.rows:
                # Build GPS location string from VisitRow's lat/lon
                gps_location = None
                if row.latitude is not None and row.longitude is not None:
                    gps_location = f"{row.latitude} {row.longitude}"

                visits_for_analysis.append(
                    {
                        "id": row.id,
                        "username": row.username,
                        "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                        "entity_name": row.entity_name,
                        "computed": row.computed,
                        "metadata": {"location": gps_location},
                    }
                )

            # Get FLW names
            yield send_sse_event("Loading FLW names...")
            try:
                flw_names = get_flw_names_for_opportunity(request)
            except Exception as e:
                logger.warning(f"[MBW GPS] Failed to fetch FLW names: {e}")
                flw_names = {}

            # Run GPS analysis on ALL visits first (to calculate case distances using full history)
            yield send_sse_event("Analyzing GPS data (calculating case distances)...")
            gps_result = analyze_gps_metrics(visits_for_analysis, flw_names)

            # Filter by date range - but keep the pre-calculated case distances!
            yield send_sse_event(f"Filtering to date range {start_date} to {end_date}...")
            filtered_visits = filter_visits_by_date(gps_result.visits, start_date, end_date)

            # Build summaries from filtered visits (which already have distances calculated)
            gps_result = build_result_from_analyzed_visits(filtered_visits, flw_names)

            logger.info(
                f"[MBW GPS] Analysis complete: {gps_result.total_visits} visits, "
                f"{gps_result.total_flagged} flagged, {len(gps_result.flw_summaries)} FLWs"
            )

            # Serialize and return
            response_data = serialize_result(gps_result, include_visits=False)
            response_data["success"] = True
            response_data["opportunity_id"] = opportunity_id
            response_data["opportunity_name"] = labs_context.get("opportunity_name")
            response_data["from_cache"] = from_cache

            yield send_sse_event("Complete!", response_data)

        except ValueError as e:
            logger.error(f"[MBW GPS] ValueError: {e}")
            sentry_sdk.capture_exception(e)
            yield send_sse_event("Error", error=str(e))
        except Exception as e:
            logger.error(f"[MBW GPS] Stream failed: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            yield send_sse_event("Error", error=f"Failed to analyze GPS data: {str(e)}")


class MBWGPSDataView(LoginRequiredMixin, View):
    """
    JSON API endpoint for GPS analysis data.

    Used for drill-down to get visits for a specific FLW.
    """

    def get(self, request):
        """Return GPS analysis data for a specific FLW."""
        # Check OAuth token for API requests
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired. Please refresh the page to re-authenticate."}, status=401)

        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            return JsonResponse({"error": "No opportunity selected"}, status=400)

        # Parse params
        default_start, default_end = get_default_date_range()
        start_date = parse_date_param(request.GET.get("start_date"), default_start)
        end_date = parse_date_param(request.GET.get("end_date"), default_end)
        include_visits = request.GET.get("include_visits") == "1"
        username_filter = request.GET.get("username")

        logger.info(f"[MBW GPS API] Request: username={username_filter}, include_visits={include_visits}")

        try:
            # Use pipeline to get cached data
            t0 = time.time()
            pipeline = AnalysisPipeline(request)
            result = pipeline.stream_analysis_ignore_events(MBW_GPS_PIPELINE_CONFIG, opportunity_id)
            logger.info(f"[MBW GPS API] Pipeline fetch took {time.time() - t0:.2f}s, got {len(result.rows)} rows")

            # Convert to visit dicts
            visits_for_analysis = []
            for row in result.rows:
                # Filter by username if specified
                if username_filter and row.username != username_filter:
                    continue

                # Build GPS location string from VisitRow's lat/lon (consistent with stream view)
                gps_location = None
                if row.latitude is not None and row.longitude is not None:
                    gps_location = f"{row.latitude} {row.longitude}"

                visits_for_analysis.append(
                    {
                        "id": row.id,
                        "username": row.username,
                        "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                        "entity_name": row.entity_name,
                        "computed": row.computed,
                        "metadata": {"location": gps_location},
                    }
                )

            logger.info(f"[MBW GPS API] Built {len(visits_for_analysis)} visits for analysis")

            # Get FLW names
            t1 = time.time()
            try:
                flw_names = get_flw_names_for_opportunity(request)
            except Exception:
                flw_names = {}
            logger.info(f"[MBW GPS API] FLW names took {time.time() - t1:.2f}s")

            # Run GPS analysis on all visits (to calculate case distances using full history)
            t2 = time.time()
            gps_result = analyze_gps_metrics(visits_for_analysis, flw_names)
            logger.info(f"[MBW GPS API] GPS analysis took {time.time() - t2:.2f}s")

            # Filter by date - keep pre-calculated case distances
            filtered_visits = filter_visits_by_date(gps_result.visits, start_date, end_date)

            # Build summaries from filtered visits
            t3 = time.time()
            gps_result = build_result_from_analyzed_visits(filtered_visits, flw_names)
            logger.info(f"[MBW GPS API] Build result took {time.time() - t3:.2f}s")

            response_data = serialize_result(gps_result, include_visits=include_visits)
            response_data["success"] = True
            response_data["opportunity_id"] = opportunity_id

            logger.info(f"[MBW GPS API] Total request time: {time.time() - t0:.2f}s")
            return JsonResponse(response_data)

        except ValueError as e:
            sentry_sdk.capture_exception(e)
            return JsonResponse({"error": str(e)}, status=400)
        except Exception as e:
            logger.error(f"[MBW GPS] API failed: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            return JsonResponse({"error": str(e)}, status=500)


class MBWGPSVisitDetailView(LoginRequiredMixin, View):
    """
    API endpoint to get detailed visits for a specific FLW.

    Used for drill-down in the UI.
    """

    def get(self, request, username: str):
        """Return visits for a specific FLW."""
        # Check OAuth token for API requests
        labs_oauth = request.session.get("labs_oauth", {})
        if not labs_oauth.get("access_token"):
            return JsonResponse({"error": "Session expired. Please refresh the page to re-authenticate."}, status=401)

        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            return JsonResponse({"error": "No opportunity selected"}, status=400)

        # Parse date range
        default_start, default_end = get_default_date_range()
        start_date = parse_date_param(request.GET.get("start_date"), default_start)
        end_date = parse_date_param(request.GET.get("end_date"), default_end)

        try:
            # Use pipeline to get cached data
            pipeline = AnalysisPipeline(request)
            result = pipeline.stream_analysis_ignore_events(MBW_GPS_PIPELINE_CONFIG, opportunity_id)

            # Filter to specific user
            visits_for_analysis = []
            for row in result.rows:
                if row.username != username:
                    continue

                # Build GPS location string from VisitRow's lat/lon (consistent with stream view)
                gps_location = None
                if row.latitude is not None and row.longitude is not None:
                    gps_location = f"{row.latitude} {row.longitude}"

                visits_for_analysis.append(
                    {
                        "id": row.id,
                        "username": row.username,
                        "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                        "entity_name": row.entity_name,
                        "computed": row.computed,
                        "metadata": {"location": gps_location},
                    }
                )

            # Run GPS analysis
            gps_result = analyze_gps_metrics(visits_for_analysis, {})

            # Filter by date
            filtered_visits = filter_visits_by_date(gps_result.visits, start_date, end_date)

            return JsonResponse(
                {
                    "success": True,
                    "username": username,
                    "visits": [serialize_visit(v) for v in filtered_visits],
                    "total_visits": len(filtered_visits),
                    "flagged_visits": sum(1 for v in filtered_visits if v.is_flagged),
                }
            )

        except Exception as e:
            logger.error(f"[MBW GPS] Visit detail failed: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            return JsonResponse({"error": str(e)}, status=500)
