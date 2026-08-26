"""Loads per-opp fixture JSON from Google Drive, cached per-process AND shared.

Two tiers, because one was not enough
-------------------------------------
L1 is a plain dict, keyed by (opp_id, folder_id, endpoint_key). L2 is the Django
cache (Redis in every deployed environment).

L1 alone was the original design, and on 2026-08-26 it was measured doing almost
nothing. `WorkflowRunView` loads workers for every opportunity a multi-opp
workflow spans; for synthetic opps each of those is a Drive folder-listing plus
a file download. A run page over 11 opps is therefore 22 sequential Drive
round-trips, and prod serves on 6 independent processes (WEB_CONCURRENCY=3 x 2
tasks), each holding its own L1. A warm entry was reachable roughly one load in
six, so real page loads paid the full fan-out over and over: 12-16s each, for
bytes that had already been fetched minutes earlier by a sibling process.

L2 makes one process's fetch serve all six. Redis is configured with
IGNORE_EXCEPTIONS, so if it is unavailable every read simply misses and the
behaviour degrades to exactly the L1-only design -- never an error.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from django.core.cache import cache

from connect_labs.labs.synthetic.gdrive import DriveAPIError

logger = logging.getLogger(__name__)

ENDPOINT_FILES: dict[str, str] = {
    "": "opportunity.json",
    "user_visits": "user_visits.json",
    "user_data": "user_data.json",
    "completed_works": "completed_works.json",
    "completed_module": "completed_module.json",
    # app_structure is served by the HTTP export API. The file holds the
    # {"learn_app", "deliver_app"} wrapper (each value the app JSON or null),
    # mirroring real Connect. Absent file => the opp has no app (served as nulls).
    "app_structure": "app_structure.json",
    # #650 gap 2 — Scout's standard connect_sync pipeline materializes these too.
    # Absent file => empty page (the opp has no such data), letting the same
    # pipeline run unchanged against synthetic opps.
    "payment": "payment.json",
    "invoice": "invoice.json",
    "assessment": "assessment.json",
}


# Fixtures change only when someone regenerates or hand-edits one in Drive, and a
# folder swap already invalidates by key. This TTL is the backstop for an in-place
# edit to the SAME folder, and is deliberately short enough that a manual fix shows
# up on its own without anyone knowing `reload` exists.
SHARED_CACHE_TTL_SECONDS = 900

# Raw bytes are cached, not the parsed object: Redis stays compact and there is no
# pickle round-trip of a large nested structure. A big opp's user_visits.json runs
# to many MB (a KMC clone is ~11.5k visits), and pushing that through Redis on
# every miss would trade one slow dependency for another -- so above this size L1
# still caches it and L2 declines.
SHARED_CACHE_MAX_BYTES = 2 * 1024 * 1024


class FixtureStore:
    """Serves fixture JSON for a set of synthetic opportunities.

    Cache keys include the registered GDrive ``folder_id`` so that a regen
    that swaps an opp's folder gets an automatic cache miss across every
    worker — no cross-worker reload broadcast needed. Old keys go stale but
    sit dormant in memory until the worker restarts (demo-scale entries).

    An in-place edit to the SAME folder is the case folder_id keying cannot
    catch, and that is what ``reload(opp_id)`` and the shared tier's generation
    counter are for.

    Args:
        drive: something that implements `list_folder(folder_id)` and
            `download_file(file_id)`.
        folder_lookup: callable mapping opp_id -> gdrive folder ID (or None).
    """

    def __init__(self, drive, folder_lookup: Callable[[int], str | None]):
        self._drive = drive
        self._folder_lookup = folder_lookup
        # Cache keyed on (opp_id, folder_id, endpoint_key) so a folder swap
        # auto-invalidates per-opp content without an explicit reload call.
        self._cache: dict[tuple[int, str, str], Any] = {}
        self._folder_listing_cache: dict[tuple[int, str], dict[str, str]] = {}

    # ------------------------------------------------------------------
    # Shared (L2) cache
    #
    # Every access is wrapped: the shared tier is an optimisation, and a cache
    # that can break a page it was added to speed up is worse than no cache.
    # django_redis already sets IGNORE_EXCEPTIONS, but locmem in tests and any
    # future backend make no such promise.
    # ------------------------------------------------------------------

    def _generation(self, opp_id: int) -> int:
        """Bump-to-invalidate counter, so `reload` needs no pattern delete.

        Folding it into every key means one `incr` retires an opp's whole shared
        footprint at once, across all six processes and without enumerating the
        folder_ids involved. Old keys are unreachable and expire on their TTL.
        """
        try:
            return int(cache.get(f"synthetic:fixture:gen:{opp_id}") or 0)
        except Exception:
            logger.debug("synthetic: shared cache generation read failed", exc_info=True)
            return 0

    def _shared_key(self, opp_id: int, folder_id: str, suffix: str, gen: int) -> str:
        return f"synthetic:fixture:v1:{gen}:{opp_id}:{folder_id}:{suffix}"

    def _shared_get(self, key: str):
        try:
            return cache.get(key)
        except Exception:
            logger.debug("synthetic: shared cache read failed for %s", key, exc_info=True)
            return None

    def _shared_set(self, key: str, value) -> None:
        try:
            cache.set(key, value, SHARED_CACHE_TTL_SECONDS)
        except Exception:
            logger.debug("synthetic: shared cache write failed for %s", key, exc_info=True)

    def load_endpoint(self, opp_id: int, endpoint_key: str) -> list[dict] | dict:
        """Return parsed JSON for one endpoint. Empty list on any miss."""
        if endpoint_key not in ENDPOINT_FILES:
            logger.warning("synthetic: unknown endpoint key %r for opp %s", endpoint_key, opp_id)
            return []

        folder_id = self._folder_lookup(opp_id)
        if not folder_id:
            logger.warning(
                "synthetic: no gdrive folder registered for opp %s; returning empty",
                opp_id,
            )
            return []

        cached = self._cache.get((opp_id, folder_id, endpoint_key))
        if cached is not None:
            return cached

        # Resolved once per load rather than per key: both shared lookups below
        # belong to the same logical read and must agree on the generation.
        gen = self._generation(opp_id)

        listing = self._folder_listing_cache.get((opp_id, folder_id))
        if listing is None:
            listing_key = self._shared_key(opp_id, folder_id, "listing", gen)
            listing = self._shared_get(listing_key)
            if listing is None:
                try:
                    listing = self._drive.list_folder(folder_id)
                except DriveAPIError as e:
                    logger.warning(
                        "synthetic: list_folder failed for opp %s folder %s: %s; returning empty",
                        opp_id,
                        folder_id,
                        e,
                    )
                    return []
                self._shared_set(listing_key, listing)
            self._folder_listing_cache[(opp_id, folder_id)] = listing

        filename = ENDPOINT_FILES[endpoint_key]
        file_id = listing.get(filename)
        if file_id is None:
            logger.warning(
                "synthetic: missing fixture file %s in folder %s for opp %s",
                filename,
                folder_id,
                opp_id,
            )
            self._cache[(opp_id, folder_id, endpoint_key)] = []
            return []

        raw_key = self._shared_key(opp_id, folder_id, f"raw:{endpoint_key}:{file_id}", gen)
        raw = self._shared_get(raw_key)
        if raw is None:
            try:
                raw = self._drive.download_file(file_id)
            except DriveAPIError as e:
                logger.warning(
                    "synthetic: download_file failed for opp %s file %s: %s; returning empty",
                    opp_id,
                    filename,
                    e,
                )
                return []
            # The size gate is on the raw bytes, which is the number we already
            # have -- measuring the parsed object would mean serialising it twice.
            if len(raw) <= SHARED_CACHE_MAX_BYTES:
                self._shared_set(raw_key, raw)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as e:
            # Manual edits in Drive can leave a file with a trailing comma,
            # truncated mid-object, or entirely empty. Don't 500 every caller —
            # degrade to empty with a loud warning so the operator sees it in
            # the labs logs.
            logger.warning(
                "synthetic: malformed JSON in fixture %s for opp %s (%d bytes): %s; returning empty",
                filename,
                opp_id,
                len(raw),
                e,
            )
            self._cache[(opp_id, folder_id, endpoint_key)] = []
            return []
        self._cache[(opp_id, folder_id, endpoint_key)] = parsed
        return parsed

    def reload(self, opp_id: int) -> None:
        """Drop any cached data for this opp; next `load_endpoint` re-pulls.

        With folder_id-keyed entries a swap auto-misses, so reload is mainly
        useful for forcing a re-pull of the SAME folder (e.g. after a manual
        Drive edit). Both per-folder listings and per-endpoint payloads are
        cleared for this opp_id across all folder_ids.
        """
        for key in [k for k in self._folder_listing_cache if k[0] == opp_id]:
            self._folder_listing_cache.pop(key)
        for key in [k for k in self._cache if k[0] == opp_id]:
            self._cache.pop(key)
        # Retire the shared tier too. Before this, `reload` cleared the dict of
        # whichever of the six processes happened to serve the reload request and
        # left the other five serving the stale fixture -- so "reload" worked about
        # one time in six, non-deterministically. Bumping the generation is what
        # makes it mean the same thing everywhere.
        try:
            gen_key = f"synthetic:fixture:gen:{opp_id}"
            if cache.get(gen_key) is None:
                cache.set(gen_key, 1, None)
            else:
                cache.incr(gen_key)
        except Exception:
            logger.warning("synthetic: could not bump shared cache generation for opp %s", opp_id, exc_info=True)
