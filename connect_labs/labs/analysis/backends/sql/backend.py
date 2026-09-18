"""
SQL backend implementation.

Uses PostgreSQL tables for caching AND computation.
All analysis is done via SQL queries, not Python/pandas.
"""

import inspect
import json
import logging
import pathlib
import time
from collections.abc import Generator
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

import sentry_sdk
from django.http import HttpRequest
from django.utils import timezone
from django.utils.dateparse import parse_date

from connect_labs.labs.analysis.backends.sql.cache import SQLCacheManager
from connect_labs.labs.analysis.backends.sql.query_builder import (
    execute_entity_aggregation,
    execute_flw_aggregation,
    execute_visit_extraction,
)
from connect_labs.labs.analysis.backends.sql.single_flight import claim_raw_rebuild
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
# How long to lend the existing rows when ANOTHER connection is already
# rebuilding this slot (see single_flight.py). Deliberately short: it only has
# to outlast the peer's walk -- measured at 10-20s, worst case the 180s export
# client timeout -- and every minute past that is a minute of staleness bought
# for nothing. Unlike RAW_CACHE_ANOMALY_TTL_MINUTES this window carries no
# anomaly banner, because nothing is wrong: the data is one rebuild behind, the
# rebuild is happening right now, and the next reader gets the fresh copy.
RAW_CACHE_PEER_REBUILD_TTL_MINUTES = 5
# What a FORCED refresh (?refresh=1) accepts as already fresh (#1926). A forced
# read is satisfied by a copy of the export fetched at or after the forcing request
# first asked (``force_refresh_since``), OR within this many minutes of now,
# whichever reaches further back.
#
# The request half is what makes one forced page load cost one walk per
# opportunity: the runner forwards ?refresh=1 to every pipeline in the stream, and
# they run one after another over the same shared slot (#1921), so the first
# pipeline walks and every later one finds rows fetched after the request began.
# It has to be the request and not a window alone: the stream behind #1926 reached
# its second pipeline's pass over opp 2154 nine minutes after its first, which no
# short window covers.
#
# The window half covers separate requests of one intent -- EventSource's
# automatic reconnect replays the same ?refresh=1 URL, and so does a double click
# or a second tab -- and callers with no request at all. It is sized like
# RAW_CACHE_PEER_REBUILD_TTL_MINUTES, for the same reason: data one walk old,
# fetched moments ago, is what the person asking for fresh data would have got.
FORCED_REFRESH_REUSE_MINUTES = 5
# How long a forced read waits for a PEER's walk of the same slot to finish before
# walking itself. Unforced losers are lent the existing rows instead (see
# single_flight.py), but a forced reader was explicitly promised fresh data, so the
# only way it can coalesce is to wait for the rows the peer is fetching. Bounded,
# and it fails open into a walk of its own: a stampede is a cost failure, an
# endless wait would be a correctness one. The ceiling covers the largest walk
# measured in #1926 (~56k visits, ~2.5 min) with room to spare.
FORCED_REFRESH_PEER_WAIT_SECONDS = 300
FORCED_REFRESH_PEER_POLL_SECONDS = 2
# Above this many missing visits, top up the cache incrementally instead of
# repaginating the whole export (#1361). Below it the delta's own overhead --
# an extra count query and a second finalize -- is not worth avoiding a small
# walk, and a full rebuild is the better-tested path.
#
# The ceiling is the important half. A huge gap means the cache is not really
# "a bit behind", it is wrong or ancient, and a top-up would paper over that
# with a long append nobody checked. It also bounds the memory this path can
# use, which is the whole reason the guard above exists.
RAW_CACHE_DELTA_MIN_ROWS = 1
RAW_CACHE_DELTA_MAX_ROWS = 5000


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
    """Convert RawVisitCache model instance to visit dict.

    ``xform_id`` is DERIVED here rather than stored, exactly as
    ``record_to_visit_dict`` derives it (``form_json["id"]``, cleared in slim mode
    because the form body is not loaded). RawVisitCache has no column for it.

    Before this it was simply absent from a cache-read dict, so the SAME visit came
    back with ``xform_id`` on a cache MISS and without the key at all on a cache
    HIT. ``audit.link_helpers`` builds the HQ form URL from it via ``.get()``, so a
    hit produced ``None`` and ``build_hq_form_url`` returned an empty string --
    the link silently vanished, and only on the reads that were fast. Deriving it
    makes the two paths agree, which matters more now that a miss also answers
    from the cache rather than from the API response it happened to be holding.
    """
    form_json = row.form_json if isinstance(row.form_json, dict) else {}
    return {
        "id": row.visit_id,
        "xform_id": None if skip_form_json else (form_json.get("id") or None),
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
        force_refresh_since: datetime | None = None,
    ) -> list[dict]:
        """
        Fetch raw visit data from SQL cache or API.

        SQL backend stores visits in RawVisitCache table. If cache is valid,
        reads directly from PostgreSQL. Otherwise, fetches from API and stores.

        `pipeline_id` is accepted for attribution but does NOT pick the slot: this
        walks the user_visits export, whose rows every visits pipeline on the
        opportunity shares (#1921), so the fill lands in the one slot their
        extraction queries read (``AnalysisPipelineConfig.raw_slot_id``).

        `accept_low_count`: bypass the shrink guard below and trust whatever
        comes back, even if it's suspiciously smaller than what's cached.
        Set this when a human has explicitly asked to see fresh data anyway
        (see `self.last_raw_fetch_anomaly`).

        `force_refresh` / `force_refresh_since`: see ``_stream_forced_raw_visits``.
        A forced read still reuses a copy fetched since the force began, so one
        forced page load walks each opportunity once, not once per pipeline (#1926).

        Sets `self.last_raw_fetch_anomaly` (a dict, or None) as a side
        effect — callers that care (see AnalysisPipeline._consume_raw_visits_stream)
        read it back right after calling.
        """
        self.last_raw_fetch_anomaly = None
        cache_manager = SQLCacheManager(opportunity_id, pipeline_id=pipeline_id)

        if force_refresh:
            for _event in self._stream_forced_raw_visits(
                opportunity_id,
                access_token,
                cache_manager,
                force_refresh_since=force_refresh_since,
                expected_visit_count=expected_visit_count,
                user=user,
                accept_low_count=accept_low_count,
                pipeline_id=pipeline_id,
                include_images=include_images,
            ):
                # No consumer for progress here; the rows are read back below.
                pass
            return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)

        # Check if we have valid cached data in SQL.
        # When expected_visit_count is unknown (0/None from Celery MockRequest), accept any
        # non-expired cache rather than always re-downloading from the API.
        effective_count = expected_visit_count or 0
        if cache_manager.has_valid_raw_cache(effective_count, tolerance_pct=tolerance_pct):
            # If images requested, verify cache actually has image data.
            # The initial pipeline run fetches without ?images=true, so cached
            # visits may have empty images arrays. In that case, fall through
            # to re-fetch from API with images included.
            if include_images:
                # Whether the SLOT was fetched with images -- not whether these
                # particular visits carry one. A case whose visits have no photo
                # is an ANSWER ("no photo"); reading it as "the cache cannot
                # answer" re-downloaded the whole opportunity on every open.
                if not cache_manager.slot_has_image_data():
                    logger.info(f"[SQL] Cache was fetched without images for opp {opportunity_id}, re-fetching")
                else:
                    logger.info(f"[SQL] Raw cache HIT (with images) for opp {opportunity_id}")
                    self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                    return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)
            else:
                logger.info(f"[SQL] Raw cache HIT for opp {opportunity_id} (tolerance={tolerance_pct}%)")
                self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)

        logger.info(f"[SQL] Raw cache MISS for opp {opportunity_id}, fetching from API")

        # One walk at a time per slot (#1361).
        #
        # include_images used to be excluded here as well, on the reasoning that
        # the lendable rows may be the image-less variant this caller already
        # rejected a few lines above. That reasoning is about LENDING, and it is
        # correct about lending — but being unable to lend was making it skip the
        # LOCK too, so image walks ran with no concurrency guard at all. They are
        # the most expensive walk there is (whole opportunity, form_json AND photo
        # payloads), which made the unguarded case the worst one. The lock is now
        # always taken; whether there is anything to lend is decided separately,
        # inside _lend_cache_during_peer_rebuild.
        with claim_raw_rebuild(opportunity_id, cache_manager.raw_slot_id) as is_leader:
            if not is_leader:
                lent = self._lend_cache_during_peer_rebuild(
                    cache_manager,
                    opportunity_id,
                    pipeline_id,
                    skip_form_json=skip_form_json,
                    require_images=include_images,
                )
                if lent is not None:
                    self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                    return lent
            # Still inside the `with`: the leader must hold the lock for the whole
            # walk, or the guard buys nothing. A top-up is a rebuild too, so it
            # runs under the same lock.
            if self._try_delta_refresh(
                cache_manager,
                opportunity_id,
                access_token,
                expected_visit_count=expected_visit_count,
                pipeline_id=pipeline_id,
                user=user,
            ):
                self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)
            return self._fetch_raw_visits_uncached(
                opportunity_id,
                access_token,
                cache_manager,
                include_images=include_images,
                user=user,
                accept_low_count=accept_low_count,
                pipeline_id=pipeline_id,
                filter_visit_ids=filter_visit_ids,
                skip_form_json=skip_form_json,
                expected_visit_count=expected_visit_count,
            )

    def _fetch_raw_visits_uncached(
        self,
        opportunity_id: int,
        access_token: str,
        cache_manager,
        *,
        include_images: bool,
        user,
        accept_low_count: bool,
        pipeline_id: int | None,
        filter_visit_ids,
        skip_form_json: bool,
        expected_visit_count: int | None = None,
    ) -> list[dict]:
        """Fill the slot with the BOUNDED writer, then read back only what was asked for.

        This used to paginate the whole export into one list and hand it to
        ``store_raw_visits``, so a request for a single visit by id materialised
        every visit in the opportunity — with ``form_json``, and with images. At
        ~35 KB a visit (``AuditDataAccess.fetch_visits_slim``: ~350 MB per 10k with
        ``form_json``, ~20 MB without) a ~30k-visit opportunity is **~1.05 GB
        resident per walk** on a 4096 MB task, which is what OOM-killed the web
        tier through September. ``_stream_raw_visits_uncached`` holds one page
        (``DEFAULT_PAGE_SIZE``), so the same walk is ~87 MB.

        The read side was always capable of this: ``_load_from_cache`` pushes both
        ``filter_visit_ids`` and ``skip_form_json`` into the query, and the old
        implementation already called it on its own anomaly branch. What was
        missing was only that the FILL and the READ were the same pass.

        The streaming writer carries the identical shrink-guard, retry and anomaly
        semantics — per-attempt sentinel, ``store_raw_visits_abort`` between
        attempts, ``last_raw_fetch_anomaly`` set on exhaustion — so nothing about
        the guard moves here. One corner does change, and deliberately: when a
        fetch stays low for all ``RAW_CACHE_MAX_ATTEMPTS`` **and** the old cache it
        was protecting has concurrently vanished, this now returns no rows where
        the list version returned the low-but-real fetch it happened to be holding.
        Keeping that would mean keeping the whole export resident for a
        doubly-exceptional case, and ``stream_raw_visits`` has behaved this way on
        the pipeline path since #1551.
        """
        for _event in self._stream_raw_visits_uncached(
            opportunity_id,
            access_token,
            cache_manager,
            expected_visit_count=expected_visit_count,
            user=user,
            accept_low_count=accept_low_count,
            pipeline_id=pipeline_id,
            include_images=include_images,
        ):
            # progress / complete / cached events have no consumer on this path;
            # the rows are read back out of Postgres below.
            pass

        return self._load_from_cache(cache_manager, skip_form_json, filter_visit_ids)

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
        force_refresh_since: datetime | None = None,
    ) -> Generator[tuple[str, Any]]:
        """
        Stream raw visit data with progress events using v2 paginated JSON.

        Behavior:
        - Cache HIT: yield ("cached", row_count) and return.
        - Cache MISS: paginate the v2 export endpoint, write each page to the
          SQL cache, and discard it. Yield
          ("progress", rows_so_far, expected_visit_count) after each page,
          then ("complete", row_count) at the end.

        **The "cached" and "complete" payloads are COUNTS, not rows.** They used
        to be the whole opportunity's visit dicts, and nothing ever read an
        element of them: the single consumer (AnalysisPipeline.
        _consume_raw_visits_stream) takes ``len()``, and ``process_and_cache``
        documents that ``visit_dicts are only used for len()`` once the rows are
        already stored — which they always are on this path. So each stream was
        accumulating, or re-SELECTing, an entire opportunity to compute one
        integer, and holding it across ``process_and_cache``'s multi-minute
        aggregation. On a 1-vCPU web task running 22-33 of these concurrently
        that is what exhausted memory. See dimagi-internal/connect-labs#1575.
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

        A forced read (``force_refresh``) skips the validity check but NOT the
        coalescing: see ``_stream_forced_raw_visits`` (#1926). It may also yield
        ("waiting", seconds) while a peer's walk of the same slot finishes.
        """
        self.last_raw_fetch_anomaly = None
        cache_manager = SQLCacheManager(opportunity_id, pipeline_id=pipeline_id)

        if force_refresh:
            yield from self._stream_forced_raw_visits(
                opportunity_id,
                access_token,
                cache_manager,
                force_refresh_since=force_refresh_since,
                expected_visit_count=expected_visit_count,
                user=user,
                accept_low_count=accept_low_count,
                pipeline_id=pipeline_id,
            )
            return

        # Check SQL cache first
        effective_count = expected_visit_count or 0
        if cache_manager.has_valid_raw_cache(effective_count, tolerance_pct=tolerance_pct):
            logger.info(f"[SQL] Raw cache HIT for opp {opportunity_id}")
            self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
            yield ("cached", cache_manager.get_raw_visit_count())
            return

        logger.info(f"[SQL] Raw cache MISS for opp {opportunity_id}, paginating export API")

        # One walk at a time per slot (#1361).
        with claim_raw_rebuild(opportunity_id, cache_manager.raw_slot_id) as is_leader:
            if not is_leader:
                lent = self._lend_cache_during_peer_rebuild(
                    cache_manager, opportunity_id, pipeline_id, skip_form_json=True, count_only=True
                )
                if lent is not None:
                    self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                    yield ("cached", lent)
                    return
            # A top-up is a rebuild too, so it runs under the same lock.
            if self._try_delta_refresh(
                cache_manager,
                opportunity_id,
                access_token,
                expected_visit_count=expected_visit_count,
                pipeline_id=pipeline_id,
                user=user,
            ):
                self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                yield ("cached", cache_manager.get_raw_visit_count())
                return
            yield from self._stream_raw_visits_uncached(
                opportunity_id,
                access_token,
                cache_manager,
                expected_visit_count=expected_visit_count,
                user=user,
                accept_low_count=accept_low_count,
                pipeline_id=pipeline_id,
            )

    def _stream_forced_raw_visits(
        self,
        opportunity_id: int,
        access_token: str,
        cache_manager,
        *,
        force_refresh_since: datetime | None,
        expected_visit_count: int | None,
        user,
        accept_low_count: bool,
        pipeline_id: int | None,
        include_images: bool = False,
    ) -> Generator[tuple[str, Any]]:
        """A forced read: walk the export unless it was already walked since the force began.

        ``force_refresh`` used to skip the validity check AND the rebuild lock, and
        the runner forwards ?refresh=1 to every pipeline stream on the page -- so the
        one shared user_visits slot per opportunity (#1921) saved nothing on a forced
        run, and each pipeline repaginated the whole export for itself. On 2026-09-18
        one forced open of workflow 20934 made nine full walks of 31-56k visits in 22
        minutes (#1926).

        Two things change, and "force means fresh" survives both:

        * **Reuse.** A slot counts as fresh if every row in it was fetched at or
          after the force floor -- ``force_refresh_since`` (when this request first
          asked), or FORCED_REFRESH_REUSE_MINUTES ago, whichever is earlier. The
          first pipeline walks; the rest read what it just fetched. Old rows kept
          alive by the shrink guard or a delta top-up never pass (see
          ``SQLCacheManager.slot_fetched_since``), and neither does any slot with a
          pending shrink anomaly, so the banner's "Retry now" still retries.
        * **Coalescing.** The walk runs under ``claim_raw_rebuild`` like any other.
          A forced LOSER cannot be lent the existing rows the way an unforced one is
          (they are what it was asked to replace), so it waits for the leader's
          fresh copy instead -- re-checking every FORCED_REFRESH_PEER_POLL_SECONDS,
          yielding ("waiting", seconds) so the page can say why, and giving up after
          FORCED_REFRESH_PEER_WAIT_SECONDS to walk on its own.
        """
        now = timezone.now()
        floor = now - timedelta(minutes=FORCED_REFRESH_REUSE_MINUTES)
        if force_refresh_since is not None and force_refresh_since < floor:
            floor = force_refresh_since
        started = time.monotonic()

        while True:
            waited = time.monotonic() - started
            with claim_raw_rebuild(opportunity_id, cache_manager.raw_slot_id) as is_leader:
                # Checked under the lock, so a peer that finished between our last
                # look and this claim is seen, not walked over. A slot with a pending
                # shrink anomaly never counts: it is serving rows a fetch just
                # rejected, and "Retry now" on that banner IS a forced read.
                if cache_manager.get_pending_raw_fetch_anomaly() is None and cache_manager.slot_fetched_since(
                    floor, require_images=include_images
                ):
                    fresh = cache_manager.get_raw_visit_count()
                elif is_leader or waited >= FORCED_REFRESH_PEER_WAIT_SECONDS:
                    if not is_leader:
                        logger.warning(
                            f"[SQL] Forced refresh for opp {opportunity_id} waited {waited:.0f}s for a peer's "
                            f"walk that never landed -- walking unguarded"
                        )
                    logger.info(f"[SQL] Forced refresh for opp {opportunity_id}, paginating export API")
                    yield from self._stream_raw_visits_uncached(
                        opportunity_id,
                        access_token,
                        cache_manager,
                        expected_visit_count=expected_visit_count,
                        user=user,
                        accept_low_count=accept_low_count,
                        pipeline_id=pipeline_id,
                        include_images=include_images,
                    )
                    return
                else:
                    fresh = None
            if fresh is not None:
                logger.info(
                    f"[SQL] Forced refresh for opp {opportunity_id} satisfied by a copy fetched since "
                    f"{floor.isoformat()} ({fresh} visits) -- not walking again"
                )
                self.last_raw_fetch_anomaly = cache_manager.get_pending_raw_fetch_anomaly()
                yield ("cached", fresh)
                return
            # A peer is walking this slot right now: wait for its rows.
            yield ("waiting", int(waited))
            time.sleep(FORCED_REFRESH_PEER_POLL_SECONDS)

    def _stream_raw_visits_uncached(
        self,
        opportunity_id: int,
        access_token: str,
        cache_manager,
        *,
        expected_visit_count: int | None,
        user,
        accept_low_count: bool,
        pipeline_id: int | None,
        include_images: bool = False,
    ) -> Generator[tuple[str, Any]]:
        """The actual pagination + cache write. Split out of ``stream_raw_visits`` so the
        single-flight guard can wrap it without re-indenting the retry/anomaly logic.

        ``include_images`` asks Connect for photo payloads AND records on the slot
        that it did, which is what ``slot_has_image_data`` reads back. Both halves
        matter: a slot filled without the flag answers "does this visit have a
        photo" with "the cache cannot say", and every image request then
        re-downloads the whole opportunity.
        """
        from connect_labs.labs.analysis.backends.visit_record import record_to_visit_dict
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError
        from connect_labs.labs.integrations.connect.factory import get_export_client

        endpoint = f"/export/opportunity/{opportunity_id}/user_visits/"
        params = {"images": "true"} if include_images else None
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
            cache_manager.store_raw_visits_start(expected_visit_count or 0, images_fetched=include_images)

            rows_so_far = 0

            try:
                with get_export_client(
                    opportunity_id=opportunity_id,
                    access_token=access_token,
                    timeout=180.0,
                    user=user,
                ) as client:
                    for page in client.paginate(endpoint, params=params):
                        # Convert v2 records to visit dicts (with form_json)
                        batch = [record_to_visit_dict(record, opportunity_id) for record in page]
                        if not batch:
                            continue

                        # Store the full batch (with form_json) to the SQL cache,
                        # then let the page go. Nothing downstream reads these rows
                        # -- the extraction step reads them back out of Postgres --
                        # so the page is the only thing that needs to be resident,
                        # and it is bounded by DEFAULT_PAGE_SIZE. This used to strip
                        # form_json and `slim_dicts.extend(batch)`, which kept every
                        # visit in the opportunity alive to be counted (#1575).
                        cache_manager.store_raw_visits_batch(batch)
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
                logger.info(f"[SQL] Streamed {rows_so_far} visits to DB (nothing retained in memory)")
                yield ("complete", rows_so_far)
                return

            logger.warning(
                f"[SQL] Raw stream for opp {opportunity_id} pipeline {pipeline_id} returned {rows_so_far} "
                f"rows, below {threshold:.0f} ({RAW_CACHE_SHRINK_THRESHOLD_PCT}% of previously-cached "
                f"{prior_count}) on attempt {attempt}/{RAW_CACHE_MAX_ATTEMPTS} -- discarding and retrying"
            )
            if attempt < RAW_CACHE_MAX_ATTEMPTS:
                cache_manager.store_raw_visits_abort()
            # The LAST attempt's rows are deliberately left un-aborted until the
            # block below has looked at whether the old cache still exists. If it
            # vanished there is nothing left to protect, and these rows — low, but
            # real — are better than nothing. See there.

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
        cache_manager.set_pending_raw_fetch_anomaly(self.last_raw_fetch_anomaly, minutes=RAW_CACHE_ANOMALY_TTL_MINUTES)
        old_count = cache_manager.get_raw_visit_count()
        if not old_count:
            # The old cache we were protecting vanished from under us (e.g. a
            # concurrent invalidation raced this guard). Serving nothing would be
            # exactly the failure this guard exists to prevent, and there is now
            # nothing left for it to protect -- so PROMOTE the last attempt's
            # rows instead of discarding them. They are low, but real, and
            # finalizing them overwrites nothing.
            #
            # This used to report `rows_so_far` as the count while those rows had
            # already been aborted, so the number described data no reader could
            # see. `fetch_raw_visits` shares this path now and must return actual
            # rows, which made the gap load-bearing rather than cosmetic.
            cache_manager.store_raw_visits_finalize(rows_so_far)
            old_count = rows_so_far
        else:
            # The old cache is intact and is what we serve, so the low attempt's
            # rows are discarded as every earlier attempt's were.
            cache_manager.store_raw_visits_abort()
        yield ("cached", old_count)

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

    def _try_delta_refresh(
        self,
        cache_manager,
        opportunity_id: int,
        access_token: str,
        *,
        expected_visit_count: int | None,
        pipeline_id: int | None,
        user=None,
    ) -> bool:
        """Top the cache up with only the visits it is missing. True if it is now current.

        This is the other half of #1361, and the half that removes the work rather
        than serialising it. Cache validity is ``count >= expected AND not expired``,
        so a miss has two causes and they deserve different answers:

        - **expired** — the periodic refresh, and the ONLY thing that re-reads
          existing visits. It must stay a full rebuild: a visit's ``status`` changes
          when it is reviewed, and that never moves the count, so nothing else would
          ever pick it up. This path deliberately declines to handle it.
        - **count short, still unexpired** — new visits arrived. Those are strictly
          NEW rows with higher ids, which is exactly what a cursor fetch returns.

        Only the second is handled here, which is why this costs nothing in
        freshness: status changes do not trigger it today either, and the TTL floor
        is untouched (see ``store_raw_visits_append_start`` for the expiry rule that
        keeps it that way).

        Returns False for anything it is not sure about — a missing anchor,
        non-numeric ids, a gap outside the configured band, an id ordering that
        contradicts the cursor assumption, or a concurrent finalize. The caller then
        does the ordinary full rebuild, so the worst case of every guard here is
        today's behaviour.
        """
        from connect_labs.labs.analysis.backends.visit_record import record_to_visit_dict
        from connect_labs.labs.integrations.connect.export_client import ExportAPIError
        from connect_labs.labs.integrations.connect.factory import get_export_client

        if not expected_visit_count:
            return False

        anchor = cache_manager.get_raw_delta_anchor()
        if anchor is None:
            return False
        prior_count, max_visit_id, expires_at = anchor

        gap = expected_visit_count - prior_count
        if gap < RAW_CACHE_DELTA_MIN_ROWS or gap > RAW_CACHE_DELTA_MAX_ROWS:
            return False

        logger.info(
            "[SQL] Raw cache SHORT for opp %s pipeline %s (%s cached, %s expected) — "
            "topping up from visit id %s instead of repaginating",
            opportunity_id,
            pipeline_id,
            prior_count,
            expected_visit_count,
            max_visit_id,
        )

        endpoint = f"/export/opportunity/{opportunity_id}/user_visits/"
        params = {"cursor_order": "forward", "last_id": max_visit_id}
        # Image readers and pipelines share this slot (#1921), so a pipeline's
        # top-up can land on rows fetched WITH images. Appending image-less rows
        # there would answer "no photo" for every new visit until the next full
        # rebuild, so the top-up matches what the slot already holds.
        images_fetched = cache_manager.slot_has_image_data(ignore_ttl=True)
        if images_fetched:
            params["images"] = "true"
        new_dicts: list[dict] = []
        try:
            with get_export_client(
                opportunity_id=opportunity_id,
                access_token=access_token,
                timeout=180.0,
                user=user,
            ) as client:
                for page in client.paginate(endpoint, params=params):
                    for record in page:
                        visit = record_to_visit_dict(record, opportunity_id)
                        # The cursor is only trustworthy if it really is returning
                        # ids ABOVE the mark. If it is not, the append would leave
                        # duplicates and a wrong count, so bail to a full rebuild.
                        raw_id = str(visit.get("id", ""))
                        if not raw_id.lstrip("-").isdigit() or int(raw_id) <= max_visit_id:
                            logger.warning(
                                "[SQL] delta for opp %s returned id %r at or below the "
                                "anchor %s — falling back to a full rebuild",
                                opportunity_id,
                                raw_id,
                                max_visit_id,
                            )
                            return False
                        new_dicts.append(visit)
                    if len(new_dicts) > RAW_CACHE_DELTA_MAX_ROWS:
                        logger.info(
                            "[SQL] delta for opp %s exceeded %s rows — full rebuild instead",
                            opportunity_id,
                            RAW_CACHE_DELTA_MAX_ROWS,
                        )
                        return False
        except ExportAPIError as e:
            logger.error("[SQL] delta fetch failed for opp %s: %s", opportunity_id, e)
            return False

        if not new_dicts:
            return False

        # RawVisitCache has UNIQUE(opportunity_id, pipeline_id, visit_count, visit_id),
        # so one id repeated across two pages of the cursor would abort the whole
        # append on a duplicate key. Overlap with rows we ALREADY hold is impossible
        # (every id here is above the anchor, and every cached id is at or below it);
        # overlap within the delta itself is not, so collapse it.
        deduped = {str(v.get("id")): v for v in new_dicts}
        if len(deduped) != len(new_dicts):
            logger.info(
                "[SQL] delta for opp %s returned %s rows over %s distinct ids — de-duplicated",
                opportunity_id,
                len(new_dicts),
                len(deduped),
            )
        new_dicts = list(deduped.values())

        cache_manager.store_raw_visits_append_start(expires_at, images_fetched=images_fetched)
        cache_manager.store_raw_visits_batch(new_dicts)
        visible = cache_manager.store_raw_visits_append_finalize(prior_count, prior_count + len(new_dicts))
        return bool(visible)

    def _lend_cache_during_peer_rebuild(
        self,
        cache_manager,
        opportunity_id,
        pipeline_id,
        *,
        skip_form_json,
        count_only: bool = False,
        require_images: bool = False,
    ):
        """Serve the existing rows because another connection is already rebuilding.

        Returns the loaded dicts, or None when there is nothing to lend — a
        first-ever fetch has no prior rows, and serving nothing would be far worse
        than briefly duplicating a walk, so the caller falls through and rebuilds.

        ``require_images`` refuses to lend a slot that was filled WITHOUT images to
        a caller that needs them: those rows cannot answer "does this visit have a
        photo", which is the whole question the image reader is asking. Declining
        to lend is not the same as declining to lock — the caller stays inside
        ``claim_raw_rebuild`` and simply does the walk, so at most one image walk
        runs per slot instead of one per request.

        ``count_only=True`` returns the ROW COUNT (an int) instead, still None when
        there is nothing to lend. The streaming caller only ever takes ``len()`` of
        what it gets back, and this is the hottest path there is under the load that
        motivated it: a burst of concurrent streams on one opportunity makes every
        non-leader land here, and each was materialising the whole opportunity out of
        Postgres to be counted and thrown away. ``prior_count`` below is already the
        COUNT query that answers it. See dimagi-internal/connect-labs#1575.
        """
        prior_count = cache_manager.get_raw_visit_count_ignoring_ttl()
        if not prior_count:
            logger.info(
                "[SingleFlight] opp %s pipeline %s is being rebuilt elsewhere but has no prior "
                "rows to lend — rebuilding anyway",
                opportunity_id,
                pipeline_id,
            )
            return None

        if require_images and not cache_manager.slot_has_image_data():
            logger.info(
                "[SingleFlight] opp %s pipeline %s is being rebuilt elsewhere but its rows carry "
                "no image data — rebuilding anyway (still under the lock)",
                opportunity_id,
                pipeline_id,
            )
            return None

        cache_manager.extend_raw_cache_ttl(minutes=RAW_CACHE_PEER_REBUILD_TTL_MINUTES)

        if count_only:
            # Re-read AFTER the TTL bump, so this matches what the list branch below
            # would have loaded (get_raw_visit_count filters on expires_at).
            lent_count = cache_manager.get_raw_visit_count()
            if not lent_count:
                return None
            logger.info(
                "[SingleFlight] opp %s pipeline %s is being rebuilt by another connection — "
                "serving %s existing visits instead of starting a second walk",
                opportunity_id,
                pipeline_id,
                lent_count,
            )
            return lent_count

        visit_dicts = self._load_from_cache(cache_manager, skip_form_json=skip_form_json, filter_visit_ids=None)
        if not visit_dicts:
            # Extending the TTL should have made them readable; if they vanished
            # underneath us (a concurrent finalize racing this) there is nothing to
            # lend after all.
            return None

        logger.info(
            "[SingleFlight] opp %s pipeline %s is being rebuilt by another connection — "
            "serving %s existing visits instead of starting a second walk",
            opportunity_id,
            pipeline_id,
            len(visit_dicts),
        )
        return visit_dicts

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
                visit_date=(
                    datetime.combine(cached_row.visit_date, datetime.min.time()) if cached_row.visit_date else None
                ),
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
        visit_dicts: list[dict] | None,
        skip_raw_store: bool = False,
        visit_count: int | None = None,
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
            visit_count: Pass this INSTEAD of ``visit_dicts`` whenever
                ``skip_raw_store`` is True. Nothing below this line reads an
                element of the list -- the three ``_process_*`` branches all take
                ``visit_count: int`` -- so on the already-stored paths the caller
                was building (or re-SELECTing) an entire opportunity's visits to
                compute one integer. Callers that must still hand over rows to be
                stored (the cchq / ocs / connect-export fetchers, which pass
                ``skip_raw_store=False``) keep passing ``visit_dicts`` unchanged.
                See dimagi-internal/connect-labs#1575.
        """
        if visit_count is None:
            if visit_dicts is None:
                raise ValueError("process_and_cache requires either visit_dicts or visit_count")
            visit_count = len(visit_dicts)
        if not skip_raw_store and visit_dicts is None:
            raise ValueError("process_and_cache needs visit_dicts when skip_raw_store is False")

        cache_manager = SQLCacheManager(opportunity_id, config)

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
                "visit_date": (
                    row.visit_date.date()
                    if row.visit_date and hasattr(row.visit_date, "date") and callable(row.visit_date.date)
                    else row.visit_date
                ),
                "status": row.status,
                "flagged": row.flagged,
                "location": (
                    row.location
                    if hasattr(row, "location")
                    else (f"{row.latitude} {row.longitude}" if row.latitude and row.longitude else "")
                ),
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
