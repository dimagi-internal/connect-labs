"""
SQL backend implementation.

Uses PostgreSQL tables for caching AND computation.
All analysis is done via SQL queries, not Python/pandas.
"""

import inspect
import json
import logging
import pathlib
from collections.abc import Generator
from datetime import date, datetime
from decimal import Decimal
from typing import Any

import sentry_sdk
from django.http import HttpRequest
from django.utils.dateparse import parse_date

from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.query_builder import (
    execute_entity_aggregation,
    execute_flw_aggregation,
    execute_visit_extraction,
)
from connect_labs.labs.analysis.config import VISIT_PASSTHROUGH_COLUMNS, AnalysisPipelineConfig, CacheStage
from connect_labs.labs.analysis.models import (
    EntityAnalysisResult,
    EntityRow,
    FLWAnalysisResult,
    FLWRow,
    VisitAnalysisResult,
    VisitRow,
)

logger = logging.getLogger(__name__)

# A fresh raw-visit fetch that comes back below this % of what's already
# cached is treated as suspicious rather than trusted outright -- see
# SQLBackend.fetch_raw_visits / stream_raw_visits. Catches transient
# short-reads (observed cause: Connect's export endpoint returning a
# partial result, e.g. read-replica lag during a burst of new visit
# writes) before they silently overwrite a good cache with a bad one.
RAW_CACHE_SHRINK_THRESHOLD_PCT = 80
# Total fetch attempts (not retries on top of an initial try) before giving
# up and keeping the previous cache.
RAW_CACHE_MAX_ATTEMPTS = 3
# How long to extend the old cache's TTL, and how long to keep surfacing the
# anomaly on cache-HIT reads (SQLCacheManager.extend_raw_cache_ttl /
# set_pending_raw_fetch_anomaly), once the guard falls back to it. These two
# windows must stay aligned -- if the anomaly flag's TTL were ever shorter
# than the cache extension's, the banner would silently disappear before the
# underlying data got a real chance to refresh, reintroducing a milder
# version of the exact bug this guard exists to prevent.
RAW_CACHE_ANOMALY_TTL_MINUTES = 10


def _with_passthrough_columns(row: dict, computed: dict) -> dict:
    """Fold the base columns `VisitRow` has no attribute for into `computed`.

    `flag_reason`, `date_created` and `review_status` are selected per visit but
    have nowhere to live on the row object, and `ComputedVisitCache` denormalizes
    only eight base columns — so anything not in `computed_fields` is dropped the
    moment a result is served from cache. Riding in `computed` is what makes them
    identical on the fresh and cached paths without a migration (#1198).

    `flag_reason` is JSONB and a raw cursor hands it back as JSON *text*, while
    the same value read from `computed_fields` (a JSONField) comes back decoded.
    Decoding here is what stops the fresh and cached paths handing render code
    two different types for one field — the ace#1657 shape all over again.

    A config field cannot collide here: every one of these names is in
    `RAW_VISIT_BASE_COLUMNS`, and `AnalysisPipelineConfig.__post_init__` already
    raises on a field that shadows a base column.
    """
    for col in VISIT_PASSTHROUGH_COLUMNS:
        value = row.get(col)
        if col == "flag_reason" and isinstance(value, str):
            try:
                value = json.loads(value)
            except (TypeError, ValueError):
                pass  # not JSON after all — hand it over as-is rather than dropping it
        computed[col] = value
    return computed


def _flw_data_to_rows(config: AnalysisPipelineConfig, flw_data: list[dict]) -> list[FLWRow]:
    """Map raw FLW-aggregation dicts (from `execute_flw_aggregation`) to FLWRow
    objects, attaching config/histogram custom fields.

    Extracted so the live compute path and the period-scoped snapshot read
    (`get_period_scoped_flw_result`, ace#764) produce byte-identical row shapes
    — the only difference between them is the date window baked into the SQL.
    """
    flw_rows: list[FLWRow] = []
    for row in flw_data:
        # Standard fields
        # Note: use _base_ prefix for date fields to avoid conflicts with custom config fields
        flw_row = FLWRow(
            username=row["username"],
            total_visits=row.get("total_visits", 0),
            approved_visits=row.get("approved_visits", 0),
            pending_visits=row.get("pending_visits", 0),
            rejected_visits=row.get("rejected_visits", 0),
            flagged_visits=row.get("flagged_visits", 0),
            first_visit_date=row.get("_base_first_visit_date"),
            last_visit_date=row.get("_base_last_visit_date"),
        )

        # Custom fields (from config fields + histograms). days_active is
        # surfaced here (rather than as a FLWRow attribute) so it flows
        # transparently through the cache JSON and out to pipeline-output
        # consumers (workflow render code) alongside any user-defined fields.
        custom = {}
        custom["days_active"] = row.get("_base_days_active") or 0
        for field in config.fields:
            if field.name in row:
                custom[field.name] = row[field.name]

        # Add histogram fields
        for hist in config.histograms:
            bin_width = (hist.upper_bound - hist.lower_bound) / hist.num_bins
            for i in range(hist.num_bins):
                bin_lower = hist.lower_bound + (i * bin_width)
                bin_upper = bin_lower + bin_width
                lower_str = str(bin_lower).replace(".", "_")
                upper_str = str(bin_upper).replace(".", "_")
                bin_name = f"{hist.bin_name_prefix}_{lower_str}_{upper_str}_visits"
                if bin_name in row:
                    custom[bin_name] = row[bin_name] or 0

            # Add summary stats (convert Decimal to float for JSON compatibility)
            if f"{hist.name}_mean" in row:
                mean_val = row[f"{hist.name}_mean"]
                if isinstance(mean_val, Decimal):
                    mean_val = float(mean_val)
                custom[f"{hist.name}_mean"] = mean_val
            if f"{hist.name}_count" in row:
                custom[f"{hist.name}_count"] = row[f"{hist.name}_count"]

        flw_row.custom_fields = custom
        flw_rows.append(flw_row)

    return flw_rows


def _model_to_visit_dict(row, skip_form_json=False) -> dict:
    """Convert RawVisitCache model instance to visit dict."""
    return {
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
        "flag_reason": row.flag_reason,
        "form_json": {} if skip_form_json else row.form_json,
        "completed_work": row.completed_work,
        "status_modified_date": row.status_modified_date.isoformat() if row.status_modified_date else None,
        "review_status": row.review_status,
        "review_created_on": row.review_created_on.isoformat() if row.review_created_on else None,
        "justification": row.justification,
        "date_created": row.date_created.isoformat() if row.date_created else None,
        "completed_work_id": row.completed_work_id,
        "images": row.images,
        "user_id": row.user_id or None,
        "user_visit_id": row.user_visit_id or None,
    }


def _build_visit_dict(row: dict) -> dict:
    """Build a visit context dict from a raw SQL row for transform/extractor post-processing."""
    form_json = row.get("form_json", {})
    if isinstance(form_json, str):
        try:
            form_json = json.loads(form_json) if form_json else {}
        except (ValueError, json.JSONDecodeError):
            form_json = {}
    images = row.get("images", [])
    if isinstance(images, str):
        try:
            images = json.loads(images) if images else []
        except (ValueError, json.JSONDecodeError):
            images = []
    return {
        "form_json": form_json,
        "images": images,
        "username": row.get("username"),
        "visit_date": row.get("visit_date"),
        "entity_name": row.get("entity_name"),
    }


class SQLBackend:
    """
    SQL backend for analysis.

    Uses PostgreSQL for both storage AND computation:
    - Raw visits stored in SQL tables
    - Field extraction via JSONB operators
    - Aggregation via GROUP BY queries
    """

    # -------------------------------------------------------------------------
    # Raw Data Layer
    # -------------------------------------------------------------------------

    def fetch_raw_visits(
        self,
        opportunity_id: int,
        access_token: str,
        expected_visit_count: int | None = None,
        force_refresh: bool = False,
        skip_form_json: bool = False,
        filter_visit_ids: set[int] | None = None,
        tolerance_pct: int = 100,
        include_images: bool = False,
        pipeline_id: int | None = None,
        user=None,
        accept_low_count: bool = False,
    ) -> list[dict]:
        """
        Fetch raw visit data from SQL cache or API.

        SQL backend stores visits in RawVisitCache table. If cache is valid,
        reads directly from PostgreSQL. Otherwise, fetches from API and stores.

        `pipeline_id` scopes the raw cache slot per #116 — must match the value
        the downstream extraction query filters on, or extraction returns 0
        rows because the raw rows are tagged with a different pipeline_id.

        `accept_low_count`: bypass the shrink guard below and trust whatever
        comes back, even if it's suspiciously smaller than what's cached.
        Set this when a human has explicitly asked to see fresh data anyway
        (see `self.last_raw_fetch_anomaly`).

        Sets `self.last_raw_fetch_anomaly` (a dict, or None) as a side
        effect — callers that care (see AnalysisPipeline._consume_raw_visits_stream)
        read it back right after calling.
        """
        self.last_raw_fetch_anomaly = None
        cache_manager = SQLCacheManager(opportunity_id, pipeline_id=pipeline_id)

        # Check if we have valid cached data in SQL.
        # When expected_visit_count is unknown (0/None from Celery MockRequest), accept any
        # non-expired cache rather than always re-downloading from the API.
        if not force_refresh:
            effective_count = expected_visit_count or 0
            if cache_manager.has_valid_raw_cache(effective_count, tolerance_pct=tolerance_pct):
                # If images requested, verify cache actually has image data.
                # The initial pipeline run fetches without ?images=true, so cached
                # visits may have empty images arrays. In that case, fall through
                # to re-fetch from API with images included.
                if include_images:
                    qs = cache_manager.get_raw_visits_queryset()
                    if filter_visit_ids is not None:
                        qs = qs.filter(visit_id__in=filter_visit_ids)
                    has_images = qs.exclude(images=[]).exists()
                    if not has_images:
                        logger.info(f"[SQL] Cache has no images for opp {opportunity_id}, re-fetching with images")
                    else:
                        logger.info(f"[SQL] Raw cache HIT (with images) for opp {opportunity_id}")
                        self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                        return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)
                else:
                    logger.info(f"[SQL] Raw cache HIT for opp {opportunity_id} (tolerance={tolerance_pct}%)")
                    self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                    return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)

        # Cache miss or force refresh - fetch from API
        logger.info(f"[SQL] Raw cache MISS for opp {opportunity_id}, fetching from API")

        # Guard against a fetch that comes back suspiciously smaller than what's
        # already cached (see RAW_CACHE_SHRINK_THRESHOLD_PCT) -- retry a couple
        # times before trusting it. `prior_count` is 0 for a first-ever fetch,
        # which always skips the guard (nothing to compare against yet). Uses
        # the TTL-ignoring count: we got here because the cache is a "miss",
        # which for the common case (natural TTL expiry, not force_refresh)
        # means the rows we're about to replace are already expired -- the
        # TTL-filtered count would read 0 and defeat the whole guard.
        prior_count = cache_manager.get_raw_visit_count_ignoring_ttl()
        threshold = prior_count * RAW_CACHE_SHRINK_THRESHOLD_PCT / 100
        visit_dicts: list[dict] = []
        for attempt in range(1, RAW_CACHE_MAX_ATTEMPTS + 1):
            visit_dicts = self._fetch_from_api(opportunity_id, access_token, include_images=include_images, user=user)
            if prior_count == 0 or accept_low_count or len(visit_dicts) >= threshold:
                break
            logger.warning(
                f"[SQL] Raw fetch for opp {opportunity_id} pipeline {pipeline_id} returned "
                f"{len(visit_dicts)} rows, below {threshold:.0f} ({RAW_CACHE_SHRINK_THRESHOLD_PCT}% of "
                f"previously-cached {prior_count}) on attempt {attempt}/{RAW_CACHE_MAX_ATTEMPTS}"
            )

        if prior_count > 0 and not accept_low_count and len(visit_dicts) < threshold:
            logger.error(
                f"[SQL] Raw fetch for opp {opportunity_id} pipeline {pipeline_id} stayed low "
                f"({len(visit_dicts)} vs previously {prior_count}) after {RAW_CACHE_MAX_ATTEMPTS} attempts "
                "-- keeping previous cache and flagging the anomaly instead of overwriting it"
            )
            sentry_sdk.capture_message(
                f"Raw visit fetch anomaly: opp {opportunity_id} pipeline {pipeline_id} "
                f"got {len(visit_dicts)} rows vs {prior_count} previously cached",
                level="warning",
            )
            cache_manager.extend_raw_cache_ttl(minutes=RAW_CACHE_ANOMALY_TTL_MINUTES)
            self.last_raw_fetch_anomaly = {
                "previous_count": prior_count,
                "attempted_count": len(visit_dicts),
                "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
            }
            # Keep surfacing this on later cache-HIT reads too -- otherwise the
            # extend_raw_cache_ttl() call above makes the old rows look like an
            # ordinary valid cache again, and the very next request (a reload,
            # a different tab) would silently drop the flag. See
            # get_pending_raw_fetch_anomaly's docstring.
            cache_manager.set_pending_raw_fetch_anomaly(
                self.last_raw_fetch_anomaly, minutes=RAW_CACHE_ANOMALY_TTL_MINUTES
            )
            low_fetch_dicts = visit_dicts
            visit_dicts = self._load_from_cache(cache_manager, skip_form_json=False, filter_visit_ids=None)
            if not visit_dicts:
                # The old cache we were protecting vanished from under us
                # (e.g. a concurrent invalidation raced this guard) -- serving
                # nothing would be worse than serving the low-but-real fetch
                # we already have. The anomaly flag above still applies.
                visit_dicts = low_fetch_dicts
        else:
            # Store full data to SQL cache
            visit_count = len(visit_dicts)
            cache_manager.store_raw_visits(visit_dicts, visit_count)
            cache_manager.clear_pending_raw_fetch_anomaly()
            logger.info(f"[SQL] Stored {visit_count} visits to RawVisitCache")

        # Apply filters for return value
        # Normalize to strings for comparison — visit_id is CharField in cache
        # but record_to_visit_dict returns int IDs, and callers may pass either type.
        #
        # `is not None`, NOT truthiness: an EMPTY set means "none of them", and
        # treating it as "no filter" returns the whole opportunity. See
        # _load_from_cache for the measured cost of that confusion.
        if filter_visit_ids is not None:
            str_filter = {str(vid) for vid in filter_visit_ids}
            visit_dicts = [v for v in visit_dicts if str(v.get("id")) in str_filter]

        if skip_form_json:
            for v in visit_dicts:
                v["form_json"] = {}

        return visit_dicts

    def stream_raw_visits(
        self,
        opportunity_id: int,
        access_token: str,
        expected_visit_count: int | None = None,
        force_refresh: bool = False,
        tolerance_pct: int = 100,
        pipeline_id: int | None = None,
        user=None,
        accept_low_count: bool = False,
    ) -> Generator[tuple[str, Any], None, None]:
        """
        Stream raw visit data with progress events using v2 paginated JSON.

        Behavior:
        - Cache HIT: yield ("cached", slim_dicts) and return.
        - Cache MISS: paginate the v2 export endpoint, write each page to the
          SQL cache, strip form_json, accumulate slim dicts. Yield
          ("progress", rows_so_far, expected_visit_count) after each page,
          then ("complete", slim_dicts) at the end.
        - A completed fetch that comes back suspiciously smaller than what's
          already cached (see RAW_CACHE_SHRINK_THRESHOLD_PCT) is discarded and
          retried up to RAW_CACHE_MAX_ATTEMPTS times. If it's still low after
          that, the new (never-finalized) rows are dropped, the OLD cache is
          kept and its TTL pushed out a little, and the anomaly is surfaced
          via `self.last_raw_fetch_anomaly` for the caller to display —
          yielded back as a "cached" event so the rest of the pipeline
          behaves exactly as a normal cache hit.

        Memory note: each page is bounded at DEFAULT_PAGE_SIZE records,
        so we never need a temp file like the v1 streaming CSV path did.
        """
        from connect_labs.labs.analysis.backends.visit_record import record_to_visit_dict
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError
        from connect_labs.labs.integrations.connect.factory import get_export_client

        self.last_raw_fetch_anomaly = None
        cache_manager = SQLCacheManager(opportunity_id, pipeline_id=pipeline_id)

        # Check SQL cache first
        if not force_refresh:
            effective_count = expected_visit_count or 0
            if cache_manager.has_valid_raw_cache(effective_count, tolerance_pct=tolerance_pct):
                logger.info(f"[SQL] Raw cache HIT for opp {opportunity_id}")
                self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                visit_dicts = self._load_from_cache(cache_manager, skip_form_json=True, filter_visit_ids=None)
                yield ("cached", visit_dicts)
                return

        logger.info(f"[SQL] Raw cache MISS for opp {opportunity_id}, paginating export API")

        endpoint = f"/export/opportunity/{opportunity_id}/user_visits/"
        # See the matching comment in fetch_raw_visits: must ignore TTL, since
        # reaching this "miss" branch on the common (non-force_refresh) path
        # means the existing rows are already expired.
        prior_count = cache_manager.get_raw_visit_count_ignoring_ttl()
        threshold = prior_count * RAW_CACHE_SHRINK_THRESHOLD_PCT / 100

        for attempt in range(1, RAW_CACHE_MAX_ATTEMPTS + 1):
            # Prepare cache for batched inserts. estimated_count is just a hint
            # for the cache; finalize() will set the real count below. Each
            # attempt gets its own sentinel (store_raw_visits_start), so a
            # retry never touches the previous attempt's (already-aborted)
            # rows or the still-valid old cache.
            cache_manager.store_raw_visits_start(expected_visit_count or 0)

            slim_dicts: list[dict] = []
            rows_so_far = 0

            try:
                with get_export_client(
                    opportunity_id=opportunity_id,
                    access_token=access_token,
                    timeout=180.0,
                    user=user,
                ) as client:
                    for page in client.paginate(endpoint):
                        # Convert v2 records to visit dicts (with form_json)
                        batch = [record_to_visit_dict(record, opportunity_id) for record in page]
                        if not batch:
                            continue

                        # Store the full batch (with form_json) to the SQL cache
                        cache_manager.store_raw_visits_batch(batch)

                        # Strip form_json from in-memory dicts to save memory; the
                        # SQL extraction step reads form_json from the DB.
                        for v in batch:
                            v["form_json"] = {}
                        slim_dicts.extend(batch)
                        rows_so_far += len(batch)

                        yield ("progress", rows_so_far, expected_visit_count or 0)

            except ExportAPIError as e:
                cache_manager.store_raw_visits_abort()
                logger.error(f"[SQL] Export API failure for opp {opportunity_id}: {e}")
                sentry_sdk.capture_exception(e)
                raise RuntimeError(f"Connect export API error: {e}") from e

            if prior_count == 0 or accept_low_count or rows_so_far >= threshold:
                # Atomically finalize cache with the real count
                cache_manager.store_raw_visits_finalize(rows_so_far)
                cache_manager.clear_pending_raw_fetch_anomaly()
                logger.info(f"[SQL] Streamed {rows_so_far} visits to DB, keeping {len(slim_dicts)} slim dicts")
                yield ("complete", slim_dicts)
                return

            logger.warning(
                f"[SQL] Raw stream for opp {opportunity_id} pipeline {pipeline_id} returned {rows_so_far} "
                f"rows, below {threshold:.0f} ({RAW_CACHE_SHRINK_THRESHOLD_PCT}% of previously-cached "
                f"{prior_count}) on attempt {attempt}/{RAW_CACHE_MAX_ATTEMPTS} -- discarding and retrying"
            )
            cache_manager.store_raw_visits_abort()

        # Exhausted every attempt and it's still low: never finalized over the
        # old cache, so it's untouched. Keep serving it, push its TTL out a
        # little so we re-check sooner than the full TTL, and flag the anomaly.
        logger.error(
            f"[SQL] Raw stream for opp {opportunity_id} pipeline {pipeline_id} stayed low after "
            f"{RAW_CACHE_MAX_ATTEMPTS} attempts -- keeping previous cache and flagging the anomaly"
        )
        sentry_sdk.capture_message(
            f"Raw visit fetch anomaly: opp {opportunity_id} pipeline {pipeline_id} stayed low "
            f"({rows_so_far} rows) vs {prior_count} previously cached after {RAW_CACHE_MAX_ATTEMPTS} attempts",
            level="warning",
        )
        cache_manager.extend_raw_cache_ttl(minutes=RAW_CACHE_ANOMALY_TTL_MINUTES)
        self.last_raw_fetch_anomaly = {
            "previous_count": prior_count,
            "attempted_count": rows_so_far,
            "threshold_pct": RAW_CACHE_SHRINK_THRESHOLD_PCT,
        }
        # Keep surfacing this on later cache-HIT reads too -- see
        # get_pending_raw_fetch_anomaly's docstring for why extend_raw_cache_ttl
        # alone isn't enough.
        cache_manager.set_pending_raw_fetch_anomaly(
            self.last_raw_fetch_anomaly, minutes=RAW_CACHE_ANOMALY_TTL_MINUTES
        )
        old_visit_dicts = self._load_from_cache(cache_manager, skip_form_json=True, filter_visit_ids=None)
        if not old_visit_dicts:
            # The old cache we were protecting vanished from under us (e.g. a
            # concurrent invalidation raced this guard) -- serving nothing
            # would be worse than serving the low-but-real data we already
            # streamed. The anomaly flag above still applies.
            old_visit_dicts = slim_dicts
        yield ("cached", old_visit_dicts)

    def has_valid_raw_cache(
        self,
        opportunity_id: int,
        expected_visit_count: int,
        tolerance_pct: int = 100,
        pipeline_id: int | None = None,
    ) -> bool:
        """Check if valid raw cache exists in SQL."""
        cache_manager = SQLCacheManager(opportunity_id, pipeline_id=pipeline_id)
        return cache_manager.has_valid_raw_cache(expected_visit_count, tolerance_pct=tolerance_pct)

    def _load_from_cache(
        self,
        cache_manager: SQLCacheManager,
        skip_form_json: bool,
        filter_visit_ids: set[int] | None,
    ) -> list[dict]:
        """Load visits from RawVisitCache table."""
        qs = cache_manager.get_raw_visits_queryset()

        # `is not None`, NOT truthiness. An EMPTY set means "none of these
        # visits"; truthiness collapses it into `None`, which means "no filter
        # at all" -- so a caller asking for ZERO visits was handed back EVERY
        # visit in the opportunity.
        #
        # It fails as a slow success, never as an error. The two callers that
        # re-filter in Python afterwards (get_visit_data, get_visits_batch)
        # still return the right answer, having materialised the entire
        # opportunity to do it -- which is why nothing ever surfaced. Measured
        # 2026-08-26 on one 5h15m audit job: 264 of these, ~23,272 rows and
        # ~8-10 seconds each, roughly 4.9 million rows and ~37 minutes spent
        # answering "give me nothing".
        #
        # fetch_visits_for_ids does NOT re-filter, so there the empty set was
        # also a correctness bug: ask for no visits, receive all of them.
        if filter_visit_ids is not None:
            qs = qs.filter(visit_id__in=filter_visit_ids)

        if skip_form_json:
            # Exclude form_json from query for efficiency
            qs = qs.defer("form_json")

        visits = []
        for row in qs.iterator():
            visits.append(_model_to_visit_dict(row, skip_form_json=skip_form_json))

        # Name the CALLER, not just the row count. Investigating why one job
        # re-loaded the same 23,272-row set 81 times (2026-08-29) cost an
        # afternoon precisely because 264 of these lines were byte-identical
        # and none of them said who asked. The frame two above _load_from_cache
        # is the application call site (this is only ever reached through
        # fetch_raw_visits).
        try:
            caller = inspect.stack()[2]
            origin = f" via {pathlib.Path(caller.filename).name}:{caller.lineno}"
        except Exception:  # pragma: no cover - diagnostics must never break a fetch
            origin = ""
        logger.info(
            f"[SQL] Loaded {len(visits)} visits from RawVisitCache"
            f" (filtered={filter_visit_ids is not None}, slim={skip_form_json}){origin}"
        )
        return visits

    def _fetch_from_api(
        self,
        opportunity_id: int,
        access_token: str,
        include_images: bool = False,
        user=None,
    ) -> list[dict]:
        """Fetch all user visits from Connect v2 export API as a list of visit dicts.

        Memory note: each page is bounded at DEFAULT_PAGE_SIZE records.
        Total memory peaks at the full visit count, same as the previous CSV path,
        but without the additional pandas DataFrame copy.
        """
        from connect_labs.labs.analysis.backends.visit_record import record_to_visit_dict
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError
        from connect_labs.labs.integrations.connect.factory import get_export_client

        endpoint = f"/export/opportunity/{opportunity_id}/user_visits/"
        params = {"images": "true"} if include_images else None

        try:
            with get_export_client(
                opportunity_id=opportunity_id,
                access_token=access_token,
                timeout=180.0,
                user=user,
            ) as client:
                visits: list[dict] = []
                for page in client.paginate(endpoint, params=params):
                    visits.extend(record_to_visit_dict(record, opportunity_id) for record in page)
                return visits
        except ExportAPIError as e:
            logger.error(f"[SQL] Export API failure for opp {opportunity_id}: {e}")
            sentry_sdk.capture_exception(e)
            raise RuntimeError(f"Connect export API error: {e}") from e

    # -------------------------------------------------------------------------
    # Analysis Results Layer
    # -------------------------------------------------------------------------

    def get_cached_flw_result(
        self,
        opportunity_id: int,
        config: AnalysisPipelineConfig,
        visit_count: int,
        tolerance_pct: int = 100,
    ) -> FLWAnalysisResult | None:
        """Get cached FLW result if valid."""
        cache_manager = SQLCacheManager(opportunity_id, config)

        if not cache_manager.has_valid_flw_cache(visit_count, tolerance_pct=tolerance_pct):
            return None

        logger.info(f"[SQL] FLW cache HIT for opp {opportunity_id}")

        # Load FLW results from SQL cache
        flw_qs = cache_manager.get_flw_results_queryset()
        flw_rows = []
        for row in flw_qs:
            flw_row = FLWRow(
                username=row.username,
                total_visits=row.total_visits,
                approved_visits=row.approved_visits,
                pending_visits=row.pending_visits,
                rejected_visits=row.rejected_visits,
                flagged_visits=row.flagged_visits,
                first_visit_date=row.first_visit_date,
                last_visit_date=row.last_visit_date,
            )
            flw_row.custom_fields = row.aggregated_fields
            flw_rows.append(flw_row)

        return FLWAnalysisResult(
            opportunity_id=opportunity_id,
            rows=flw_rows,
            metadata={"total_visits": visit_count, "from_sql_cache": True},
        )

    def get_period_scoped_flw_result(
        self,
        opportunity_id: int,
        config: AnalysisPipelineConfig,
    ) -> FLWAnalysisResult | None:
        """Re-aggregate the EXISTING raw-visit cache to FLW level, restricted to
        the window in `config.date_from`/`date_to` (ace#764).

        Unlike `get_cached_flw_result`, which returns the pre-aggregated all-time
        FLW cache, this re-runs the FLW GROUP BY over the per-pipeline raw visit
        cache with a `visit_date` predicate. That cache already holds every
        visit (the window is excluded from the config hash), so this is a single
        bounded SQL aggregation — no download, no recompute, and nothing is
        written back to any cache. It is the read path saved-run snapshots use
        to freeze a period-scoped slice instead of the whole-program total.

        Returns None when the raw visit cache is empty for this pipeline slot
        (caller treats that as a cache miss — "load the pipeline first"). An
        empty window with a populated cache legitimately returns a result with
        zero rows, so emptiness of the *result* is never treated as a miss.
        """
        cache_manager = SQLCacheManager(opportunity_id, config)
        if cache_manager.get_raw_visit_count() == 0:
            logger.info(
                "[SQL] Period-scoped FLW read: no raw visit cache for opp %s pipeline %s — miss",
                opportunity_id,
                config.pipeline_id,
            )
            return None

        flw_data = execute_flw_aggregation(config, opportunity_id)
        flw_rows = _flw_data_to_rows(config, flw_data)
        total_visits = sum(r.total_visits for r in flw_rows)
        return FLWAnalysisResult(
            opportunity_id=opportunity_id,
            rows=flw_rows,
            metadata={
                "total_visits": total_visits,
                "total_flws": len(flw_rows),
                "from_sql_cache": True,
                "period_scoped": True,
                "date_from": config.date_from,
                "date_to": config.date_to,
            },
        )

    def get_cached_visit_result(
        self,
        opportunity_id: int,
        config: AnalysisPipelineConfig,
        visit_count: int,
        tolerance_pct: int = 100,
    ) -> VisitAnalysisResult | None:
        """Get cached visit result if valid, applying filters at query time."""
        cache_manager = SQLCacheManager(opportunity_id, config)

        if not cache_manager.has_valid_computed_visit_cache(visit_count, tolerance_pct=tolerance_pct):
            return None

        logger.info(f"[SQL] Visit cache HIT for opp {opportunity_id}")

        # Load computed visits (no join needed - all fields are in ComputedVisitCache now)
        computed_qs = cache_manager.get_computed_visits_queryset()

        # Apply filters at query time (OPTIMIZATION: filters not in cache hash)
        if config.filters:
            for key, value in config.filters.items():
                # entity_id is a column, filter directly
                if key == "entity_id":
                    computed_qs = computed_qs.filter(entity_id=value)
                    logger.info(f"[SQL] Applying entity_id filter: {value}")
                # status is a column on ComputedVisitCache, not in computed_fields JSONB
                elif key == "status":
                    if isinstance(value, list):
                        computed_qs = computed_qs.filter(status__in=value)
                    else:
                        computed_qs = computed_qs.filter(status=value)
                    logger.info(f"[SQL] Applying status filter: {value}")
                # All other filters are treated as computed field filters
                # This enables linking by fields like beneficiary_case_id, rutf_case_id, etc.
                else:
                    # Use Django's JSONB contains lookup for exact match
                    computed_qs = computed_qs.filter(computed_fields__contains={key: value})
                    logger.info(f"[SQL] Applying computed field filter: {key}={value}")

        # Build VisitRow objects directly from ComputedVisitCache
        visit_rows = []
        for cached_row in computed_qs:
            # Parse GPS from location string (format: "lat lon alt accuracy")
            latitude, longitude, accuracy = None, None, None
            if cached_row.location:
                parts = cached_row.location.split()
                if len(parts) >= 2:
                    try:
                        latitude = float(parts[0])
                        longitude = float(parts[1])
                        if len(parts) >= 4:
                            accuracy = float(parts[3])
                    except (ValueError, IndexError):
                        pass

            visit_row = VisitRow(
                id=str(cached_row.visit_id),
                user_id=None,
                username=cached_row.username,
                visit_date=datetime.combine(cached_row.visit_date, datetime.min.time())
                if cached_row.visit_date
                else None,
                status=cached_row.status,
                flagged=cached_row.flagged,
                latitude=latitude,
                longitude=longitude,
                accuracy_in_m=accuracy,
                deliver_unit_id=cached_row.deliver_unit_id,
                deliver_unit_name=cached_row.deliver_unit,
                entity_id=cached_row.entity_id,
                entity_name=cached_row.entity_name,
                computed=cached_row.computed_fields,
            )
            visit_rows.append(visit_row)

        # Build field metadata from config
        field_metadata = [{"name": f.name, "description": f.description} for f in config.fields]

        return VisitAnalysisResult(
            opportunity_id=opportunity_id,
            rows=visit_rows,
            metadata={"total_visits": len(visit_rows), "from_sql_cache": True},
            field_metadata=field_metadata,
        )

    def get_cached_entity_result(
        self,
        opportunity_id: int,
        config: AnalysisPipelineConfig,
        visit_count: int,
        tolerance_pct: int = 100,
    ) -> EntityAnalysisResult | None:
        """Get cached entity-stage result if valid."""
        cache_manager = SQLCacheManager(opportunity_id, config)

        if not cache_manager.has_valid_entity_cache(visit_count, tolerance_pct=tolerance_pct):
            return None

        logger.info(f"[SQL] Entity cache HIT for opp {opportunity_id}")

        entity_qs = cache_manager.get_entity_results_queryset()
        entity_rows = []
        for row in entity_qs:
            entity_row = EntityRow(
                entity_id=row.entity_id,
                entity_name=row.entity_name,
                username=row.username,
                total_visits=row.total_visits,
                first_visit_date=row.first_visit_date,
                last_visit_date=row.last_visit_date,
            )
            entity_row.custom_fields = row.aggregated_fields
            entity_rows.append(entity_row)

        return EntityAnalysisResult(
            opportunity_id=opportunity_id,
            rows=entity_rows,
            metadata={"total_visits": visit_count, "from_sql_cache": True},
        )

    def process_and_cache(
        self,
        request: HttpRequest,
        config: AnalysisPipelineConfig,
        opportunity_id: int,
        visit_dicts: list[dict],
        skip_raw_store: bool = False,
    ) -> FLWAnalysisResult | VisitAnalysisResult | EntityAnalysisResult:
        """
        Process visits using SQL and cache results.

        For VISIT_LEVEL:
        1. Store raw visits in SQL (unless skip_raw_store=True)
        2. Execute visit extraction query (no aggregation)
        3. Cache computed visits and return VisitAnalysisResult

        For AGGREGATED:
        1. Store raw visits in SQL (unless skip_raw_store=True)
        2. Execute FLW aggregation query
        3. Cache and return FLWAnalysisResult

        For ENTITY:
        1. Store raw visits in SQL (unless skip_raw_store=True)
        2. Execute entity aggregation query (GROUP BY config.linking_field)
        3. Cache and return EntityAnalysisResult

        Args:
            skip_raw_store: If True, skip storing raw visits (already stored
                during streaming parse or already in cache from a cache hit).
                visit_dicts are only used for len() when this is True.
        """
        cache_manager = SQLCacheManager(opportunity_id, config)
        visit_count = len(visit_dicts)

        # Step 1: Store raw visits to SQL (skip if already stored during streaming)
        if not skip_raw_store:
            logger.info(f"[SQL] Storing {visit_count} raw visits to SQL")
            cache_manager.store_raw_visits(visit_dicts, visit_count)
        else:
            logger.info(f"[SQL] Skipping raw store ({visit_count} visits already in DB)")

        # Branch based on terminal stage
        if config.terminal_stage == CacheStage.VISIT_LEVEL:
            return self._process_visit_level(config, opportunity_id, visit_count, cache_manager)
        elif config.terminal_stage == CacheStage.ENTITY:
            return self._process_entity_level(config, opportunity_id, visit_count, cache_manager)
        else:
            return self._process_flw_level(config, opportunity_id, visit_count, cache_manager)

    def _process_visit_level(
        self,
        config: AnalysisPipelineConfig,
        opportunity_id: int,
        visit_count: int,
        cache_manager: SQLCacheManager,
    ) -> VisitAnalysisResult:
        """Process and cache visit-level analysis (no aggregation)."""
        logger.info("[SQL] Executing visit extraction query")
        visit_data, computed_field_names = execute_visit_extraction(config, opportunity_id)

        # Build VisitRow objects
        visit_rows = []
        for row in visit_data:
            # Parse GPS from location string (format: "lat lon alt accuracy")
            latitude, longitude, accuracy = None, None, None
            location = row.get("location") or ""
            if location:
                parts = location.split()
                if len(parts) >= 2:
                    try:
                        latitude = float(parts[0])
                        longitude = float(parts[1])
                        if len(parts) >= 4:
                            accuracy = float(parts[3])
                    except (ValueError, IndexError):
                        pass

            # Separate computed fields from base fields
            computed = _with_passthrough_columns(row, {name: row.get(name) for name in computed_field_names})

            # Apply post-processing transforms that need full visit context
            # (e.g., extract_images_with_question_ids needs both form_json and images)
            # Build visit_dict once per row (lazy); transforms must not mutate it.
            visit_dict = None
            for field in config.fields:
                if field.name not in computed_field_names:
                    continue

                if field.transform and callable(field.transform):
                    # Check if this transform needs full visit data (has form_json/images params)
                    import inspect

                    sig = inspect.signature(field.transform)
                    params = list(sig.parameters.keys())

                    # If transform takes 'visit_data' param, it needs full context
                    if "visit_data" in params or len(params) == 0:
                        try:
                            if visit_dict is None:
                                visit_dict = _build_visit_dict(row)
                            computed[field.name] = field.transform(visit_dict)
                        except Exception as e:
                            logger.warning(f"Transform for {field.name} failed: {e}")
                            computed[field.name] = None

                elif field.extractor and callable(field.extractor):
                    try:
                        if visit_dict is None:
                            visit_dict = _build_visit_dict(row)
                        computed[field.name] = field.extractor(visit_dict)
                    except Exception as e:
                        logger.warning(f"Extractor for {field.name} failed: {e}")
                        computed[field.name] = None

            # Parse visit_date
            visit_date_val = row.get("visit_date")
            if visit_date_val and isinstance(visit_date_val, date):
                visit_date_val = datetime.combine(visit_date_val, datetime.min.time())

            visit_row = VisitRow(
                id=str(row.get("visit_id", "")),
                user_id=None,
                username=row.get("username", ""),
                visit_date=visit_date_val,
                status=row.get("status", ""),
                flagged=row.get("flagged", False),
                latitude=latitude,
                longitude=longitude,
                accuracy_in_m=accuracy,
                deliver_unit_id=row.get("deliver_unit_id"),
                deliver_unit_name=row.get("deliver_unit", ""),
                entity_id=row.get("entity_id", ""),
                entity_name=row.get("entity_name", ""),
                computed=computed,
            )
            visit_rows.append(visit_row)

        # Cache computed visits (store base fields as columns to avoid joins later)
        computed_cache_data = [
            {
                "visit_id": row.id,
                "username": row.username,
                # Handle both date and datetime objects
                "visit_date": row.visit_date.date()
                if row.visit_date and hasattr(row.visit_date, "date") and callable(row.visit_date.date)
                else row.visit_date,
                "status": row.status,
                "flagged": row.flagged,
                "location": row.location
                if hasattr(row, "location")
                else (f"{row.latitude} {row.longitude}" if row.latitude and row.longitude else ""),
                "deliver_unit": row.deliver_unit_name,
                "deliver_unit_id": row.deliver_unit_id,
                "entity_id": row.entity_id,
                "entity_name": row.entity_name,
                "computed_fields": row.computed,
            }
            for row in visit_rows
        ]
        cache_manager.store_computed_visits(computed_cache_data, visit_count)

        # Build field metadata from config
        field_metadata = [{"name": f.name, "description": f.description} for f in config.fields]

        visit_result = VisitAnalysisResult(
            opportunity_id=opportunity_id,
            rows=visit_rows,
            metadata={
                "total_visits": len(visit_rows),
                "computed_via": "sql",
            },
            field_metadata=field_metadata,
        )

        logger.info(f"[SQL] Processed {len(visit_rows)} visits with {len(computed_field_names)} computed fields")
        return visit_result

    def _process_flw_level(
        self,
        config: AnalysisPipelineConfig,
        opportunity_id: int,
        visit_count: int,
        cache_manager: SQLCacheManager,
    ) -> FLWAnalysisResult:
        """
        Process and cache FLW-level aggregation.

        Like Python/Redis backend, we ALWAYS cache visit-level first,
        then aggregate to FLW. This allows visit-level cache to be
        reused by coverage map and other visit-level consumers.
        """
        # Step 1: Extract and cache visit-level data first (for cache sharing)
        logger.info("[SQL] Step 1: Extracting visit-level data for cache")
        visit_data, computed_field_names = execute_visit_extraction(config, opportunity_id)

        # Cache computed visits (so coverage can reuse this)
        computed_cache_data = [
            {
                "visit_id": v.get("visit_id", 0),
                "username": v.get("username", ""),
                "computed_fields": _with_passthrough_columns(v, {name: v.get(name) for name in computed_field_names}),
            }
            for v in visit_data
        ]
        cache_manager.store_computed_visits(computed_cache_data, visit_count)
        logger.info(f"[SQL] Cached {len(computed_cache_data)} visit-level rows")

        # Step 2: Execute FLW aggregation query
        logger.info("[SQL] Step 2: Executing FLW aggregation query")
        flw_data = execute_flw_aggregation(config, opportunity_id)

        # Convert to FLWRow objects
        flw_rows = _flw_data_to_rows(config, flw_data)
        total_visits = sum(r.total_visits for r in flw_rows)

        # Build result
        flw_result = FLWAnalysisResult(
            opportunity_id=opportunity_id,
            rows=flw_rows,
            metadata={
                "total_visits": total_visits,
                "total_flws": len(flw_rows),
                "computed_via": "sql",
            },
        )

        # Cache FLW results
        flw_cache_data = [
            {
                "username": row.username,
                "aggregated_fields": row.custom_fields,
                "total_visits": row.total_visits,
                "approved_visits": row.approved_visits,
                "pending_visits": row.pending_visits,
                "rejected_visits": row.rejected_visits,
                "flagged_visits": row.flagged_visits,
                "first_visit_date": row.first_visit_date,
                "last_visit_date": row.last_visit_date,
            }
            for row in flw_rows
        ]
        cache_manager.store_flw_results(flw_cache_data, total_visits)

        logger.info(f"[SQL] Processed {len(flw_rows)} FLWs, {total_visits} visits (via SQL)")
        return flw_result

    def _process_entity_level(
        self,
        config: AnalysisPipelineConfig,
        opportunity_id: int,
        visit_count: int,
        cache_manager: SQLCacheManager,
    ) -> EntityAnalysisResult:
        """
        Process and cache entity-level aggregation.

        Like FLW-level, we extract and cache visit-level data first so the
        visit cache can be reused. Then run the entity-stage GROUP BY query
        on top of raw visits and cache the per-entity rows.
        """
        # Step 1: Extract and cache visit-level data first (for cache sharing)
        logger.info("[SQL] Step 1 (entity): Extracting visit-level data for cache")
        visit_data, computed_field_names = execute_visit_extraction(config, opportunity_id)

        computed_cache_data = [
            {
                "visit_id": v.get("visit_id", 0),
                "username": v.get("username", ""),
                "computed_fields": _with_passthrough_columns(v, {name: v.get(name) for name in computed_field_names}),
            }
            for v in visit_data
        ]
        cache_manager.store_computed_visits(computed_cache_data, visit_count)
        logger.info(f"[SQL] Cached {len(computed_cache_data)} visit-level rows")

        # Step 2: Execute entity aggregation query
        logger.info("[SQL] Step 2 (entity): Executing entity aggregation query")
        entity_data = execute_entity_aggregation(config, opportunity_id)

        # Convert to EntityRow objects
        entity_rows = []
        total_visits = 0

        for row in entity_data:
            entity_row = EntityRow(
                entity_id=str(row.get("entity_id") or ""),
                entity_name=row.get("entity_name") or "",
                username=row.get("username") or "",
                total_visits=row.get("total_visits", 0),
                first_visit_date=row.get("_base_first_visit_date"),
                last_visit_date=row.get("_base_last_visit_date"),
            )

            # Custom fields (from config fields + histograms). Note: entity stage
            # may also surface a column named entity_id/entity_name/username from
            # the SELECT — those are already on the EntityRow's standard slots, so
            # we don't replicate them into custom_fields.
            standard_keys = {
                "entity_id",
                "entity_name",
                "username",
                "total_visits",
                "_base_first_visit_date",
                "_base_last_visit_date",
            }
            custom = {}
            for field in config.fields:
                if field.name in row and field.name not in standard_keys:
                    custom[field.name] = row[field.name]

            # Add histogram fields
            for hist in config.histograms:
                bin_width = (hist.upper_bound - hist.lower_bound) / hist.num_bins
                for i in range(hist.num_bins):
                    bin_lower = hist.lower_bound + (i * bin_width)
                    bin_upper = bin_lower + bin_width
                    lower_str = str(bin_lower).replace(".", "_")
                    upper_str = str(bin_upper).replace(".", "_")
                    bin_name = f"{hist.bin_name_prefix}_{lower_str}_{upper_str}_visits"
                    if bin_name in row:
                        custom[bin_name] = row[bin_name] or 0

                if f"{hist.name}_mean" in row:
                    mean_val = row[f"{hist.name}_mean"]
                    if isinstance(mean_val, Decimal):
                        mean_val = float(mean_val)
                    custom[f"{hist.name}_mean"] = mean_val
                if f"{hist.name}_count" in row:
                    custom[f"{hist.name}_count"] = row[f"{hist.name}_count"]

            entity_row.custom_fields = custom

            entity_rows.append(entity_row)
            total_visits += entity_row.total_visits

        entity_result = EntityAnalysisResult(
            opportunity_id=opportunity_id,
            rows=entity_rows,
            metadata={
                "total_visits": total_visits,
                "total_entities": len(entity_rows),
                "computed_via": "sql",
            },
        )

        # Cache entity results
        entity_cache_data = [
            {
                "entity_id": row.entity_id,
                "entity_name": row.entity_name,
                "username": row.username,
                "aggregated_fields": row.custom_fields,
                "total_visits": row.total_visits,
                "first_visit_date": row.first_visit_date,
                "last_visit_date": row.last_visit_date,
            }
            for row in entity_rows
        ]
        cache_manager.store_entity_results(entity_cache_data, total_visits)

        logger.info(f"[SQL] Processed {len(entity_rows)} entities, {total_visits} visits (via SQL)")
        return entity_result

    # -------------------------------------------------------------------------
    # Visit Filtering (for Audit) - SQL-optimized
    # -------------------------------------------------------------------------

    def filter_visits_for_audit(
        self,
        opportunity_id: int,
        access_token: str,
        expected_visit_count: int | None,
        usernames: list[str] | None = None,
        start_date: str | None = None,
        end_date: str | None = None,
        last_n_per_user: int | None = None,
        last_n_total: int | None = None,
        sample_percentage: int = 100,
        deliver_unit_types: list[str] | None = None,
        visit_statuses: list[str] | None = None,
        days_of_week: list[int] | None = None,
        return_visit_data: bool = False,
    ) -> list[int] | tuple[list[int], list[dict]]:
        """
        Filter visits using SQL queries (much faster than Python/pandas).

        Pushes all filtering into PostgreSQL using indexes and window functions.
        """
        cache_manager = SQLCacheManager(opportunity_id, config=None)

        # Ensure cache is populated
        if not cache_manager.has_valid_raw_cache(expected_visit_count or 0):
            logger.info(f"[SQL] Cache miss during filter, populating for opp {opportunity_id}")
            self.fetch_raw_visits(opportunity_id, access_token, expected_visit_count)

        # Parse date strings to date objects
        start_date_obj: date | None = None
        end_date_obj: date | None = None
        if start_date:
            start_date_obj = parse_date(start_date)
        if end_date:
            end_date_obj = parse_date(end_date)

        # DEBUG: Log incoming filter parameters
        logger.info(
            f"[SQL] filter_visits_for_audit called with: last_n_total={last_n_total}, "
            f"last_n_per_user={last_n_per_user}, usernames={usernames}, "
            f"start_date={start_date_obj}, end_date={end_date_obj}, sample_pct={sample_percentage}"
        )

        if return_visit_data:
            # Get both IDs and slim visit data in one query
            visits = cache_manager.get_filtered_visits_slim(
                usernames=usernames,
                start_date=start_date_obj,
                end_date=end_date_obj,
                last_n_per_user=last_n_per_user,
                last_n_total=last_n_total,
                sample_percentage=sample_percentage,
                deliver_unit_types=deliver_unit_types,
                visit_statuses=visit_statuses,
                days_of_week=days_of_week,
            )
            visit_ids = [v["id"] for v in visits]
            logger.info(f"[SQL] Filtered to {len(visit_ids)} visits (with data)")
            return visit_ids, visits
        else:
            # Get only IDs (fastest path)
            visit_ids = cache_manager.get_filtered_visit_ids(
                usernames=usernames,
                start_date=start_date_obj,
                end_date=end_date_obj,
                last_n_per_user=last_n_per_user,
                last_n_total=last_n_total,
                sample_percentage=sample_percentage,
                deliver_unit_types=deliver_unit_types,
                visit_statuses=visit_statuses,
                days_of_week=days_of_week,
            )
            logger.info(f"[SQL] Filtered to {len(visit_ids)} visit IDs")
            return visit_ids

    def get_deliver_unit_types_for_opportunity(
        self,
        opportunity_id: int,
        access_token: str,
        expected_visit_count: int | None,
    ) -> list[str]:
        """Get distinct deliver unit types (form.@name) seen in an opportunity's cached raw visits."""
        cache_manager = SQLCacheManager(opportunity_id, config=None)

        if not cache_manager.has_valid_raw_cache(expected_visit_count or 0):
            logger.info(f"[SQL] Cache miss during deliver-unit-type lookup, populating for opp {opportunity_id}")
            self.fetch_raw_visits(opportunity_id, access_token, expected_visit_count)

        return cache_manager.get_distinct_deliver_unit_types()
