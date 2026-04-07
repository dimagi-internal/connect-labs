"""
Paginated JSON export API client for CommCare Connect.

Wraps the `/export/...` v2 endpoints (Accept: application/json; version=2.0).
Handles keyset pagination by following `next` URLs until null.

Usage:
    with ExportAPIClient(base_url, access_token) as client:
        # Stream pages (memory-efficient)
        for page in client.paginate("/export/opportunity/42/user_visits/"):
            process(page)  # page is list[dict]

        # Or materialize everything
        rows = client.fetch_all("/export/opportunity/42/user_data/")
"""
import logging

import httpx

logger = logging.getLogger(__name__)

VERSION_HEADER = "application/json; version=2.0"
DEFAULT_TIMEOUT = 60.0


class ExportAPIError(Exception):
    """Raised when the export API returns an error or pagination fails."""


class ExportAPIClient:
    """Client for the v2 paginated JSON `/export/...` endpoints."""

    def __init__(
        self,
        base_url: str,
        access_token: str,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        self.base_url = base_url.rstrip("/")
        self.access_token = access_token
        self.http_client = httpx.Client(
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": VERSION_HEADER,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
        )

    def close(self):
        if self.http_client is not None:
            self.http_client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()

    def _resolve_url(self, endpoint: str) -> str:
        """Accept either an absolute path (`/export/...`) or a bare endpoint."""
        if endpoint.startswith("http://") or endpoint.startswith("https://"):
            return endpoint
        if not endpoint.startswith("/"):
            endpoint = "/" + endpoint
        return f"{self.base_url}{endpoint}"

    def paginate(self, endpoint: str, params: dict | None = None):
        """
        Yield each page's `results` list until the server's `next` is null.

        Args:
            endpoint: Path like `/export/opportunity/42/user_visits/` or full URL.
            params: Initial query parameters (e.g., `{"images": "true"}`). Only
                used for the first request — subsequent requests follow the
                `next` URL verbatim, which already contains all preserved params.

        Yields:
            list[dict]: One list of records per page.

        Raises:
            ExportAPIError: On HTTP error, invalid JSON, or missing `results` key.
        """
        url: str | None = self._resolve_url(endpoint)
        request_params: dict | None = params

        while url is not None:
            try:
                response = self.http_client.get(url, params=request_params)
                response.raise_for_status()
            except httpx.HTTPStatusError as e:
                raise ExportAPIError(f"Export API returned {e.response.status_code} for {url}") from e
            except httpx.HTTPError as e:
                raise ExportAPIError(f"Export API request failed for {url}: {e}") from e

            try:
                payload = response.json()
            except ValueError as e:
                raise ExportAPIError(f"Export API returned invalid JSON for {url}: {e}") from e

            if "results" not in payload:
                raise ExportAPIError(f"Export API response missing 'results' key for {url}: {payload!r}")

            yield payload["results"]

            # Server's `next` already includes preserved params; don't re-pass ours.
            url = payload.get("next")
            request_params = None

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Materialize every page into a single list. Convenience for small responses."""
        rows: list[dict] = []
        for page in self.paginate(endpoint, params=params):
            rows.extend(page)
        return rows
