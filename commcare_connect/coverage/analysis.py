"""
Coverage analysis module.

Provides coverage-specific enrichment for visit analysis results.

Uses the pipeline pattern:
1. AnalysisPipeline.run_analysis() - get cached visit-level data from labs framework
2. enrich_with_coverage_context() - add DU/SA geographic context

This enables coverage to reuse the same cached visit data as other analysis views.
"""

import logging

from django.http import HttpRequest

from commcare_connect.labs.analysis.config import AnalysisPipelineConfig, CacheStage, FieldComputation
from commcare_connect.labs.analysis.models import VisitAnalysisResult
from commcare_connect.labs.analysis.pipeline import AnalysisPipeline

logger = logging.getLogger(__name__)


def enrich_with_coverage_context(
    visit_result: VisitAnalysisResult, du_lookup: dict[str, dict] | None = None
) -> VisitAnalysisResult:
    """
    Enrich a VisitAnalysisResult with coverage-specific context.

    Adds service_area_id to visits based on their delivery unit name.

    Args:
        visit_result: Pre-computed VisitAnalysisResult from compute_visit_analysis()
        du_lookup: Dict mapping du_name -> {service_area_id, ...}

    Returns:
        The same VisitAnalysisResult with service_area_id populated

    Example:
        # Get cached visit analysis
        visit_result = compute_visit_analysis(request, config)

        # Build DU lookup from coverage data
        du_lookup = {du.du_name: {"service_area_id": du.service_area_id} for du in coverage.delivery_units.values()}

        # Enrich with geographic context
        enriched = enrich_with_coverage_context(visit_result, du_lookup)
    """
    if not du_lookup:
        return visit_result

    enriched_count = 0
    null_du_count = 0
    unmatched_du_count = 0
    sample_visit_du_names = []
    sample_unmatched = []
    sample_computed_keys = []

    for i, row in enumerate(visit_result.rows):
        # Get DU name from computed field (not the base deliver_unit_name field)
        du_name = row.computed.get("du_name", "")

        # Debug: Log computed fields for first few visits
        if i < 3:
            sample_computed_keys.append(list(row.computed.keys()))

        if not du_name:
            null_du_count += 1
            continue

        if i < 5:  # Collect sample for debugging
            sample_visit_du_names.append(du_name)

        if du_name in du_lookup:
            du_info = du_lookup[du_name]
            row.service_area_id = du_info.get("service_area_id", "")
            enriched_count += 1
        else:
            unmatched_du_count += 1
            if len(sample_unmatched) < 5:
                sample_unmatched.append(du_name)

    logger.info(
        f"Enriched {enriched_count}/{len(visit_result.rows)} visits with DU context "
        f"({null_du_count} null/empty, {unmatched_du_count} unmatched)"
    )
    if sample_computed_keys:
        logger.info(f"Sample computed keys from visits: {sample_computed_keys[:3]}")
    if sample_visit_du_names:
        logger.info(f"Sample visit DU names: {sample_visit_du_names[:5]}")
    if sample_unmatched:
        logger.warning(f"Sample unmatched DU names: {sample_unmatched}")

    return visit_result


def get_coverage_visit_analysis(
    request: HttpRequest,
    config: AnalysisPipelineConfig,
    du_lookup: dict[str, dict] | None = None,
    use_cache: bool = True,
    cache_tolerance_minutes: int | None = None,
) -> VisitAnalysisResult:
    """
    Get visit analysis with coverage context, using cached data.

    Pipeline:
    1. Get cached VisitAnalysisResult via AnalysisPipeline.run_analysis()
    2. Enrich with DU/SA context

    Args:
        request: HttpRequest with labs context
        config: AnalysisPipelineConfig defining field computations
        du_lookup: Dict mapping du_name -> {service_area_id, ...}
        use_cache: Whether to use caching (default: True)
        cache_tolerance_minutes: Accept cache if age < N minutes (even if counts mismatch)
            NOTE: cache_tolerance_minutes is not currently used by AnalysisPipeline,
            but kept for API compatibility. Force refresh via ?refresh=1 query param.

    Returns:
        VisitAnalysisResult with service_area_id populated

    Example:
        from commcare_connect.coverage.analysis import COVERAGE_BASE_CONFIG, get_coverage_visit_analysis

        # Build DU lookup from coverage data
        du_lookup = {du.du_name: {"service_area_id": du.service_area_id} for du in coverage.delivery_units.values()}

        # Get cached analysis with coverage context
        result = get_coverage_visit_analysis(request, COVERAGE_BASE_CONFIG, du_lookup)
    """
    # Ensure config requests visit-level output (not aggregated)
    # This is important because coverage needs individual visit data
    if config.terminal_stage != CacheStage.VISIT_LEVEL:
        logger.warning(
            f"[Coverage] Config has terminal_stage={config.terminal_stage.value}, "
            "coverage analysis requires VISIT_LEVEL. Overriding."
        )
        # Create a modified config with correct terminal_stage
        config = AnalysisPipelineConfig(
            grouping_key=config.grouping_key,
            fields=config.fields,
            histograms=config.histograms,
            filters=config.filters,
            date_field=config.date_field,
            experiment=config.experiment,
            terminal_stage=CacheStage.VISIT_LEVEL,
        )

    # Use the AnalysisPipeline (backend-agnostic)
    pipeline = AnalysisPipeline(request)
    visit_result = pipeline.stream_analysis_ignore_events(config)

    # Enrich with coverage context
    return enrich_with_coverage_context(visit_result, du_lookup)


def create_coverage_analysis_config(
    base_config: AnalysisPipelineConfig | None = None,
    additional_fields: list | None = None,
) -> AnalysisPipelineConfig:
    """
    Create a coverage-specific analysis config.

    Optionally extends an existing config with additional coverage-specific fields.

    Args:
        base_config: Optional base config to extend
        additional_fields: Optional additional field computations

    Returns:
        AnalysisPipelineConfig suitable for coverage analysis
    """
    fields = []

    # Include fields from base config
    if base_config:
        fields.extend(base_config.fields)

    # Add additional fields
    if additional_fields:
        fields.extend(additional_fields)

    return AnalysisPipelineConfig(
        grouping_key="username",  # Not used for visit-level, but required
        fields=fields,
        histograms=base_config.histograms if base_config else [],
        filters=base_config.filters if base_config else {},
        terminal_stage=CacheStage.VISIT_LEVEL,  # Coverage needs individual visit data
    )


# Base config for coverage - SHOULD NOT BE USED
# If this is being used, it means no proper config was specified (ERROR condition)
# All opportunities should have a specific config registered (e.g., chc_nutrition)
COVERAGE_BASE_CONFIG = AnalysisPipelineConfig(
    grouping_key="username",
    fields=[
        # CommCare user ID for FLW matching
        FieldComputation(
            name="commcare_userid",
            path="form.meta.userID",
            aggregation="first",
            description="CommCare user ID from form metadata",
        ),
        # Extract CommCare delivery unit name from form JSON
        # This is REQUIRED for coverage enrichment to work
        # NOTE: Named "du_name" not "deliver_unit_name" to avoid shadowing VisitRow field
        FieldComputation(
            name="du_name",
            path="form.case.update.du_name",  # Alphanumeric DU name like 'AG015FB'
            aggregation="first",
        ),
    ],
    histograms=[],
    filters={},  # No filters - include all visits
    terminal_stage=CacheStage.VISIT_LEVEL,  # Coverage needs individual visit data
)
