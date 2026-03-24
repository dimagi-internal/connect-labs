"""
Analysis pipeline - the single entry point for all analysis data access.

Usage Patterns:
--------------

Raw data access (replaces api_cache.py):
    pipeline = AnalysisPipeline(request)
    visits = pipeline.fetch_raw_visits(opportunity_id=814)
    visits_slim = pipeline.fetch_raw_visits(opportunity_id=814, skip_form_json=True)

Web Views (SSE Streaming):
    # Use stream_analysis() - it's fast when cached, shows progress when not
    pipeline = AnalysisPipeline(request)
    for event_type, data in pipeline.stream_analysis(config):
        if event_type == EVENT_STATUS:
            yield sse_event(data["message"])
        elif event_type == EVENT_RESULT:
            return data

Non-Web Contexts (Synchronous):
    # Use stream_analysis_ignore_events() for tests, scripts, enrichment
    pipeline = AnalysisPipeline(request)
    result = pipeline.stream_analysis_ignore_events(config)

Note: stream_analysis() is ALWAYS fast when data is cached (typically <1s),
      so web views don't need to check cache existence beforehand.

Uses PostgreSQL table caching with SQL computation (SQLBackend).
"""

import logging
from collections.abc import Generator
from typing import Any

import sentry_sdk
from django.conf import settings
from django.http import HttpRequest

from commcare_connect.labs.analysis.config import AnalysisPipelineConfig, CacheStage
from commcare_connect.labs.analysis.models import FLWAnalysisResult, VisitAnalysisResult

logger = logging.getLogger(__name__)

# Event type constants
EVENT_STATUS = "status"
EVENT_DOWNLOAD = "download"
EVENT_RESULT = "result"
EVENT_ERROR = "error"


def get_backend():
    """Get the SQL backend instance (lazy import to avoid AppRegistryNotReady)."""
    from commcare_connect.labs.analysis.backends.sql.backend import SQLBackend

    return SQLBackend()


class AnalysisPipeline:
    """
    Single entry point for all analysis data access.

    Facade that hides backend implementation details from callers.
    Use this instead of importing from api_cache.py or backends directly.
    """

    def __init__(self, request: HttpRequest):
        """
        Initialize pipeline with request context.

        Args:
            request: HttpRequest with labs_oauth and labs_context
        """
        self.request = request
        self.backend = get_backend()
        self.backend_name = "sql"

        # Extract context
        self.access_token = request.session.get("labs_oauth", {}).get("access_token")

        # CCHQ OAuth token (for cchq_forms data sources)
        self.cchq_access_token = request.session.get("commcare_oauth", {}).get("access_token")
        self.labs_context = getattr(request, "labs_context", {})

        if not self.access_token:
            raise ValueError("No labs OAuth token found in session")

    @property
    def opportunity_id(self) -> int | None:
        """Get opportunity ID from labs context."""
        return self.labs_context.get("opportunity_id")

    @property
    def visit_count(self) -> int:
        """Get expected visit count from labs context."""
        opportunity = self.labs_context.get("opportunity", {})
        return opportunity.get("visit_count", 0)

    @property
    def cache_tolerance_pct(self) -> int:
        """Cache tolerance percentage. Accept cache if it has >= N% of expected visits.

        Reads from ?tolerance= query param first, then PIPELINE_CACHE_TOLERANCE_PCT
        setting (default 100 for production).
        """
        if self.request and hasattr(self.request, "GET"):
            param = self.request.GET.get("tolerance")
            if param is not None:
                try:
                    val = int(param)
                    if 1 <= val <= 100:
                        return val
                except (ValueError, TypeError):
                    pass
        return getattr(settings, "PIPELINE_CACHE_TOLERANCE_PCT", 100)

    # -------------------------------------------------------------------------
    # Raw Data Access (replaces api_cache.py)
    # -------------------------------------------------------------------------

    def fetch_raw_visits(
        self,
        opportunity_id: int | None = None,
        skip_form_json: bool = False,
        filter_visit_ids: set[int] | None = None,
        force_refresh: bool = False,
        include_images: bool = False,
    ) -> list[dict]:
        """
        Fetch raw visit data. Backend handles caching internally.

        Args:
            opportunity_id: Opportunity ID (defaults to labs_context)
            skip_form_json: If True, exclude form_json (slim mode for audit selection)
            filter_visit_ids: If provided, only return visits with these IDs
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            List of visit dicts

        Examples:
            # Full visits with form_json
            visits = pipeline.fetch_raw_visits()

            # Slim mode (no form_json) for audit selection
            visits = pipeline.fetch_raw_visits(skip_form_json=True)

            # Specific visits with form_json for audit extraction
            visits = pipeline.fetch_raw_visits(filter_visit_ids={1, 2, 3})
        """
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            raise ValueError("No opportunity_id provided and none in labs_context")

        # Check URL param for force refresh
        if self.request.GET.get("refresh") == "1":
            force_refresh = True

        return self.backend.fetch_raw_visits(
            opportunity_id=opp_id,
            access_token=self.access_token,
            expected_visit_count=self.visit_count,
            force_refresh=force_refresh,
            skip_form_json=skip_form_json,
            filter_visit_ids=filter_visit_ids,
            include_images=include_images,
        )

    def has_valid_raw_cache(self, opportunity_id: int | None = None) -> bool:
        """Check if valid raw cache exists for the opportunity."""
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            return False
        return self.backend.has_valid_raw_cache(opp_id, self.visit_count, tolerance_pct=self.cache_tolerance_pct)

    def has_valid_processed_cache(self, config: AnalysisPipelineConfig, opportunity_id: int | None = None) -> bool:
        """
        Check if valid processed/computed cache exists for the config.

        This is the correct check for views that want to decide whether to
        trigger SSE loading vs render from cache. Unlike has_valid_raw_cache(),
        this checks the appropriate processed cache level based on config.terminal_stage.

        Args:
            config: Analysis configuration (determines which cache level to check)
            opportunity_id: Opportunity ID (defaults to labs_context)

        Returns:
            True if processed cache exists and is valid, False otherwise
        """
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            return False

        tolerance = self.cache_tolerance_pct
        terminal_stage = config.terminal_stage
        if terminal_stage == CacheStage.AGGREGATED:
            return (
                self.backend.get_cached_flw_result(opp_id, config, self.visit_count, tolerance_pct=tolerance)
                is not None
            )
        else:
            return (
                self.backend.get_cached_visit_result(opp_id, config, self.visit_count, tolerance_pct=tolerance)
                is not None
            )

    def filter_visits_for_audit(
        self,
        opportunity_id: int | None = None,
        usernames: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        last_n_per_user: int | None = None,
        last_n_total: int | None = None,
        sample_percentage: int = 100,
        return_visit_data: bool = False,
    ) -> list[int] | tuple[list[int], list[dict]]:
        """
        Filter visits based on audit criteria.

        Delegates to SQL backend which uses database queries with indexes
        and window functions for optimal performance.

        Args:
            opportunity_id: Opportunity ID (defaults to labs_context)
            usernames: Filter to specific FLW usernames (None = all)
            start_date: Filter visits on or after this date (ISO format)
            end_date: Filter visits on or before this date (ISO format)
            last_n_per_user: Take only last N visits per user
            last_n_total: Take only last N visits total
            sample_percentage: Random sample percentage (1-100)
            return_visit_data: If True, also return filtered visit dicts (slim, no form_json)

        Returns:
            List of visit IDs, or (visit_ids, visit_dicts) if return_visit_data=True
        """
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            return ([], []) if return_visit_data else []

        return self.backend.filter_visits_for_audit(
            opportunity_id=opp_id,
            access_token=self.access_token,
            expected_visit_count=self.visit_count,
            usernames=usernames,
            start_date=start_date,
            end_date=end_date,
            last_n_per_user=last_n_per_user,
            last_n_total=last_n_total,
            sample_percentage=sample_percentage,
            return_visit_data=return_visit_data,
        )

    # -------------------------------------------------------------------------
    # Analysis
    # -------------------------------------------------------------------------

    def stream_analysis_ignore_events(
        self,
        config: AnalysisPipelineConfig,
        opportunity_id: int | None = None,
    ) -> FLWAnalysisResult | VisitAnalysisResult:
        """
        Run analysis synchronously, ignoring progress events.

        Convenience wrapper around stream_analysis() for non-web contexts
        (tests, scripts, enrichment) that don't need progress updates.

        For web views, use stream_analysis() directly to provide real-time
        progress feedback via SSE.

        Args:
            config: Analysis configuration
            opportunity_id: Opportunity ID (defaults to labs_context)

        Returns:
            FLWAnalysisResult or VisitAnalysisResult based on config.terminal_stage
        """
        for event_type, data in self.stream_analysis(config, opportunity_id):
            if event_type == EVENT_RESULT:
                return data
            elif event_type == EVENT_ERROR:
                raise RuntimeError(f"Analysis pipeline failed: {data.get('message', 'Unknown error')}")

        raise RuntimeError("Analysis pipeline completed without returning a result")

    def _consume_raw_visits_stream(
        self, opp_id: int, force_refresh: bool = False, tolerance_pct: int = 100
    ) -> Generator[tuple[str, Any], None, None]:
        """
        Consume stream_raw_visits events and yield SSE pipeline events.

        Translates backend events (cached/progress/parsing/complete) into
        pipeline events (EVENT_STATUS/EVENT_DOWNLOAD). After iteration,
        self._visit_dicts and self._raw_data_already_stored are set.

        Usage:
            yield from self._consume_raw_visits_stream(opp_id, ...)
            visit_dicts = self._visit_dicts
            raw_data_already_stored = self._raw_data_already_stored
        """
        self._visit_dicts = None
        self._raw_data_already_stored = False

        for event in self.backend.stream_raw_visits(
            opportunity_id=opp_id,
            access_token=self.access_token,
            expected_visit_count=self.visit_count,
            force_refresh=force_refresh,
            tolerance_pct=tolerance_pct,
        ):
            event_type = event[0]
            if event_type == "cached":
                self._visit_dicts = event[1]
                self._raw_data_already_stored = True
                logger.info(f"[Pipeline/{self.backend_name}] Raw data CACHE HIT: {len(self._visit_dicts)} visits")
                yield (EVENT_STATUS, {"message": f"Using cached raw data ({len(self._visit_dicts)} visits)..."})
            elif event_type == "progress":
                _, bytes_downloaded, total_bytes = event
                yield (EVENT_DOWNLOAD, {"bytes": bytes_downloaded, "total": total_bytes})
            elif event_type == "parsing":
                csv_size = event[1]
                raw_lines = event[2] if len(event) > 2 else None
                size_mb = csv_size / (1024 * 1024)
                lines_msg = f" ({raw_lines} raw lines)" if raw_lines else ""
                yield (EVENT_STATUS, {"message": f"Parsing {size_mb:.1f} MB of data{lines_msg}..."})
            elif event_type == "complete":
                self._visit_dicts = event[1]
                self._raw_data_already_stored = True
                logger.info(f"[Pipeline/{self.backend_name}] Downloaded and parsed {len(self._visit_dicts)} visits")
                yield (EVENT_STATUS, {"message": f"Downloaded {len(self._visit_dicts)} visits"})

        if self._visit_dicts is None:
            raise RuntimeError("No data received from API")

    def stream_analysis(
        self,
        config: AnalysisPipelineConfig,
        opportunity_id: int | None = None,
    ) -> Generator[tuple[str, Any], None, None]:
        """
        Stream analysis pipeline with progress events.

        Yields:
            Tuples of (event_type, event_data):
            - ("status", {"message": "..."}) - progress updates
            - ("download", {"bytes": N, "total": M}) - download progress
            - ("result", FLWAnalysisResult|VisitAnalysisResult) - final result
            - ("error", {"message": "..."}) - error (terminates stream)

        Args:
            config: Analysis configuration
            opportunity_id: Opportunity ID (defaults to labs_context)
        """
        opp_id = opportunity_id or self.opportunity_id
        if not opp_id:
            yield (EVENT_ERROR, {"message": "No opportunity_id provided"})
            return

        force_refresh = self.request.GET.get("refresh") == "1"
        terminal_stage = config.terminal_stage

        try:
            # Check cache first
            stage_name = "FLW" if terminal_stage == CacheStage.AGGREGATED else "visit"
            logger.info(
                f"[Pipeline/{self.backend_name}] Checking {stage_name}-level cache for opp {opp_id} "
                f"(expected visits: {self.visit_count})"
            )

            # CRITICAL FIX: Detect if we have filters - we'll need to handle specially
            has_filters = bool(config.filters)
            if has_filters:
                logger.info(f"[Pipeline/{self.backend_name}] Config has filters: {list(config.filters.keys())}")

            yield (EVENT_STATUS, {"message": f"Checking {stage_name}-level cache..."})

            tolerance = self.cache_tolerance_pct
            # For CCHQ form sources, don't validate against opportunity visit count
            # (CCHQ forms have far fewer rows than Connect visits)
            # For filtered configs, skip strict tolerance — filter changes should reuse
            # existing cache, not re-download. The expires_at TTL handles staleness.
            is_cchq = config.data_source.type == "cchq_forms"
            expected_count = 0 if (is_cchq or has_filters) else self.visit_count
            if not force_refresh:
                cached_result = None
                if terminal_stage == CacheStage.AGGREGATED:
                    cached_result = self.backend.get_cached_flw_result(
                        opp_id,
                        config,
                        expected_count,
                        tolerance_pct=tolerance,
                    )
                else:
                    cached_result = self.backend.get_cached_visit_result(
                        opp_id,
                        config,
                        expected_count,
                        tolerance_pct=tolerance,
                    )

                if cached_result:
                    yield (EVENT_STATUS, {"message": f"{stage_name.capitalize()}-level cache HIT!"})
                    logger.info(
                        f"[Pipeline/{self.backend_name}] CACHE HIT ({stage_name}-level) for opp {opp_id}: "
                        f"{len(cached_result.rows)} rows"
                    )
                    yield (EVENT_RESULT, cached_result)
                    return
                else:
                    logger.info(f"[Pipeline/{self.backend_name}] CACHE MISS ({stage_name}-level) for opp {opp_id}")

                    # CRITICAL FIX: If config has filters on cache miss, we must cache UNFILTERED data first
                    # Filters should only be applied when reading from cache, never when writing to cache
                    # This ensures the cache contains the full dataset and can serve all filtered queries
                    if config.filters:
                        from copy import deepcopy

                        logger.info(
                            f"[Pipeline/{self.backend_name}] Filtered config detected on cache miss - "
                            f"will cache full unfiltered dataset first"
                        )
                        yield (EVENT_STATUS, {"message": "Building cache with full dataset..."})

                        # Create unfiltered version for caching
                        unfiltered_config = deepcopy(config)
                        unfiltered_config.filters = {}

                        # Download, process, and cache with unfiltered config
                        yield (EVENT_STATUS, {"message": "Connecting to Connect API..."})
                        logger.info(f"[Pipeline/{self.backend_name}] Downloading visit data for opp {opp_id}...")

                        if unfiltered_config.data_source.type == "cchq_forms":
                            from commcare_connect.labs.analysis.backends.sql.cchq_fetcher import (
                                fetch_cchq_forms_as_visit_dicts,
                            )

                            yield (
                                EVENT_STATUS,
                                {
                                    "message": (
                                        f"Fetching '{unfiltered_config.data_source.form_name}'" " from CommCare HQ..."
                                    )
                                },
                            )
                            visit_dicts = fetch_cchq_forms_as_visit_dicts(
                                request=self.request,
                                data_source=unfiltered_config.data_source,
                                access_token=self.access_token,
                                opportunity_id=opp_id,
                            )
                            raw_data_already_stored = False
                        else:
                            yield from self._consume_raw_visits_stream(
                                opp_id,
                                force_refresh=force_refresh,
                                tolerance_pct=tolerance,
                            )
                            visit_dicts = self._visit_dicts
                            raw_data_already_stored = self._raw_data_already_stored

                        if visit_dicts is None:
                            raise RuntimeError("No data received from API")

                        # Process and cache with UNFILTERED config (critical!)
                        yield (EVENT_STATUS, {"message": f"Processing {len(visit_dicts)} visits..."})
                        logger.info(
                            f"[Pipeline/{self.backend_name}] Processing {len(visit_dicts)} visits "
                            "with unfiltered config"
                        )

                        self.backend.process_and_cache(
                            self.request,
                            unfiltered_config,
                            opp_id,
                            visit_dicts,
                            skip_raw_store=raw_data_already_stored,
                        )
                        del visit_dicts

                        # Now read from cache with ORIGINAL FILTERED config
                        logger.info(f"[Pipeline/{self.backend_name}] Reading cached data with filters applied")
                        yield (EVENT_STATUS, {"message": "Applying filters..."})

                        # expected_count=0: we just wrote this cache, skip count validation
                        if terminal_stage == CacheStage.AGGREGATED:
                            filtered_result = self.backend.get_cached_flw_result(
                                opp_id,
                                config,
                                0,
                                tolerance_pct=tolerance,
                            )
                        else:
                            filtered_result = self.backend.get_cached_visit_result(
                                opp_id,
                                config,
                                0,
                                tolerance_pct=tolerance,
                            )

                        if filtered_result:
                            yield (EVENT_STATUS, {"message": "Complete!"})
                            logger.info(
                                f"[Pipeline/{self.backend_name}] Complete: {len(filtered_result.rows)} rows (filtered)"
                            )
                            yield (EVENT_RESULT, filtered_result)
                            return
                        else:
                            raise RuntimeError("Failed to read filtered data from cache after caching full dataset")
            else:
                logger.info(f"[Pipeline/{self.backend_name}] Force refresh requested, skipping cache")

                # CRITICAL FIX: If force refresh with filters, must cache unfiltered first
                if config.filters:
                    from copy import deepcopy

                    logger.info(
                        f"[Pipeline/{self.backend_name}] Force refresh with filters - "
                        f"will cache full unfiltered dataset first"
                    )
                    yield (EVENT_STATUS, {"message": "Force refresh: caching full dataset..."})

                    # Create unfiltered version
                    unfiltered_config = deepcopy(config)
                    unfiltered_config.filters = {}

                    # Download and process with unfiltered config
                    yield (EVENT_STATUS, {"message": "Connecting to Connect API..."})
                    logger.info(f"[Pipeline/{self.backend_name}] Downloading visit data for opp {opp_id}...")

                    if unfiltered_config.data_source.type == "cchq_forms":
                        from commcare_connect.labs.analysis.backends.sql.cchq_fetcher import (
                            fetch_cchq_forms_as_visit_dicts,
                        )

                        yield (
                            EVENT_STATUS,
                            {"message": f"Fetching '{unfiltered_config.data_source.form_name}' from CommCare HQ..."},
                        )
                        visit_dicts = fetch_cchq_forms_as_visit_dicts(
                            request=self.request,
                            data_source=unfiltered_config.data_source,
                            access_token=self.access_token,
                            opportunity_id=opp_id,
                        )
                        raw_data_already_stored = False
                    else:
                        yield from self._consume_raw_visits_stream(
                            opp_id,
                            force_refresh=True,
                        )
                        visit_dicts = self._visit_dicts
                        raw_data_already_stored = self._raw_data_already_stored

                    if visit_dicts is None:
                        raise RuntimeError("No data received from API")

                    # Process and cache with UNFILTERED config
                    yield (EVENT_STATUS, {"message": f"Processing {len(visit_dicts)} visits..."})
                    logger.info(
                        f"[Pipeline/{self.backend_name}] Processing {len(visit_dicts)} visits with unfiltered config"
                    )

                    self.backend.process_and_cache(
                        self.request,
                        unfiltered_config,
                        opp_id,
                        visit_dicts,
                        skip_raw_store=raw_data_already_stored,
                    )
                    del visit_dicts

                    # Read back with filters
                    logger.info(f"[Pipeline/{self.backend_name}] Reading cached data with filters applied")
                    yield (EVENT_STATUS, {"message": "Applying filters..."})

                    # expected_count=0: we just wrote this cache, skip count validation
                    if terminal_stage == CacheStage.AGGREGATED:
                        filtered_result = self.backend.get_cached_flw_result(
                            opp_id,
                            config,
                            0,
                            tolerance_pct=tolerance,
                        )
                    else:
                        filtered_result = self.backend.get_cached_visit_result(
                            opp_id,
                            config,
                            0,
                            tolerance_pct=tolerance,
                        )

                    if filtered_result:
                        yield (EVENT_STATUS, {"message": "Complete!"})
                        logger.info(
                            f"[Pipeline/{self.backend_name}] Complete: {len(filtered_result.rows)} rows (filtered)"
                        )
                        yield (EVENT_RESULT, filtered_result)
                        return
                    else:
                        raise RuntimeError("Failed to read filtered data from cache after force refresh")

            # CCHQ data source: fetch synchronously (no streaming progress)
            if config.data_source.type == "cchq_forms":
                from commcare_connect.labs.analysis.backends.sql.cchq_fetcher import fetch_cchq_forms_as_visit_dicts

                yield (EVENT_STATUS, {"message": f"Fetching '{config.data_source.form_name}' from CommCare HQ..."})

                visit_dicts = fetch_cchq_forms_as_visit_dicts(
                    request=self.request,
                    data_source=config.data_source,
                    access_token=self.access_token,
                    opportunity_id=opp_id,
                )

                if not visit_dicts:
                    yield (EVENT_STATUS, {"message": "No forms found"})
                    yield (EVENT_RESULT, VisitAnalysisResult(opportunity_id=opp_id, rows=[], metadata={}))
                    return

                yield (EVENT_STATUS, {"message": f"Processing {len(visit_dicts)} forms..."})
                result = self.backend.process_and_cache(self.request, config, opp_id, visit_dicts)
                yield (EVENT_STATUS, {"message": "Complete!"})
                yield (EVENT_RESULT, result)
                return

            # Stream raw data fetch with progress (unfiltered path)
            yield (EVENT_STATUS, {"message": "Connecting to Connect API..."})
            logger.info(f"[Pipeline/{self.backend_name}] Downloading visit data for opp {opp_id}...")

            yield from self._consume_raw_visits_stream(
                opp_id,
                force_refresh=force_refresh,
                tolerance_pct=tolerance,
            )
            visit_dicts = self._visit_dicts
            raw_data_already_stored = self._raw_data_already_stored

            # Process with backend
            yield (EVENT_STATUS, {"message": f"Processing {len(visit_dicts)} visits..."})
            logger.info(f"[Pipeline/{self.backend_name}] Processing {len(visit_dicts)} visits")

            result = self.backend.process_and_cache(
                self.request,
                config,
                opp_id,
                visit_dicts,
                skip_raw_store=raw_data_already_stored,
            )
            del visit_dicts

            yield (EVENT_STATUS, {"message": "Complete!"})
            logger.info(f"[Pipeline/{self.backend_name}] Complete: {len(result.rows)} rows")

            yield (EVENT_RESULT, result)

        except Exception as e:
            logger.error(f"[Pipeline/{self.backend_name}] Error: {e}", exc_info=True)
            sentry_sdk.capture_exception(e)
            yield (EVENT_ERROR, {"message": str(e)})
