"""Drop-in replacement for `ExportAPIClient` that reads from fixtures.

Same public API — `paginate(endpoint, params=None)` and
`fetch_all(endpoint, params=None)` — so the factory can swap it in without
touching callsites. No pagination slicing and no param handling: returns one
page with every fixture row.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


class SyntheticExportClient:
    def __init__(self, opp_id: int, fixture_store):
        self.opp_id = opp_id
        self.store = fixture_store

    def paginate(self, endpoint: str, params: dict | None = None):
        if params:
            logger.debug("synthetic: ignoring params %r for %s", params, endpoint)
        rows = self.store.load_endpoint(self.opp_id, self._endpoint_key(endpoint))
        yield [rows] if isinstance(rows, dict) else rows

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        if params:
            logger.debug("synthetic: ignoring params %r for %s", params, endpoint)
        rows = self.store.load_endpoint(self.opp_id, self._endpoint_key(endpoint))
        return [rows] if isinstance(rows, dict) else rows

    def close(self):
        pass

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    @staticmethod
    def _endpoint_key(endpoint: str) -> str:
        # "/export/opportunity/42/user_visits/" -> "user_visits"
        # "/export/opportunity/42/"             -> ""
        stripped = endpoint.rstrip("/")
        segments = stripped.split("/")
        tail = segments[-1]
        if tail.isdigit():  # e.g. ".../42" — we're at the opportunity detail
            return ""
        return tail
