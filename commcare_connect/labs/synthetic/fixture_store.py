"""Loads per-opp fixture JSON from Google Drive with in-process caching.

Cache is a plain dict keyed by (opp_id, endpoint_key). Entries live until the
worker restarts or `reload(opp_id)` is called. Dataset sizes are demo-scale.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from commcare_connect.labs.synthetic.gdrive import DriveAPIError

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
}


class FixtureStore:
    """Serves fixture JSON for a set of synthetic opportunities.

    Cache keys include the registered GDrive ``folder_id`` so that a regen
    that swaps an opp's folder gets an automatic cache miss across every
    worker — no cross-worker reload broadcast needed. Old keys go stale but
    sit dormant in memory until the worker restarts (demo-scale entries).

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

        listing = self._folder_listing_cache.get((opp_id, folder_id))
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
