"""Loads per-opp fixture JSON from Google Drive with in-process caching.

Cache is a plain dict keyed by (opp_id, endpoint_key). Entries live until the
worker restarts or `reload(opp_id)` is called. Dataset sizes are demo-scale.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)

ENDPOINT_FILES: dict[str, str] = {
    "": "opportunity.json",
    "user_visits": "user_visits.json",
    "user_data": "user_data.json",
    "completed_works": "completed_works.json",
    "completed_module": "completed_module.json",
}


class FixtureStore:
    """Serves fixture JSON for a set of synthetic opportunities.

    Args:
        drive: something that implements `list_folder(folder_id)` and
            `download_file(file_id)`.
        folder_lookup: callable mapping opp_id -> gdrive folder ID (or None).
    """

    def __init__(self, drive, folder_lookup: Callable[[int], str | None]):
        self._drive = drive
        self._folder_lookup = folder_lookup
        self._cache: dict[tuple[int, str], Any] = {}
        self._folder_listing_cache: dict[int, dict[str, str]] = {}

    def load_endpoint(self, opp_id: int, endpoint_key: str) -> list[dict] | dict:
        """Return parsed JSON for one endpoint. Empty list on any miss."""
        if endpoint_key not in ENDPOINT_FILES:
            logger.warning("synthetic: unknown endpoint key %r for opp %s", endpoint_key, opp_id)
            return []

        cached = self._cache.get((opp_id, endpoint_key))
        if cached is not None:
            return cached

        folder_id = self._folder_lookup(opp_id)
        if not folder_id:
            raise ValueError(f"No gdrive folder registered for synthetic opp {opp_id}")

        listing = self._folder_listing_cache.get(opp_id)
        if listing is None:
            listing = self._drive.list_folder(folder_id)
            self._folder_listing_cache[opp_id] = listing

        filename = ENDPOINT_FILES[endpoint_key]
        file_id = listing.get(filename)
        if file_id is None:
            logger.warning(
                "synthetic: missing fixture file %s in folder %s for opp %s",
                filename,
                folder_id,
                opp_id,
            )
            self._cache[(opp_id, endpoint_key)] = []
            return []

        raw = self._drive.download_file(file_id)
        parsed = json.loads(raw)
        self._cache[(opp_id, endpoint_key)] = parsed
        return parsed

    def reload(self, opp_id: int) -> None:
        """Drop any cached data for this opp; next `load_endpoint` re-pulls."""
        self._folder_listing_cache.pop(opp_id, None)
        for key in [k for k in self._cache if k[0] == opp_id]:
            self._cache.pop(key)
