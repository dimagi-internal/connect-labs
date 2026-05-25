"""
Views for coverage visualization.
"""

import json
import logging
import time
from collections.abc import Generator

import httpx
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.coverage.data_access import CoverageDataAccess

logger = logging.getLogger(__name__)


class BaseCoverageView(LoginRequiredMixin, TemplateView):
    """
    Base view for coverage app views.

    Provides shared OAuth checking functionality for views that need
    to interact with CommCare HQ APIs.
    """

    def check_commcare_oauth(self) -> bool:
        """Check if CommCare OAuth is configured and not expired"""
        from django.utils import timezone

        commcare_oauth = self.request.session.get("commcare_oauth", {})
        access_token = commcare_oauth.get("access_token")

        if not access_token:
            logger.debug("No CommCare OAuth token found in session")
            return False

        # Check expiration
        expires_at = commcare_oauth.get("expires_at", 0)
        if timezone.now().timestamp() >= expires_at:
            logger.warning(f"CommCare OAuth token expired at {expires_at}")
            return False

        return True


class CoverageMapView(BaseCoverageView):
    """
    Interactive coverage map with FLW color-coding and real-time progress.

    Uses Server-Sent Events (SSE) to stream data loading progress to the frontend.
    Data is loaded via CoverageMapStreamView which uses helper classes:
    - CoverageMapDataLoader: handles data processing and GeoJSON generation
    - CoverageDataAccess: fetches DUs from CommCare

    Features:
    - Real-time loading progress indicators
    - Unique colors for each FLW
    - Colored delivery unit polygons
    - Colored service point markers
    - FLW filter with color swatches
    - Service area filtering
    """

    template_name = "coverage/map.html"

    def get_context_data(self, **kwargs):
        context = super(TemplateView, self).get_context_data(**kwargs)

        # Only check OAuth and provide basic context for initial page load
        context["has_commcare_oauth"] = self.check_commcare_oauth()

        if not context["has_commcare_oauth"]:
            context["error"] = "CommCare OAuth not configured. Please authorize CommCare access."
            context["needs_oauth"] = True

        # Provide the API endpoint URL for async data loading
        from django.urls import reverse

        context["stream_api_url"] = reverse("coverage:map_stream")

        # Get opportunity_id from labs_context or query params for admin boundary panel
        labs_context = getattr(self.request, "labs_context", {}) or {}
        opportunity_id = labs_context.get("opportunity_id") or self.request.GET.get("opportunity_id")
        context["opportunity_id"] = int(opportunity_id) if opportunity_id else None

        return context


class CoverageTokenStatusView(LoginRequiredMixin, TemplateView):
    """Debug view to check OAuth token status"""

    template_name = "coverage/token_status.html"

    def get_context_data(self, **kwargs):
        from django.utils import timezone

        context = super().get_context_data(**kwargs)

        # Check Connect OAuth
        labs_oauth = self.request.session.get("labs_oauth", {})
        context["has_connect_token"] = bool(labs_oauth.get("access_token"))
        context["connect_expires_at"] = labs_oauth.get("expires_at", 0)

        # Check CommCare OAuth
        commcare_oauth = self.request.session.get("commcare_oauth", {})
        context["has_commcare_token"] = bool(commcare_oauth.get("access_token"))
        context["commcare_expires_at"] = commcare_oauth.get("expires_at", 0)

        # Check if expired
        now = timezone.now().timestamp()
        context["connect_expired"] = context["connect_expires_at"] < now if context["connect_expires_at"] else True
        context["commcare_expired"] = context["commcare_expires_at"] < now if context["commcare_expires_at"] else True

        return context


class CoverageDebugView(LoginRequiredMixin, TemplateView):
    """Debug view to list accessible opportunities"""

    template_name = "coverage/debug.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        # Get access token
        access_token = self.request.session.get("labs_oauth", {}).get("access_token")

        if not access_token:
            context["error"] = "No OAuth token found"
            context["opportunities"] = []
            return context

        try:
            # Fetch all opportunities from API
            url = f"{settings.CONNECT_PRODUCTION_URL}/export/opp_org_program_list/"
            response = httpx.get(url, headers={"Authorization": f"Bearer {access_token}"}, timeout=30.0)
            response.raise_for_status()

            data = response.json()
            opportunities = data.get("opportunities", [])

            # Format for display
            context["opportunities"] = [
                {
                    "id": opp.get("id"),
                    "name": opp.get("name"),
                    "program": opp.get("program_name"),
                    "organization": opp.get("organization"),
                    "has_deliver_app": bool(opp.get("deliver_app")),
                    "cc_domain": opp.get("deliver_app", {}).get("cc_domain") if opp.get("deliver_app") else None,
                }
                for opp in opportunities
            ]

            context["total_count"] = len(context["opportunities"])
        except Exception as e:
            logger.error(f"Failed to fetch opportunities: {e}", exc_info=True)
            context["error"] = str(e)
            context["opportunities"] = []

        return context


class CoverageMapStreamView(LoginRequiredMixin, View):
    """
    SSE streaming endpoint for Coverage Map with real-time progress.

    Uses Server-Sent Events to push progress updates to the frontend as each
    step of the data loading completes. This keeps the connection alive during
    long-running operations (CommCare HQ + Connect API calls can take 2+ minutes).
    """

    def get(self, request):
        """Stream coverage map data loading progress via Server-Sent Events."""
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        response = StreamingHttpResponse(
            self._stream_coverage_data(request),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"
        return response

    def _stream_coverage_data(self, request) -> Generator[str, None, None]:
        """Generator that yields SSE events as coverage data loads."""

        def send_sse(message: str, data: dict | None = None, error: str | None = None) -> str:
            """Format a message as an SSE event."""
            event = {"message": message, "complete": data is not None}
            if data:
                event["data"] = data
            if error:
                event["error"] = error
            return f"data: {json.dumps(event)}\n\n"

        try:
            from django.utils import timezone

            from commcare_connect.coverage.data_loader import CoverageMapDataLoader

            commcare_oauth = request.session.get("commcare_oauth", {})
            access_token = commcare_oauth.get("access_token")

            if not access_token:
                yield send_sse("Error", error="CommCare OAuth not configured. Please authorize CommCare access.")
                return

            expires_at = commcare_oauth.get("expires_at", 0)
            if timezone.now().timestamp() >= expires_at:
                yield send_sse("Error", error="CommCare OAuth token expired. Please re-authorize CommCare access.")
                return

            loader = CoverageMapDataLoader(request)
            data_access = CoverageDataAccess(request)
            from_cache = False

            # STEP 1: Check cache
            yield send_sse("Checking cache...")

            # STEP 2: CommCare HQ - Fetch DU polygons
            yield send_sse("Fetching CommCare HQ data (delivery units)...")
            step2_start = time.time()
            coverage = data_access.build_coverage_dus_only()
            step2_duration = time.time() - step2_start
            du_count = len(coverage.delivery_units)
            logger.info(f"[Coverage Map Stream] CommCare HQ complete ({step2_duration:.1f}s): {du_count} DUs")
            yield send_sse(f"CommCare HQ complete: {len(coverage.delivery_units)} delivery units")

            # STEP 3: Get analysis config
            from commcare_connect.coverage.analysis import COVERAGE_BASE_CONFIG

            config = COVERAGE_BASE_CONFIG

            # STEP 4: Connect - Download visits
            yield send_sse("Fetching Connect data (this may take a while)...")
            step4_start = time.time()
            visit_rows, field_metadata = loader.get_enriched_visits(config, coverage)
            step4_duration = time.time() - step4_start
            logger.info(f"[Coverage Map Stream] Connect complete ({step4_duration:.1f}s): {len(visit_rows)} visits")
            yield send_sse(f"Connect complete: {len(visit_rows)} visits loaded")

            if step4_duration < 5:
                from_cache = True

            # STEP 5: Process visits
            yield send_sse("Processing visits into coverage structure...")
            step5_start = time.time()
            loader.populate_coverage_visits(coverage, visit_rows)
            step5_duration = time.time() - step5_start
            logger.info(f"[Coverage Map Stream] Processing complete ({step5_duration:.1f}s)")
            yield send_sse("Processing complete")

            # STEP 6: Build GeoJSON layers
            yield send_sse("Building map layers...")
            step6_start = time.time()

            flw_colors = loader.generate_flw_colors(coverage.flws)

            commcare_to_username = {}
            for visit in visit_rows:
                commcare_id = visit.computed.get("commcare_userid", "")
                if commcare_id and visit.username:
                    commcare_to_username[commcare_id] = visit.username

            delivery_units_geojson = loader.build_colored_du_geojson(coverage, flw_colors, commcare_to_username)
            service_points_geojson = loader.build_colored_points_geojson(visit_rows, coverage.flws, flw_colors)

            flw_list_colored = [
                {
                    "id": flw_key,
                    "name": (
                        f"{flw.display_name or flw.commcare_id} ({flw.total_visits} visits)"
                        if flw.total_visits > 0
                        else f"{flw.commcare_id} (no connect visits)"
                    ),
                    "visits": flw.total_visits,
                    "color": flw_colors.get(flw_key, "#999999"),
                }
                for flw_key, flw in coverage.flws.items()
            ]

            step6_duration = time.time() - step6_start
            logger.info(f"[Coverage Map Stream] Layers built ({step6_duration:.1f}s) - {len(flw_list_colored)} FLWs")

            # STEP 7: Complete
            total_duration = step2_duration + step4_duration + step5_duration + step6_duration
            logger.info(f"[Coverage Map Stream] Complete! Total: {total_duration:.1f}s")

            response_data = {
                "success": True,
                "delivery_units_geojson": delivery_units_geojson,
                "service_points_geojson": service_points_geojson,
                "flw_list_colored": flw_list_colored,
                "service_area_list": sorted(list(coverage.service_areas.keys())),
                "service_points_count": len(visit_rows),
                "computed_field_metadata": field_metadata,
                "from_cache": from_cache,
            }

            yield send_sse("Complete!", response_data)

        except Exception as e:
            logger.error(f"[Coverage Map Stream] Error: {e}", exc_info=True)
            yield send_sse("Error", error=str(e))
