"""
LabsRecord-based cache backend.

================================================================================
EXPERIMENTAL - NOT YET IN USE
================================================================================

This module provides a cache backend using the LabsRecord API for persistent
cross-session storage. It is designed as a third-tier cache after Redis:

- Redis: Fast, in-memory, TTL-based (1 hour)
- LabsRecord: Persistent in production DB, cross-user shareable

This is under development and not currently integrated into the analysis pipeline.
================================================================================
"""

import logging
from datetime import datetime
from typing import Any

from django.http import HttpRequest

logger = logging.getLogger(__name__)


class LabsRecordCacheManager:
    """
    Cache backend using LabsRecord API for persistent cross-session storage.

    Uses the LabsRecordAPIClient to store/retrieve serialized analysis results.
    Cache entries are identified by experiment + analysis_type + opportunity_id.

    Cache invalidation is based on visit_count stored in the record metadata.
    """

    def __init__(self, request: HttpRequest, experiment: str):
        """
        Initialize LabsRecord cache.

        Args:
            request: HttpRequest with labs OAuth and context
            experiment: Experiment name (e.g., "chc_nutrition", "coverage")
        """
        self.request = request
        self.experiment = experiment
        self.access_token = request.session.get("labs_oauth", {}).get("access_token")
        self.labs_context = getattr(request, "labs_context", {})
        self.opportunity_id = self.labs_context.get("opportunity_id")

        logger.debug(f"LabsRecordCacheManager initialized: experiment={experiment}, opp={self.opportunity_id}")

    def _get_api_client(self):
        """Get LabsRecordAPIClient instance."""
        from connect_labs.labs.integrations.connect.api_client import LabsRecordAPIClient

        return LabsRecordAPIClient(
            access_token=self.access_token,
            opportunity_id=self.opportunity_id,
        )

    def get(self, analysis_type: str) -> dict | None:
        """
        Load cached result from LabsRecord.

        Args:
            analysis_type: Type of analysis (e.g., "flw_analysis", "visit_analysis")

        Returns:
            Dict with 'visit_count', 'cached_at', 'result' or None if not found
        """
        if not self.access_token or not self.opportunity_id:
            logger.debug("LabsRecordCacheManager.get() skipped - missing auth or context")
            return None

        try:
            client = self._get_api_client()
            records = client.get_records(
                experiment=self.experiment,
                type=f"cache_{analysis_type}",
            )

            if not records:
                logger.info(
                    f"LabsRecordCacheManager MISS: no record for {self.experiment}/{analysis_type} "
                    f"(opp {self.opportunity_id})"
                )
                return None

            # Get the most recent record
            record = records[0]
            data = record.data

            logger.info(
                f"LabsRecordCacheManager HIT: found record for {self.experiment}/{analysis_type} "
                f"(opp {self.opportunity_id}, visit_count={data.get('visit_count')})"
            )

            return data

        except Exception as e:
            logger.warning(f"LabsRecordCacheManager.get() failed: {e}")
            return None

    def set(self, analysis_type: str, result: Any, visit_count: int) -> bool:
        """
        Save result to LabsRecord.

        Args:
            analysis_type: Type of analysis (e.g., "flw_analysis", "visit_analysis")
            result: Analysis result object to cache
            visit_count: Visit count for cache invalidation

        Returns:
            True if saved successfully
        """
        if not self.access_token or not self.opportunity_id:
            logger.debug("LabsRecordCacheManager.set() skipped - missing auth or context")
            return False

        try:
            # Serialize the result
            cache_data = {
                "visit_count": visit_count,
                "cached_at": datetime.utcnow().isoformat(),
                "result": result.to_dict() if hasattr(result, "to_dict") else result,
            }

            client = self._get_api_client()

            # Check if record already exists
            existing = client.get_records(
                experiment=self.experiment,
                type=f"cache_{analysis_type}",
            )

            if existing:
                # Update existing record
                record = existing[0]
                client.update_record(
                    record_id=record.id,
                    experiment=self.experiment,
                    type=f"cache_{analysis_type}",
                    data=cache_data,
                )
                logger.info(
                    f"LabsRecordCacheManager updated: {self.experiment}/{analysis_type} "
                    f"(opp {self.opportunity_id}, visit_count={visit_count})"
                )
            else:
                # Create new record
                client.create_record(
                    experiment=self.experiment,
                    type=f"cache_{analysis_type}",
                    data=cache_data,
                )
                logger.info(
                    f"LabsRecordCacheManager created: {self.experiment}/{analysis_type} "
                    f"(opp {self.opportunity_id}, visit_count={visit_count})"
                )

            return True

        except Exception as e:
            logger.warning(f"LabsRecordCacheManager.set() failed: {e}")
            return False

    def is_valid(self, cached_data: dict | None, current_visit_count: int | None = None) -> bool:
        """
        Check if cached data is still valid based on visit count.

        Cache is valid if cached_count >= expected_count, because sometimes
        the API returns more visits than what's reported in opportunity metadata.

        Args:
            cached_data: Cached data dict from get()
            current_visit_count: Expected visit count for validation

        Returns:
            True if cache is valid
        """
        if not cached_data:
            return False

        # If no visit count validation needed, cache is valid
        if current_visit_count is None:
            return True

        # Cached count must be >= expected count
        cached_count = cached_data.get("visit_count")
        if cached_count is not None and cached_count < current_visit_count:
            logger.info(
                f"LabsRecordCacheManager invalid: cached_count={cached_count} < expected={current_visit_count}"
            )
            return False

        if cached_count is not None:
            logger.debug(
                f"LabsRecordCacheManager valid: cached_count={cached_count} >= expected={current_visit_count}"
            )

        return True

    def clear(self, analysis_type: str) -> bool:
        """
        Clear cached record for an analysis type.

        Args:
            analysis_type: Type of analysis to clear

        Returns:
            True if cleared successfully
        """
        if not self.access_token or not self.opportunity_id:
            return False

        try:
            client = self._get_api_client()
            records = client.get_records(
                experiment=self.experiment,
                type=f"cache_{analysis_type}",
            )

            if records:
                record_ids = [r.id for r in records]
                client.delete_records(record_ids)
                logger.info(
                    f"LabsRecordCacheManager cleared: {self.experiment}/{analysis_type} "
                    f"(opp {self.opportunity_id}, deleted {len(record_ids)} records)"
                )

            return True

        except Exception as e:
            logger.warning(f"LabsRecordCacheManager.clear() failed: {e}")
            return False
