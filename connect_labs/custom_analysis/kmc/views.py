"""
KMC-specific views using pipeline and SSE streaming.
"""

import logging
from collections.abc import Generator

import httpx
from django.conf import settings
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpResponse, JsonResponse
from django.urls import reverse
from django.views.generic import TemplateView, View

from connect_labs.custom_analysis.kmc import timeline_config
from connect_labs.custom_analysis.kmc.pipeline_config import KMC_PIPELINE_CONFIG
from connect_labs.labs.analysis.pipeline import AnalysisPipeline
from connect_labs.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, BaseSSEStreamView, send_sse_event
from connect_labs.labs.configurable_ui.views import GenericTimelineDataStreamView, GenericTimelineDetailView
from connect_labs.opportunity.models import BlobMeta

logger = logging.getLogger(__name__)


class KMCTimelineListView(LoginRequiredMixin, TemplateView):
    """
    List all KMC children - uses client-side rendering with Alpine.js.

    Pattern: Page loads instantly, SSE streams data, Alpine.js renders table.
    This avoids the complexity of django-tables2 needing data synchronously.
    """

    template_name = "custom_analysis/kmc/child_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)

        labs_context = getattr(self.request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id")

        context["opportunity_id"] = opportunity_id
        context["opportunity_name"] = labs_context.get("opportunity_name")
        context["has_context"] = bool(opportunity_id)

        # Check for OAuth token
        labs_oauth = self.request.session.get("labs_oauth", {})
        context["has_oauth"] = bool(labs_oauth.get("access_token"))

        # Provide SSE stream URL for async data loading
        if opportunity_id:
            context["stream_url"] = reverse("kmc:child_list_stream")

        # Provide timeline URL base for building child-specific URLs
        # Use a placeholder that JavaScript will replace with the actual child_id
        context["timeline_url_base"] = reverse("kmc:child_timeline", kwargs={"child_id": "__CHILD_ID__"})

        # Provide linking field name for inspector queries
        # For beneficiary_case_id, we need to query form_json path since it's not a base column
        linking_field = KMC_PIPELINE_CONFIG.linking_field
        context["linking_field"] = linking_field
        if linking_field == "beneficiary_case_id":
            # Query form_json for case_id (registration visits store it in form.case.@case_id)
            context["inspector_query_template"] = "form_json->'form'->'case'->>'@case_id' = '{}'"
        else:
            # Default to entity_id which is a base column
            context["inspector_query_template"] = "entity_id = '{}'"

        return context


class KMCTimelineDetailView(GenericTimelineDetailView):
    """Display timeline for a single KMC child."""

    api_url_name = "kmc:api_child_data_stream"


class KMCTimelineDataStreamView(GenericTimelineDataStreamView):
    """SSE streaming endpoint for KMC child timeline data."""

    config_module = timeline_config
    pipeline_config = KMC_PIPELINE_CONFIG


class KMCChildListStreamView(AnalysisPipelineSSEMixin, BaseSSEStreamView):
    """SSE streaming endpoint for loading KMC child list with progress - uses reusable mixin."""

    def stream_data(self, request) -> Generator[str, None, None]:
        """Stream child list data loading progress via SSE."""
        try:
            # Check for context (from labs context or query params)
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

            if not opportunity_id:
                yield send_sse_event("Error", error="No opportunity selected")
                return

            # Check for OAuth token
            labs_oauth = request.session.get("labs_oauth", {})
            if not labs_oauth.get("access_token"):
                yield send_sse_event("Error", error="No OAuth token found. Please log in to Connect.")
                return

            # Run analysis pipeline with streaming using mixin
            pipeline = AnalysisPipeline(request)
            pipeline_stream = pipeline.stream_analysis(KMC_PIPELINE_CONFIG, opportunity_id=opportunity_id)

            logger.info(f"[KMC] Starting stream for opportunity {opportunity_id}")

            # Stream all pipeline events as SSE (using mixin)
            yield from self.stream_pipeline_events(pipeline_stream)

            # Result is now available in self._pipeline_result
            result = self._pipeline_result
            from_cache = self._pipeline_from_cache

            # Process result into child list
            if result:
                logger.info(f"[KMC] Got result with {len(result.rows) if hasattr(result, 'rows') else 0} rows")
                yield send_sse_event("Processing visits into children...")

                # Get FLW display names
                from connect_labs.labs.analysis.data_access import get_flw_names_for_opportunity

                try:
                    flw_names = get_flw_names_for_opportunity(request)
                except Exception as e:
                    logger.warning(f"Failed to fetch FLW names: {e}")
                    flw_names = {}

                # Group by linking_field from config (allows switching between entity_id and case_id)
                # Default is entity_id, but can be set to beneficiary_case_id for twins
                linking_field = KMC_PIPELINE_CONFIG.linking_field
                children_dict = {}
                for row in result.rows:
                    # Get child_id from linking_field (either base field or computed field)
                    if linking_field == "entity_id":
                        child_id = row.entity_id
                    else:
                        child_id = row.computed.get(linking_field)
                    if not child_id:
                        continue

                    if child_id not in children_dict:
                        children_dict[child_id] = {
                            "child_id": child_id,
                            "child_name": row.computed.get("child_name"),
                            "entity_name": row.entity_name or row.computed.get("entity_name"),
                            "flw_username": row.username,
                            "flw_display_name": flw_names.get(row.username, row.username),
                            "visits": [],
                        }

                    # Append visit for this child (outside the if block!)
                    children_dict[child_id]["visits"].append(
                        {
                            "weight": row.computed.get("weight"),  # Updated field name
                            "visit_date": row.computed.get("date")  # Updated field name
                            or (row.visit_date.isoformat() if row.visit_date else None),
                            "visit_number": row.computed.get("visit_number"),
                        }
                    )

                # Calculate aggregates for each child
                children_list = []
                for child_id, child_data in children_dict.items():
                    visits = child_data["visits"]
                    if not visits:
                        continue

                    # Sort by visit date
                    visits.sort(key=lambda v: v.get("visit_date") or "")

                    # Get first and last weights
                    weights = [v.get("weight") for v in visits if v.get("weight")]  # Updated field name
                    starting_weight = weights[0] if weights else None
                    current_weight = weights[-1] if weights else None

                    child_entry = {
                        "child_id": child_id,
                        "child_name": child_data["child_name"],
                        "entity_name": child_data["entity_name"],
                        "flw_username": child_data["flw_username"],
                        "flw_display_name": child_data["flw_display_name"],
                        "visit_count": len(visits),
                        "starting_weight": starting_weight,
                        "current_weight": current_weight,
                        "last_visit_date": visits[-1].get("visit_date") if visits else None,
                    }
                    children_list.append(child_entry)

                logger.info(f"[KMC] Processed {len(children_list)} children from {len(result.rows)} visits")

                yield send_sse_event(
                    "Complete",
                    data={
                        "children": children_list,
                        "opportunity_id": opportunity_id,
                        "from_cache": from_cache,
                    },
                )
            else:
                logger.warning("[KMC] No result from pipeline")
                yield send_sse_event("Error", error="No data returned from pipeline")

        except Exception as e:
            logger.error(f"[KMC] Stream failed: {e}", exc_info=True)
            yield send_sse_event("Error", error=f"Failed to load child data: {str(e)}")


class KMCImageProxyView(LoginRequiredMixin, View):
    """Proxy view to fetch images from Connect API with authentication."""

    def get(self, request, blob_id):
        """Download and return image from Connect."""
        opportunity_id = request.GET.get("opportunity_id")
        if not opportunity_id:
            return JsonResponse({"error": "opportunity_id required"}, status=400)

        # Get OAuth token from session
        labs_oauth = request.session.get("labs_oauth", {})
        access_token = labs_oauth.get("access_token")
        if not access_token:
            return JsonResponse({"error": "Not authenticated"}, status=401)

        # Fetch image from Connect
        production_url = settings.CONNECT_PRODUCTION_URL.rstrip("/")
        url = f"{production_url}/export/opportunity/{opportunity_id}/image/"

        try:
            with httpx.Client() as client:
                response = client.get(
                    url,
                    params={"blob_id": blob_id},
                    headers={"Authorization": f"Bearer {access_token}"},
                    timeout=30.0,
                )
                response.raise_for_status()

                # Get content type from BlobMeta if available
                try:
                    blob_meta = BlobMeta.objects.get(blob_id=blob_id)
                    content_type = blob_meta.content_type or "image/jpeg"
                except BlobMeta.DoesNotExist:
                    content_type = "image/jpeg"

                return HttpResponse(response.content, content_type=content_type)

        except httpx.HTTPError as e:
            logging.error(f"[KMC] Failed to fetch image {blob_id}: {e}")
            return JsonResponse({"error": "Failed to fetch image"}, status=502)
