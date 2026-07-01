"""
Generic views for configurable timeline UI.

These views work with any program's timeline configuration - KMC, nutrition, early childhood, etc.
The program-specific views just need to provide a config_module.
"""

from collections.abc import Generator

from django.contrib.auth.mixins import LoginRequiredMixin
from django.views.generic import TemplateView

from connect_labs.labs.analysis.sse_streaming import AnalysisPipelineSSEMixin, BaseSSEStreamView, send_sse_event


class GenericTimelineDetailView(LoginRequiredMixin, TemplateView):
    """Generic detail view for a single child's timeline."""

    template_name = "labs/configurable_ui/timeline.html"
    api_url_name = None  # Override in subclass (e.g., "kmc:api_child_data")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        child_id = self.kwargs["child_id"]
        context["child_id"] = child_id

        # Build API URL
        from django.urls import reverse

        context["api_url"] = reverse(self.api_url_name, kwargs={"child_id": child_id})

        # Add opportunity_id for image URLs
        labs_context = getattr(self.request, "labs_context", {})
        context["opportunity_id"] = labs_context.get("opportunity_id") or self.request.GET.get("opportunity_id")

        return context


class GenericTimelineDataStreamView(AnalysisPipelineSSEMixin, BaseSSEStreamView):
    """Generic SSE streaming endpoint for child timeline data - works with any config."""

    config_module = None  # Override in subclass
    pipeline_config = None  # Override in subclass - the AnalysisPipelineConfig

    def stream_data(self, request, child_id) -> Generator[str, None, None]:
        """Stream timeline data loading progress via SSE."""
        import logging
        from copy import deepcopy

        from connect_labs.labs.analysis.pipeline import AnalysisPipeline

        logger = logging.getLogger(__name__)

        # Get opportunity from labs context or query params
        labs_context = getattr(request, "labs_context", {})
        opportunity_id = labs_context.get("opportunity_id") or request.GET.get("opportunity_id")

        logger.info(f"[Timeline Stream] child_id={child_id}, opportunity_id={opportunity_id}")

        if not opportunity_id:
            yield send_sse_event("Error", error="No opportunity selected")
            return

        try:
            # Import configuration
            widgets_config = self.config_module.KMC_WIDGETS
            layout_config = self.config_module.KMC_LAYOUT
            header_fields = self.config_module.KMC_HEADER_FIELDS

            # Use pipeline with linking_field filter
            pipeline = AnalysisPipeline(request)

            # Create filtered config for this specific child
            # Use linking_field from config (allows switching between entity_id and case_id)
            filtered_config = deepcopy(self.pipeline_config)
            linking_field = self.pipeline_config.linking_field
            filtered_config.filters = {linking_field: child_id}

            logger.info(f"[Timeline Stream] Running pipeline with {linking_field} filter: {child_id}")

            # Stream pipeline events using mixin
            pipeline_stream = pipeline.stream_analysis(filtered_config, opportunity_id=opportunity_id)
            yield from self.stream_pipeline_events(pipeline_stream)

            # Result is now available in self._pipeline_result
            result = self._pipeline_result

            if not result or not result.rows:
                logger.warning(f"[Timeline Stream] No visits found for {linking_field}={child_id}")
                yield send_sse_event("Error", error=f"No visits found for child {child_id}")
                return

            logger.info(f"[Timeline Stream] Pipeline returned {len(result.rows)} visits for child {child_id}")

            yield send_sse_event("Processing timeline data...")

            # Sort by time_end (form submission end time) for consistent chronological order
            def get_sort_key(row):
                time_end = row.computed.get("time_end")
                if time_end:
                    return time_end
                return row.visit_date.isoformat() if row.visit_date else ""

            child_rows = sorted(result.rows, key=get_sort_key)

            # Build timeline data from computed fields ONLY
            timeline_data = self._build_timeline_data(
                child_id, child_rows, widgets_config, layout_config, header_fields
            )

            logger.info(f"[Timeline Stream] Sending complete event with {len(timeline_data['visits'])} visits")

            # Send complete event with data
            yield send_sse_event("Complete", data=timeline_data)

        except Exception as e:
            logger.error(f"[Timeline Stream] Failed to stream timeline data: {e}", exc_info=True)
            yield send_sse_event("Error", error=f"Failed to load timeline data: {str(e)}")

    def _build_timeline_data(self, child_id, visit_rows, widgets_config, layout_config, header_fields):
        """Build complete timeline data using ONLY pipeline computed fields."""
        import logging

        logger = logging.getLogger(__name__)

        # Extract first visit for header
        first_row = visit_rows[0]
        last_row = visit_rows[-1]

        # Header data from computed fields
        # Status fields should use LAST visit (current state), others use FIRST visit (registration data)
        status_fields = {"kmc_status"}  # Fields that represent current status, not registration data
        header = {}
        for field_name in header_fields.keys():
            if field_name in status_fields:
                # Status fields: use most recent visit for current state
                header[field_name] = last_row.computed.get(field_name)
            else:
                # Demographic/registration fields: use first visit
                header[field_name] = first_row.computed.get(field_name)

        # Calculate weight metrics from visit data
        # Weight is stored in computed fields from the pipeline
        first_weight = first_row.computed.get("weight")
        current_weight = last_row.computed.get("weight")

        logger.info(f"[Timeline Stream] Weight extraction - first: {first_weight}, current: {current_weight}")

        if first_weight is not None:
            header["starting_weight"] = first_weight

        if current_weight is not None:
            header["current_weight"] = current_weight

        if first_weight is not None and current_weight is not None:
            header["weight_gain"] = current_weight - first_weight
        else:
            header["weight_gain"] = None

        logger.info(f"[Timeline Stream] Header data with weights: {header}")

        # Extract data for each visit from computed fields (including images)
        visit_data = []
        for row in visit_rows:
            visit_info = {
                "visit_id": row.id,
                "visit_date": row.visit_date.isoformat() if row.visit_date else None,  # Raw datetime for formatting
                "widgets": {},  # Data organized by widget_id
                "images": row.computed.get("images_with_questions", []),  # Pre-computed by pipeline
            }

            # Extract data for each widget from computed fields
            for widget_id, widget_config in widgets_config.items():
                widget_data = {}

                # Extract each field for this widget from computed fields
                # Pipeline field names should match widget field names
                for field_name in widget_config.field_extractors.keys():
                    widget_data[field_name] = row.computed.get(field_name)

                visit_info["widgets"][widget_id] = widget_data

            visit_data.append(visit_info)

        logger.info(f"[Timeline Stream] Built {len(visit_data)} visit data objects")

        # Serialize widget configs for frontend
        widget_configs_serialized = {}
        for widget_id, config in widgets_config.items():
            widget_configs_serialized[widget_id] = {
                "widget_type": config.widget_type,
                "title": config.title,
                "options": config.options,
                "field_labels": {
                    field_name: extractor.display_name for field_name, extractor in config.field_extractors.items()
                },
            }

        return {
            "child_id": child_id,
            "header": header,
            "visits": visit_data,
            "layout": {
                "left": layout_config.left_widgets,
                "center": layout_config.center_widgets,
                "right": layout_config.right_widgets,
            },
            "widget_configs": widget_configs_serialized,
        }

    def get(self, request, *args, **kwargs):
        """Override get to pass child_id to stream_data."""
        from django.http import StreamingHttpResponse

        if not request.user.is_authenticated:
            from django.http import JsonResponse

            return JsonResponse({"error": "Not authenticated"}, status=401)

        child_id = kwargs.get("child_id")

        response = StreamingHttpResponse(
            self.stream_data(request, child_id),
            content_type="text/event-stream",
        )
        response["Cache-Control"] = "no-cache"
        response["X-Accel-Buffering"] = "no"  # Disable nginx buffering
        return response
