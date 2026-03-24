"""
API views for labs analysis pipeline.

Provides REST API endpoints for accessing FLW analysis results.
"""

import logging

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import JsonResponse
from django.views import View

from commcare_connect.labs.analysis.config import AnalysisPipelineConfig, CacheStage
from commcare_connect.labs.analysis.models import FLWAnalysisResult
from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

logger = logging.getLogger(__name__)


class FLWAnalysisAPIView(LoginRequiredMixin, View):
    """
    API endpoint to get FLW-level analysis results.

    Query Parameters:
        - config: (optional) Name of a registered config to use (e.g., "chc_nutrition")
        - grouping_key: (optional, default: "username") Field to group by
        - experiment: (optional) Experiment name for caching
        - refresh: (optional) Set to "1" to force refresh cache
        - use_labs_record_cache: (optional) Set to "true" to use LabsRecord cache

    Returns:
        JSON response with FLW analysis results:
        {
            "opportunity_id": int,
            "opportunity_name": str,
            "rows": [...],  # List of FLW rows
            "metadata": {...},
            "computed_at": "ISO datetime string",
            "row_count": int
        }

    Example:
        GET /labs/api/analysis/flw/?config=chc_nutrition
        GET /labs/api/analysis/flw/?grouping_key=username&experiment=my_analysis
    """

    def get(self, request):
        """Return FLW analysis results as JSON."""
        try:
            # Check labs context (set by middleware)
            labs_context = getattr(request, "labs_context", {})
            opportunity_id = labs_context.get("opportunity_id")

            if not opportunity_id:
                return JsonResponse(
                    {"error": "No opportunity selected. Please select an opportunity from the labs context."},
                    status=400,
                )

            # Get config from query params
            config = self._get_config(request)

            if not config:
                return JsonResponse(
                    {
                        "error": "Could not determine analysis configuration. "
                        "Provide 'config' parameter or ensure config registry is set up."
                    },
                    status=400,
                )

            logger.info(
                f"[FLW Analysis API] Starting analysis for opportunity {opportunity_id}, "
                f"experiment={config.experiment}, grouping_key={config.grouping_key}"
            )

            # Run the unified analysis pipeline
            # This handles all caching (LabsRecord if ?use_labs_record_cache=true, Redis, file)
            result = AnalysisPipeline(request).stream_analysis_ignore_events(config)

            # Ensure we got an FLW result (not visit-level)
            if not isinstance(result, FLWAnalysisResult):
                return JsonResponse(
                    {"error": "Expected FLW-level result but got visit-level result. Check config terminal_stage."},
                    status=500,
                )

            # Convert result to dict for JSON serialization
            result_dict = result.to_dict()

            logger.info(
                f"[FLW Analysis API] Returning {result_dict.get('row_count', 0)} FLW rows "
                f"for opportunity {opportunity_id}"
            )

            return JsonResponse(result_dict)

        except Exception as e:
            logger.exception(f"[FLW Analysis API] Error processing request: {e}")
            return JsonResponse({"error": str(e)}, status=500)

    def _get_config(self, request) -> AnalysisPipelineConfig | None:
        """
        Get analysis config from request parameters.

        Tries in order:
        1. Registered config by name (from config registry)
        2. Minimal default config with query params
        """
        config_name = request.GET.get("config")

        # Try to get from config registry if name provided
        if config_name:
            try:
                from commcare_connect.coverage.config_registry import get_config

                registered_config = get_config(config_name)
                if registered_config:
                    logger.info(f"[FLW Analysis API] Using registered config: {config_name}")
                    # Ensure terminal_stage is AGGREGATED for FLW results
                    if registered_config.terminal_stage != CacheStage.AGGREGATED:
                        # Create a copy with AGGREGATED terminal stage
                        return AnalysisPipelineConfig(
                            grouping_key=registered_config.grouping_key,
                            fields=registered_config.fields,
                            histograms=registered_config.histograms,
                            filters=registered_config.filters,
                            date_field=registered_config.date_field,
                            experiment=registered_config.experiment or config_name,
                            terminal_stage=CacheStage.AGGREGATED,
                        )
                    return registered_config
            except ImportError:
                logger.warning("[FLW Analysis API] Config registry not available")
            except Exception as e:
                logger.warning(f"[FLW Analysis API] Failed to get registered config '{config_name}': {e}")

        # Fall back to minimal default config
        grouping_key = request.GET.get("grouping_key", "username")
        experiment = request.GET.get("experiment", "analysis")

        logger.info(
            f"[FLW Analysis API] Using minimal default config: grouping_key={grouping_key}, experiment={experiment}"
        )

        return AnalysisPipelineConfig(
            grouping_key=grouping_key,
            fields=[],
            histograms=[],
            filters={},
            experiment=experiment,
            terminal_stage=CacheStage.AGGREGATED,
        )
