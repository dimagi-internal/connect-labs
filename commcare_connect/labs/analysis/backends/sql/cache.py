"""
SQL cache manager for the analysis framework.

Handles reading/writing cache data to PostgreSQL tables.
"""

import logging
import random
from datetime import date, datetime, timedelta

from django.db import transaction
from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from commcare_connect.labs.analysis.backends.sql.models import ComputedFLWCache, ComputedVisitCache, RawVisitCache
from commcare_connect.labs.analysis.config import AnalysisPipelineConfig
from commcare_connect.labs.analysis.utils import get_config_hash

logger = logging.getLogger(__name__)

# Default cache TTL. Read from settings to allow dev override.
# Production: 1 hour. Local dev: configurable via PIPELINE_CACHE_TTL_HOURS.
DEFAULT_TTL_HOURS = 1


def _parse_date(value) -> date | None:
    """Parse a date value from various formats."""
    if value is None:
        return None
    if isinstance(value, date):
        return value
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, str):
        # Try parsing as datetime first (for ISO format with time)
        dt = parse_datetime(value)
        if dt:
            return dt.date()
        # Try parsing as date
        return parse_date(value)
    return None


def _parse_datetime(value) -> datetime | None:
    """Parse a datetime value from various formats."""
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return parse_datetime(value)
    return None


class SQLCacheManager:
    """
    Manages SQL-based caching for analysis results.

    Three cache levels:
    - Raw visits: One row per visit, shared across configs
    - Computed visits: One row per visit per config
    - Computed FLWs: One row per FLW per config
    """

    def __init__(self, opportunity_id: int, config: AnalysisPipelineConfig | None = None):
        self.opportunity_id = opportunity_id
        self.config = config
        self.config_hash = get_config_hash(config) if config else None
        from django.conf import settings

        ttl_hours = getattr(settings, "PIPELINE_CACHE_TTL_HOURS", DEFAULT_TTL_HOURS)
        self.ttl = timedelta(hours=ttl_hours)

    def _get_expires_at(self):
        return timezone.now() + self.ttl

    # -------------------------------------------------------------------------
    # Raw Visit Cache
    # -------------------------------------------------------------------------

    def has_valid_raw_cache(self, expected_visit_count: int, tolerance_pct: int = 100) -> bool:
        """Check if we have valid raw visit cache.

        Args:
            expected_visit_count: The live visit count from the opportunity.
            tolerance_pct: Accept cache if it has >= this % of expected visits.
                           100 = strict (default), 95 = accept if >=95% of visits cached.
        """
        min_count = int(expected_visit_count * tolerance_pct / 100) if tolerance_pct < 100 else expected_visit_count
        return RawVisitCache.objects.filter(
            opportunity_id=self.opportunity_id,
            visit_count__gte=min_count,
            expires_at__gt=timezone.now(),
        ).exists()

    def get_raw_visit_count(self) -> int:
        """Get count of cached raw visits (excludes in-progress sentinel rows)."""
        return RawVisitCache.objects.filter(
            opportunity_id=self.opportunity_id,
            visit_count__gt=0,
            expires_at__gt=timezone.now(),
        ).count()

    def store_raw_visits(self, visit_dicts: list[dict], visit_count: int):
        """
        Store raw visit data to SQL cache.

        Args:
            visit_dicts: List of visit dicts from CSV parsing
            visit_count: Total visit count for invalidation
        """
        expires_at = self._get_expires_at()

        # Build rows first (outside transaction for speed)
        rows = []
        for v in visit_dicts:
            rows.append(
                RawVisitCache(
                    opportunity_id=self.opportunity_id,
                    visit_count=visit_count,
                    expires_at=expires_at,
                    visit_id=v.get("id", 0),
                    username=v.get("username") or "",
                    deliver_unit=v.get("deliver_unit") or "",
                    deliver_unit_id=v.get("deliver_unit_id"),
                    entity_id=v.get("entity_id") or "",
                    entity_name=v.get("entity_name") or "",
                    visit_date=_parse_date(v.get("visit_date")),
                    status=v.get("status") or "",
                    reason=v.get("reason") or "",
                    location=v.get("location") or "",
                    flagged=v.get("flagged") or False,
                    flag_reason=v.get("flag_reason") or {},
                    form_json=v.get("form_json") or {},
                    completed_work=v.get("completed_work") or {},
                    status_modified_date=_parse_datetime(v.get("status_modified_date")),
                    review_status=v.get("review_status") or "",
                    review_created_on=_parse_datetime(v.get("review_created_on")),
                    justification=v.get("justification") or "",
                    date_created=_parse_datetime(v.get("date_created")),
                    completed_work_id=v.get("completed_work_id"),
                    images=v.get("images") or [],
                )
            )

        # DELETE and INSERT in same transaction to prevent race condition
        # where concurrent requests could both insert, causing duplicates
        with transaction.atomic():
            RawVisitCache.objects.filter(opportunity_id=self.opportunity_id).delete()
            RawVisitCache.objects.bulk_create(rows, batch_size=1000)

        logger.info(f"[SQLCache] Stored {len(rows)} raw visits for opp {self.opportunity_id}")

    def store_raw_visits_start(self, visit_count: int):
        """
        Delete existing raw cache and prepare for batched inserts.

        Must be called before store_raw_visits_batch(). Rows are inserted
        with a unique negative visit_count (sentinel) so they're invisible
        to readers until store_raw_visits_finalize() is called.

        Each writer gets a unique negative sentinel to prevent cross-contamination
        when concurrent writers insert rows for the same opportunity.

        Args:
            visit_count: Estimated count (used for logging only, not stored)
        """
        # Unique negative sentinel per writer — prevents finalize cross-contamination
        self._pending_visit_count = -random.randint(1, 2**31 - 1)
        self._pending_expires_at = self._get_expires_at()
        # Don't delete here — old rows remain visible to readers until finalize().
        # This prevents Writer B's start() from wiping Writer A's in-progress batches.
        logger.info(
            f"[SQLCache] Preparing raw cache for opp {self.opportunity_id}, "
            f"~{visit_count} visits (sentinel={self._pending_visit_count})"
        )

    def store_raw_visits_batch(self, visit_dicts: list[dict]) -> int:
        """
        Insert a batch of raw visits. Call store_raw_visits_start() first.

        Returns:
            Number of rows inserted
        """
        rows = []
        for v in visit_dicts:
            rows.append(
                RawVisitCache(
                    opportunity_id=self.opportunity_id,
                    visit_count=self._pending_visit_count,
                    expires_at=self._pending_expires_at,
                    visit_id=v.get("id", 0),
                    username=v.get("username") or "",
                    deliver_unit=v.get("deliver_unit") or "",
                    deliver_unit_id=v.get("deliver_unit_id"),
                    entity_id=v.get("entity_id") or "",
                    entity_name=v.get("entity_name") or "",
                    visit_date=_parse_date(v.get("visit_date")),
                    status=v.get("status") or "",
                    reason=v.get("reason") or "",
                    location=v.get("location") or "",
                    flagged=v.get("flagged") or False,
                    flag_reason=v.get("flag_reason") or {},
                    form_json=v.get("form_json") or {},
                    completed_work=v.get("completed_work") or {},
                    status_modified_date=_parse_datetime(v.get("status_modified_date")),
                    review_status=v.get("review_status") or "",
                    review_created_on=_parse_datetime(v.get("review_created_on")),
                    justification=v.get("justification") or "",
                    date_created=_parse_datetime(v.get("date_created")),
                    completed_work_id=v.get("completed_work_id"),
                    images=v.get("images") or [],
                )
            )
        RawVisitCache.objects.bulk_create(rows, batch_size=1000)
        return len(rows)

    def store_raw_visits_finalize(self, actual_count: int):
        """
        Atomically make batched rows visible by setting the real visit_count.

        Must be called after all store_raw_visits_batch() calls. In a single
        transaction: removes any other writer's rows (old finalized or other
        in-progress sentinel), then promotes THIS writer's sentinel rows to
        the actual parsed count — making them visible to has_valid_raw_cache().
        """
        with transaction.atomic():
            # Remove rows that don't belong to this writer (old cache + other writers)
            RawVisitCache.objects.filter(
                opportunity_id=self.opportunity_id,
            ).exclude(
                visit_count=self._pending_visit_count,
            ).delete()
            # Make this writer's rows visible
            updated = RawVisitCache.objects.filter(
                opportunity_id=self.opportunity_id,
                visit_count=self._pending_visit_count,
            ).update(visit_count=actual_count)
        logger.info(
            f"[SQLCache] Finalized {updated} raw visits for opp {self.opportunity_id} " f"(visit_count={actual_count})"
        )

    def get_raw_visits_queryset(self):
        """Get queryset of cached raw visits (excludes in-progress sentinel rows)."""
        return RawVisitCache.objects.filter(
            opportunity_id=self.opportunity_id,
            visit_count__gt=0,
            expires_at__gt=timezone.now(),
        )

    # -------------------------------------------------------------------------
    # Computed Visit Cache
    # -------------------------------------------------------------------------

    def has_valid_computed_visit_cache(self, expected_visit_count: int, tolerance_pct: int = 100) -> bool:
        """Check if we have valid computed visit cache for this config."""
        if not self.config_hash:
            return False
        min_count = int(expected_visit_count * tolerance_pct / 100) if tolerance_pct < 100 else expected_visit_count
        return ComputedVisitCache.objects.filter(
            opportunity_id=self.opportunity_id,
            config_hash=self.config_hash,
            visit_count__gte=min_count,
            expires_at__gt=timezone.now(),
        ).exists()

    def store_computed_visits(self, visits_data: list[dict], visit_count: int):
        """
        Store computed visit results.

        Args:
            visits_data: List of dicts with visit_id, username, base fields, and computed_fields
            visit_count: Total visit count for invalidation
        """
        if not self.config_hash:
            return

        expires_at = self._get_expires_at()

        # Build rows first (outside transaction for speed)
        rows = [
            ComputedVisitCache(
                opportunity_id=self.opportunity_id,
                config_hash=self.config_hash,
                visit_count=visit_count,
                expires_at=expires_at,
                visit_id=v["visit_id"],
                username=v["username"],
                # Base fields (allow None/NULL for missing values)
                visit_date=_parse_date(v.get("visit_date")),
                status=v.get("status") or None,
                flagged=v.get("flagged", False),
                location=v.get("location") or None,
                deliver_unit=v.get("deliver_unit") or None,
                deliver_unit_id=v.get("deliver_unit_id"),
                entity_id=v.get("entity_id") or None,
                entity_name=v.get("entity_name") or None,
                # Computed fields
                computed_fields=v["computed_fields"],
            )
            for v in visits_data
        ]

        # DELETE and INSERT in same transaction to prevent race condition
        # where concurrent requests could both insert, causing duplicates
        with transaction.atomic():
            ComputedVisitCache.objects.filter(
                opportunity_id=self.opportunity_id,
                config_hash=self.config_hash,
            ).delete()
            ComputedVisitCache.objects.bulk_create(rows, batch_size=1000)

        logger.info(f"[SQLCache] Stored {len(rows)} computed visits for opp {self.opportunity_id}")

    def get_computed_visits_queryset(self):
        """Get queryset of computed visits for this config."""
        if not self.config_hash:
            return ComputedVisitCache.objects.none()
        return ComputedVisitCache.objects.filter(
            opportunity_id=self.opportunity_id,
            config_hash=self.config_hash,
            expires_at__gt=timezone.now(),
        )

    # -------------------------------------------------------------------------
    # Computed FLW Cache
    # -------------------------------------------------------------------------

    def has_valid_flw_cache(self, expected_visit_count: int, tolerance_pct: int = 100) -> bool:
        """Check if we have valid FLW cache for this config."""
        if not self.config_hash:
            return False
        min_count = int(expected_visit_count * tolerance_pct / 100) if tolerance_pct < 100 else expected_visit_count
        return ComputedFLWCache.objects.filter(
            opportunity_id=self.opportunity_id,
            config_hash=self.config_hash,
            visit_count__gte=min_count,
            expires_at__gt=timezone.now(),
        ).exists()

    def store_flw_results(self, flw_data: list[dict], visit_count: int):
        """
        Store aggregated FLW results.

        Args:
            flw_data: List of dicts with FLW aggregated data
            visit_count: Total visit count for invalidation
        """
        if not self.config_hash:
            return

        expires_at = self._get_expires_at()

        # Build rows first (outside transaction for speed)
        rows = [
            ComputedFLWCache(
                opportunity_id=self.opportunity_id,
                config_hash=self.config_hash,
                visit_count=visit_count,
                expires_at=expires_at,
                username=f["username"],
                aggregated_fields=f.get("aggregated_fields", {}),
                total_visits=f.get("total_visits", 0),
                approved_visits=f.get("approved_visits", 0),
                pending_visits=f.get("pending_visits", 0),
                rejected_visits=f.get("rejected_visits", 0),
                flagged_visits=f.get("flagged_visits", 0),
                first_visit_date=_parse_date(f.get("first_visit_date")),
                last_visit_date=_parse_date(f.get("last_visit_date")),
            )
            for f in flw_data
        ]

        # DELETE and INSERT in same transaction to prevent race condition
        # where concurrent requests could both insert, causing duplicates
        with transaction.atomic():
            ComputedFLWCache.objects.filter(
                opportunity_id=self.opportunity_id,
                config_hash=self.config_hash,
            ).delete()
            ComputedFLWCache.objects.bulk_create(rows, batch_size=1000)

        logger.info(f"[SQLCache] Stored {len(rows)} FLW results for opp {self.opportunity_id}")

    def get_flw_results_queryset(self):
        """Get queryset of FLW results for this config."""
        if not self.config_hash:
            return ComputedFLWCache.objects.none()
        return ComputedFLWCache.objects.filter(
            opportunity_id=self.opportunity_id,
            config_hash=self.config_hash,
            expires_at__gt=timezone.now(),
        )

    # -------------------------------------------------------------------------
    # Cache Invalidation
    # -------------------------------------------------------------------------

    def invalidate_all(self):
        """Invalidate all cache for this opportunity."""
        RawVisitCache.objects.filter(opportunity_id=self.opportunity_id).delete()
        ComputedVisitCache.objects.filter(opportunity_id=self.opportunity_id).delete()
        ComputedFLWCache.objects.filter(opportunity_id=self.opportunity_id).delete()
        logger.info(f"[SQLCache] Invalidated all cache for opp {self.opportunity_id}")

    def delete_config(self):
        """
        Delete all cache for this opportunity and config.

        Deletes computed visit and FLW cache for current config_hash.
        Does not delete raw cache as it's shared across configs.
        """
        if not self.config_hash:
            logger.warning("[SQLCache] Cannot delete config cache without config_hash")
            return {"computed_visit": 0, "computed_flw": 0}

        visit_deleted, _ = ComputedVisitCache.objects.filter(
            opportunity_id=self.opportunity_id,
            config_hash=self.config_hash,
        ).delete()

        flw_deleted, _ = ComputedFLWCache.objects.filter(
            opportunity_id=self.opportunity_id,
            config_hash=self.config_hash,
        ).delete()

        logger.info(
            f"[SQLCache] Deleted config cache for opp {self.opportunity_id}, "
            f"config {self.config_hash}: {visit_deleted} visits, {flw_deleted} FLWs"
        )

        return {"computed_visit": visit_deleted, "computed_flw": flw_deleted}

    # -------------------------------------------------------------------------
    # Cache Management (Class Methods)
    # -------------------------------------------------------------------------

    @classmethod
    def delete_all_cache(cls, opportunity_id: int) -> dict[str, int]:
        """
        Delete all cache for an opportunity (all cache types, all configs).

        Args:
            opportunity_id: Opportunity to delete cache for

        Returns:
            Dict with deletion counts for each cache type
        """
        raw_deleted = RawVisitCache.invalidate_opportunity(opportunity_id)

        visit_deleted, _ = ComputedVisitCache.objects.filter(opportunity_id=opportunity_id).delete()

        flw_deleted, _ = ComputedFLWCache.objects.filter(opportunity_id=opportunity_id).delete()

        logger.info(
            f"[SQLCache] Deleted all cache for opp {opportunity_id}: "
            f"{raw_deleted} raw, {visit_deleted} computed visits, {flw_deleted} FLWs"
        )

        return {
            "raw": raw_deleted,
            "computed_visit": visit_deleted,
            "computed_flw": flw_deleted,
        }

    @classmethod
    def delete_config_cache(cls, opportunity_id: int, config_hash: str) -> dict[str, int]:
        """
        Delete cache for a specific opportunity and config combination.

        Args:
            opportunity_id: Opportunity to delete cache for
            config_hash: Config hash to delete cache for

        Returns:
            Dict with deletion counts
        """
        visit_deleted = ComputedVisitCache.invalidate_opportunity_config(opportunity_id, config_hash)

        flw_deleted = ComputedFLWCache.invalidate_opportunity_config(opportunity_id, config_hash)

        logger.info(
            f"[SQLCache] Deleted config cache for opp {opportunity_id}, "
            f"config {config_hash}: {visit_deleted} visits, {flw_deleted} FLWs"
        )

        return {
            "computed_visit": visit_deleted,
            "computed_flw": flw_deleted,
        }

    @classmethod
    def get_cache_stats(cls, opportunity_id: int) -> dict[str, dict]:
        """
        Get comprehensive cache statistics for an opportunity.

        Args:
            opportunity_id: Opportunity to get stats for

        Returns:
            Dict with stats for each cache type
        """
        from django.db.models import Count

        # Raw cache stats
        raw_stats = RawVisitCache.objects.filter(opportunity_id=opportunity_id).aggregate(
            count=Count("id"),
            total_rows=Count("visit_id"),
        )

        # Computed visit cache stats
        computed_visit_stats = ComputedVisitCache.objects.filter(opportunity_id=opportunity_id).aggregate(
            count=Count("id"),
            total_rows=Count("visit_id"),
        )

        computed_visit_configs = list(
            ComputedVisitCache.objects.filter(opportunity_id=opportunity_id)
            .values_list("config_hash", flat=True)
            .distinct()
        )

        # Computed FLW cache stats
        computed_flw_stats = ComputedFLWCache.objects.filter(opportunity_id=opportunity_id).aggregate(
            count=Count("id"),
            total_rows=Count("username"),
        )

        computed_flw_configs = list(
            ComputedFLWCache.objects.filter(opportunity_id=opportunity_id)
            .values_list("config_hash", flat=True)
            .distinct()
        )

        return {
            "raw": {
                "count": raw_stats["count"] or 0,
                "total_rows": raw_stats["total_rows"] or 0,
                "configs": [],
            },
            "computed_visit": {
                "count": computed_visit_stats["count"] or 0,
                "total_rows": computed_visit_stats["total_rows"] or 0,
                "configs": computed_visit_configs,
            },
            "computed_flw": {
                "count": computed_flw_stats["count"] or 0,
                "total_rows": computed_flw_stats["total_rows"] or 0,
                "configs": computed_flw_configs,
            },
        }

    @classmethod
    def get_all_opportunities_with_cache(cls) -> list[int]:
        """
        Get list of all opportunity IDs that have any cache.

        Returns:
            List of unique opportunity IDs
        """
        # Get opportunity IDs from all three cache tables
        raw_opps = set(RawVisitCache.objects.values_list("opportunity_id", flat=True).distinct())
        visit_opps = set(ComputedVisitCache.objects.values_list("opportunity_id", flat=True).distinct())
        flw_opps = set(ComputedFLWCache.objects.values_list("opportunity_id", flat=True).distinct())

        # Combine and return sorted list
        all_opps = sorted(raw_opps | visit_opps | flw_opps)
        return all_opps

    @classmethod
    def get_configs_for_opportunity(cls, opportunity_id: int) -> list[str]:
        """
        Get list of config hashes for a specific opportunity.

        Args:
            opportunity_id: Opportunity to get configs for

        Returns:
            List of unique config hashes
        """
        # Get config hashes from computed caches
        visit_configs = set(
            ComputedVisitCache.objects.filter(opportunity_id=opportunity_id)
            .values_list("config_hash", flat=True)
            .distinct()
        )

        flw_configs = set(
            ComputedFLWCache.objects.filter(opportunity_id=opportunity_id)
            .values_list("config_hash", flat=True)
            .distinct()
        )

        # Combine and return sorted list
        all_configs = sorted(visit_configs | flw_configs)
        return all_configs

    @classmethod
    def get_cache_details(cls) -> list[dict]:
        """
        Get comprehensive details about all cache entries.

        Returns:
            List of dicts with cache entry details (without size - size calculation removed to avoid confusion)
        """
        from django.db.models import Count, Min

        details = []

        # Raw cache entries (group by opportunity)
        # Note: Use Min() for created_at since it's an aggregate function.
        # Coalesce is NOT an aggregate - it would add created_at to GROUP BY,
        # splitting rows by their individual timestamps.
        raw_entries = (
            RawVisitCache.objects.values("opportunity_id", "visit_count", "expires_at")
            .annotate(row_count=Count("id"), created_at_min=Min("created_at"))
            .order_by("opportunity_id")
        )

        for entry in raw_entries:
            details.append(
                {
                    "opportunity_id": entry["opportunity_id"],
                    "cache_type": "raw",
                    "config_hash": None,
                    "row_count": entry["row_count"],
                    "expires_at": entry["expires_at"],
                    "created_at": entry["created_at_min"],
                    "visit_count": entry["visit_count"],
                }
            )

        # Computed visit cache entries (group by opportunity + config)
        visit_entries = (
            ComputedVisitCache.objects.values("opportunity_id", "config_hash", "visit_count", "expires_at")
            .annotate(row_count=Count("id"), created_at_min=Min("created_at"))
            .order_by("opportunity_id", "config_hash")
        )

        for entry in visit_entries:
            details.append(
                {
                    "opportunity_id": entry["opportunity_id"],
                    "cache_type": "computed_visit",
                    "config_hash": entry["config_hash"],
                    "row_count": entry["row_count"],
                    "expires_at": entry["expires_at"],
                    "created_at": entry["created_at_min"],
                    "visit_count": entry["visit_count"],
                }
            )

        # Computed FLW cache entries (group by opportunity + config)
        flw_entries = (
            ComputedFLWCache.objects.values("opportunity_id", "config_hash", "visit_count", "expires_at")
            .annotate(row_count=Count("id"), created_at_min=Min("created_at"))
            .order_by("opportunity_id", "config_hash")
        )

        for entry in flw_entries:
            details.append(
                {
                    "opportunity_id": entry["opportunity_id"],
                    "cache_type": "computed_flw",
                    "config_hash": entry["config_hash"],
                    "row_count": entry["row_count"],
                    "expires_at": entry["expires_at"],
                    "created_at": entry["created_at_min"],
                    "visit_count": entry["visit_count"],
                }
            )

        return details

    # -------------------------------------------------------------------------
    # Visit Filtering (for Audit)
    # -------------------------------------------------------------------------

    def filter_visits(
        self,
        usernames: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        last_n_per_user: int | None = None,
        last_n_total: int | None = None,
        sample_percentage: int = 100,
    ):
        """
        Build a filtered queryset of visits using SQL.

        Returns a queryset that can be further processed or converted to values.
        Uses window functions for last_n_per_user (much faster than Python groupby).
        """
        from django.db.models import F, Window
        from django.db.models.functions import RowNumber

        # DEBUG: Log filter parameters
        logger.info(
            f"[SQLCache.filter_visits] last_n_total={last_n_total}, "
            f"last_n_per_user={last_n_per_user}, usernames={usernames is not None}, "
            f"sample_percentage={sample_percentage}"
        )

        # Start with base queryset (valid cache)
        qs = self.get_raw_visits_queryset()

        # Filter by usernames
        if usernames:
            qs = qs.filter(username__in=usernames)

        # Filter by date range
        if start_date:
            qs = qs.filter(visit_date__gte=start_date)
        if end_date:
            qs = qs.filter(visit_date__lte=end_date)

        # Apply last_n_per_user using window function
        if last_n_per_user:
            # Add row number partitioned by username, ordered by visit_date desc
            qs = qs.annotate(
                row_num=Window(
                    expression=RowNumber(),
                    partition_by=[F("username")],
                    order_by=F("visit_date").desc(),
                )
            ).filter(row_num__lte=last_n_per_user)

        # Apply last_n_total
        if last_n_total:
            logger.info(f"[SQLCache.filter_visits] Applying last_n_total={last_n_total} LIMIT")
            qs = qs.order_by("-visit_date")[:last_n_total]

        # Apply sampling (using random ordering)
        # Note: For large datasets, TABLESAMPLE would be better but isn't portable
        if sample_percentage < 100:
            # Get count first, then limit
            total_count = qs.count()
            sample_size = max(1, int(total_count * sample_percentage / 100))
            # Order by random and take sample_size
            qs = qs.order_by("?")[:sample_size]

        return qs

    def get_filtered_visit_ids(
        self,
        usernames: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        last_n_per_user: int | None = None,
        last_n_total: int | None = None,
        sample_percentage: int = 100,
    ) -> list[int]:
        """Get filtered visit IDs using SQL. Returns only IDs (very fast)."""
        qs = self.filter_visits(
            usernames=usernames,
            start_date=start_date,
            end_date=end_date,
            last_n_per_user=last_n_per_user,
            last_n_total=last_n_total,
            sample_percentage=sample_percentage,
        )
        return list(qs.values_list("visit_id", flat=True))

    def get_filtered_visits_slim(
        self,
        usernames: list[str] | None = None,
        start_date: date | None = None,
        end_date: date | None = None,
        last_n_per_user: int | None = None,
        last_n_total: int | None = None,
        sample_percentage: int = 100,
    ) -> list[dict]:
        """
        Get filtered visits WITHOUT form_json (slim mode).

        Returns visit dicts suitable for preview display.
        """
        qs = self.filter_visits(
            usernames=usernames,
            start_date=start_date,
            end_date=end_date,
            last_n_per_user=last_n_per_user,
            last_n_total=last_n_total,
            sample_percentage=sample_percentage,
        )

        # Defer heavy columns
        qs = qs.defer("form_json", "completed_work", "flag_reason")

        visits = []
        for row in qs.iterator():
            visits.append(
                {
                    "id": row.visit_id,
                    "opportunity_id": row.opportunity_id,
                    "username": row.username,
                    "deliver_unit": row.deliver_unit,
                    "deliver_unit_id": row.deliver_unit_id,
                    "entity_id": row.entity_id,
                    "entity_name": row.entity_name,
                    "visit_date": row.visit_date.isoformat() if row.visit_date else None,
                    "status": row.status,
                    "reason": row.reason,
                    "location": row.location,
                    "flagged": row.flagged,
                    "review_status": row.review_status,
                    "images": row.images,
                }
            )

        logger.info(f"[SQLCache] Filtered to {len(visits)} visits (slim)")
        return visits
