"""
Views for CHC Nutrition analysis.

Provides FLW-level analysis of nutrition metrics using the labs analysis framework.

Uses the unified pipeline pattern via AnalysisPipeline which handles:
- Multi-tier caching (LabsRecord, Redis, file)
- Automatic terminal stage detection from config

The visit_result is kept in context for potential drill-down views.

SSE Streaming:
- CHCNutritionStreamView provides Server-Sent Events for real-time progress
- Frontend uses EventSource to receive progress updates as they happen
"""

import json
import logging
from collections.abc import Generator

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse, StreamingHttpResponse
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from commcare_connect.custom_analysis.chc_nutrition.analysis_config import CHC_NUTRITION_CONFIG
from commcare_connect.labs.analysis.data_access import get_flw_names_for_opportunity
from commcare_connect.labs.analysis.pipeline import EVENT_DOWNLOAD, EVENT_RESULT, EVENT_STATUS, AnalysisPipeline

logger = logging.getLogger(__name__)


class CHCNutritionAnalysisView(LoginRequiredMixin, TemplateView):
    """
    Main analysis view for CHC Nutrition project.

    Displays one row per FLW with aggregated nutrition and health metrics.
    Uses progressive loading: the page loads quickly with a loading indicator,
    then fetches data asynchronously via the CHCNutritionDataView API.
    """

    template_name = "custom_analysis/chc_nutrition/analysis.html"

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

        # Provide the API endpoint URLs for async data loading
        context["data_api_url"] = reverse("chc_nutrition:api_data")
        context["stream_api_url"] = reverse("chc_nutrition:api_stream")

        return context


class CHCNutritionDataView(LoginRequiredMixin, View):
    """API endpoint to load CHC Nutrition data asynchronously."""

    def get(self, request):
        """Return CHC Nutrition analysis data as JSON for progressive loading."""
        try:
            # Check labs context
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id")

            if not opportunity_id:
                return JsonResponse(
                    {"error": "No opportunity selected. Please select an opportunity from the labs context."},
                    status=400,
                )

            logger.info(f"[CHC Nutrition API] Starting analysis for opportunity {opportunity_id}")

            # Run the unified analysis pipeline
            # This handles all caching (LabsRecord if ?use_labs_record_cache=true, Redis, file)
            logger.info("[CHC Nutrition API] Step 1/3: Running analysis pipeline...")
            flw_result = AnalysisPipeline(request).stream_analysis_ignore_events(CHC_NUTRITION_CONFIG)
            logger.info(f"[CHC Nutrition API] Got {len(flw_result.rows)} FLWs from pipeline")

            # Step 2: Get FLW display names
            logger.info("[CHC Nutrition API] Step 2/3: Fetching FLW display names...")
            try:
                flw_names = get_flw_names_for_opportunity(request)
                logger.info(f"[CHC Nutrition API] Loaded display names for {len(flw_names)} FLWs")
            except Exception as e:
                logger.warning(f"Failed to fetch FLW names: {e}")
                flw_names = {}

            # Step 3: Build response data
            logger.info("[CHC Nutrition API] Step 3/3: Building response...")

            # Process FLW rows
            flws_data = []
            for flw in flw_result.rows:
                display_name = flw_names.get(flw.username, flw.username)

                # Calculate gender split
                male_count = flw.custom_fields.get("male_count") or 0
                female_count = flw.custom_fields.get("female_count") or 0
                total_gendered = male_count + female_count
                gender_split_female_pct = (
                    round((female_count / total_gendered) * 100, 1) if total_gendered > 0 else None
                )

                flw_data = {
                    "username": flw.username,
                    "display_name": display_name,
                    "total_visits": flw.total_visits,
                    "approved_visits": flw.approved_visits,
                    "approval_rate": round(flw.approval_rate, 1) if flw.approval_rate else 0,
                    "days_active": flw.days_active,
                    "custom_fields": flw.custom_fields,
                    "gender_split_female_pct": gender_split_female_pct,
                    "male_count": male_count,
                    "female_count": female_count,
                }
                flws_data.append(flw_data)

            # Calculate summary stats
            summary = flw_result.get_summary_stats()

            # Calculate nutrition summary
            nutrition_summary = self._get_nutrition_summary(flw_result)

            # Get opportunity info for audit button
            opportunity = labs_context.get("opportunity", {})
            deliver_app = opportunity.get("deliver_app", {})

            response_data = {
                "success": True,
                "flws": flws_data,
                "summary": summary,
                "nutrition_summary": nutrition_summary,
                "total_visits": flw_result.metadata.get("total_visits", 0),
                "opportunity_id": opportunity_id,
                "opportunity_name": labs_context.get("opportunity_name"),
                "deliver_app_cc_app_id": deliver_app.get("cc_app_id"),
                "deliver_app_cc_domain": deliver_app.get("cc_domain"),
                "from_cache": request.GET.get("refresh") != "1",
            }

            logger.info(
                f"[CHC Nutrition API] Complete! Returning {len(flws_data)} FLWs, "
                f"{nutrition_summary.get('total_muac_measurements', 0)} MUAC measurements"
            )
            return JsonResponse(response_data)

        except Exception as e:
            logger.error(f"[CHC Nutrition API] Failed to compute analysis: {e}", exc_info=True)
            return JsonResponse({"error": str(e)}, status=500)

    def _get_nutrition_summary(self, result) -> dict:
        """
        Calculate nutrition-specific summary statistics.

        Args:
            result: FLWAnalysisResult

        Returns:
            Dictionary of nutrition-specific metrics
        """
        if not result.rows:
            return {}

        # Aggregate across all FLWs (handle None values explicitly)
        total_muac_measurements = sum(row.custom_fields.get("muac_measurements_count") or 0 for row in result.rows)
        total_muac_consents = sum(row.custom_fields.get("muac_consent_count") or 0 for row in result.rows)
        total_children_unwell = sum(row.custom_fields.get("children_unwell_count") or 0 for row in result.rows)
        total_malnutrition_diagnosed = sum(
            row.custom_fields.get("malnutrition_diagnosed_count") or 0 for row in result.rows
        )
        total_under_treatment = sum(
            row.custom_fields.get("under_malnutrition_treatment_count") or 0 for row in result.rows
        )
        total_va_doses = sum(row.custom_fields.get("received_va_dose_before_count") or 0 for row in result.rows)

        # SAM and MAM counts
        total_sam = sum(row.custom_fields.get("sam_count") or 0 for row in result.rows)
        total_mam = sum(row.custom_fields.get("mam_count") or 0 for row in result.rows)

        # Calculate averages
        avg_muac_measurements_per_flw = total_muac_measurements / len(result.rows) if result.rows else 0

        # MUAC consent rate
        muac_consent_rate = (total_muac_consents / total_muac_measurements * 100) if total_muac_measurements > 0 else 0

        # SAM and MAM rates
        sam_rate = (total_sam / total_muac_measurements * 100) if total_muac_measurements > 0 else 0
        mam_rate = (total_mam / total_muac_measurements * 100) if total_muac_measurements > 0 else 0

        return {
            "total_muac_measurements": total_muac_measurements,
            "total_muac_consents": total_muac_consents,
            "muac_consent_rate": round(muac_consent_rate, 1),
            "avg_muac_measurements_per_flw": round(avg_muac_measurements_per_flw, 2),
            "total_children_unwell": total_children_unwell,
            "total_malnutrition_diagnosed": total_malnutrition_diagnosed,
            "total_under_treatment": total_under_treatment,
            "total_va_doses": total_va_doses,
            "total_sam": total_sam,
            "sam_rate": round(sam_rate, 1),
            "total_mam": total_mam,
            "mam_rate": round(mam_rate, 1),
        }


class CHCNutritionStreamView(LoginRequiredMixin, View):
    """
    SSE streaming endpoint for CHC Nutrition analysis with real-time progress.

    Uses Server-Sent Events to push progress updates to the frontend as each
    step of the analysis pipeline completes. This gives users visibility into
    what's actually happening during long-running operations.

    Progress events are sent as JSON with format:
        {"step": 1, "total": 7, "message": "...", "complete": false}

    The final event includes the full data payload:
        {"step": 7, "total": 7, "message": "Complete!", "complete": true, "data": {...}}
    """

    def get(self, request):
        """Stream analysis progress via Server-Sent Events."""
        # Check authentication
        if not request.user.is_authenticated:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        # Check labs context
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        if not opportunity_id:
            return JsonResponse(
                {"error": "No opportunity selected. Please select an opportunity from the labs context."},
                status=400,
            )

        # Return streaming response
        response = StreamingHttpResponse(
            self._stream_analysis(request, labs_context, opportunity_id),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        return response

    def _stream_analysis(self, request, labs_context: dict, opportunity_id: int) -> Generator[str, None, None]:
        """Stream analysis progress via SSE, delegating to the shared pipeline."""

        def send_sse(message: str, data: dict | None = None) -> str:
            event = {"message": message, "complete": data is not None}
            if data:
                event["data"] = data
            return f"data: {json.dumps(event)}\n\n"

        def format_bytes(b: int) -> str:
            return f"{b / (1024 * 1024):.1f} MB"

        flw_result = None
        from_cache = False

        for event_type, data in AnalysisPipeline(request).stream_analysis(CHC_NUTRITION_CONFIG, opportunity_id):
            if event_type == EVENT_STATUS:
                from_cache = from_cache or "Cache hit" in data["message"]
                yield send_sse(data["message"])
            elif event_type == EVENT_DOWNLOAD:
                pct = f" ({int(data['bytes'] / data['total'] * 100)}%)" if data["total"] else ""
                yield send_sse(f"Downloading... {format_bytes(data['bytes'])}{pct}")
            elif event_type == EVENT_RESULT:
                flw_result = data

        # Build response with FLW names
        flw_names = get_flw_names_for_opportunity(request)
        response_data = self._build_response(request, flw_result, flw_names, labs_context, from_cache)
        yield send_sse("Complete!", response_data)

    def _build_response(self, request, flw_result, flw_names: dict, labs_context: dict, from_cache: bool) -> dict:
        """Build the final response data payload."""
        flws_data = []
        for flw in flw_result.rows:
            display_name = flw_names.get(flw.username, flw.username)

            # Calculate gender split
            male_count = flw.custom_fields.get("male_count") or 0
            female_count = flw.custom_fields.get("female_count") or 0
            total_gendered = male_count + female_count
            gender_split_female_pct = round((female_count / total_gendered) * 100, 1) if total_gendered > 0 else None

            flw_data = {
                "username": flw.username,
                "display_name": display_name,
                "total_visits": flw.total_visits,
                "approved_visits": flw.approved_visits,
                "approval_rate": round(flw.approval_rate, 1) if flw.approval_rate else 0,
                "days_active": flw.days_active,
                "custom_fields": flw.custom_fields,
                "gender_split_female_pct": gender_split_female_pct,
                "male_count": male_count,
                "female_count": female_count,
            }
            flws_data.append(flw_data)

        # Calculate summary stats
        summary = flw_result.get_summary_stats()

        # Calculate nutrition summary
        nutrition_summary = self._get_nutrition_summary(flw_result)

        # Get opportunity info
        opportunity = labs_context.get("opportunity", {})
        deliver_app = opportunity.get("deliver_app", {})

        return {
            "success": True,
            "flws": flws_data,
            "summary": summary,
            "nutrition_summary": nutrition_summary,
            "total_visits": flw_result.metadata.get("total_visits", 0),
            "opportunity_id": labs_context.get("opportunity_id"),
            "opportunity_name": labs_context.get("opportunity_name"),
            "deliver_app_cc_app_id": deliver_app.get("cc_app_id"),
            "deliver_app_cc_domain": deliver_app.get("cc_domain"),
            "from_cache": from_cache,
        }

    def _get_nutrition_summary(self, result) -> dict:
        """Calculate nutrition-specific summary statistics."""
        if not result.rows:
            return {}

        total_muac_measurements = sum(row.custom_fields.get("muac_measurements_count") or 0 for row in result.rows)
        total_muac_consents = sum(row.custom_fields.get("muac_consent_count") or 0 for row in result.rows)
        total_children_unwell = sum(row.custom_fields.get("children_unwell_count") or 0 for row in result.rows)
        total_malnutrition_diagnosed = sum(
            row.custom_fields.get("malnutrition_diagnosed_count") or 0 for row in result.rows
        )
        total_under_treatment = sum(
            row.custom_fields.get("under_malnutrition_treatment_count") or 0 for row in result.rows
        )
        total_va_doses = sum(row.custom_fields.get("received_va_dose_before_count") or 0 for row in result.rows)
        total_sam = sum(row.custom_fields.get("sam_count") or 0 for row in result.rows)
        total_mam = sum(row.custom_fields.get("mam_count") or 0 for row in result.rows)

        avg_muac_measurements_per_flw = total_muac_measurements / len(result.rows) if result.rows else 0
        muac_consent_rate = (total_muac_consents / total_muac_measurements * 100) if total_muac_measurements > 0 else 0
        sam_rate = (total_sam / total_muac_measurements * 100) if total_muac_measurements > 0 else 0
        mam_rate = (total_mam / total_muac_measurements * 100) if total_muac_measurements > 0 else 0

        return {
            "total_muac_measurements": total_muac_measurements,
            "total_muac_consents": total_muac_consents,
            "muac_consent_rate": round(muac_consent_rate, 1),
            "avg_muac_measurements_per_flw": round(avg_muac_measurements_per_flw, 2),
            "total_children_unwell": total_children_unwell,
            "total_malnutrition_diagnosed": total_malnutrition_diagnosed,
            "total_under_treatment": total_under_treatment,
            "total_va_doses": total_va_doses,
            "total_sam": total_sam,
            "sam_rate": round(sam_rate, 1),
            "total_mam": total_mam,
            "mam_rate": round(mam_rate, 1),
        }
