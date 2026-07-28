"""
Paginated JSON export API client for Connect.

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
import re

import httpx

logger = logging.getLogger(__name__)

_OPP_ID_RE = re.compile(r"/opportunity/(\d+)/")


def _record_export_audit(
    endpoint: str,
    row_count: int,
    error: str | None,
    terminated_early: bool = False,
) -> None:
    """Audit one bulk PHI fetch (visit/user exports). Best-effort, lazy import
    so this module stays importable outside a configured Django context.

    ``terminated_early`` marks a read the caller deliberately cut short (see
    ``paginate(partial_ok=True)``). That is a *successful* partial export, not
    a failure — but the compliance record still needs to show the read stopped
    short of the full dataset, so it is kept in metadata.
    """
    try:
        from connect_labs.audit_trail import service
        from connect_labs.audit_trail.models import Action, Outcome

        opp_match = _OPP_ID_RE.search(endpoint)
        resource_type = endpoint.split("?")[0].rstrip("/").rsplit("/", 1)[-1] or "export"
        metadata = {"endpoint": endpoint.split("?")[0][:200]}
        if error:
            metadata["error"] = error
        if terminated_early:
            metadata["terminated"] = "early"
        service.record(
            Action.EXPORT,
            resource_type=resource_type,
            record_count=row_count,
            opportunity_id=int(opp_match.group(1)) if opp_match else None,
            outcome=Outcome.FAILURE if error else Outcome.SUCCESS,
            metadata=metadata,
        )
    except Exception:  # pragma: no cover - audit must never break exports
        logger.exception("Export audit recording failed (non-fatal)")


VERSION_HEADER = "application/json; version=2.0"
DEFAULT_TIMEOUT = 60.0
# Server defaults to 1000, max 5000. 2500 trades ~2.5x fewer round-trips for
# proportionally larger response payloads and longer per-page latency — still
# well under the 180s timeout used by the streaming backend, and frequent
# enough to keep SSE progress events flowing.
DEFAULT_PAGE_SIZE = 2500


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
        # follow_redirects=True works around a production bug in dimagi/commcare-connect
        # where gunicorn strips X-Forwarded-Proto (no --forwarded-allow-ips flag), causing
        # `request.scheme` to be 'http' in pagination's get_next_link(). The server then
        # emits `next` URLs with http:// scheme, and the reverse proxy 301-redirects them
        # back to https://. Following redirects here makes pagination robust regardless.
        self.http_client = httpx.Client(
            headers={
                "Authorization": f"Bearer {access_token}",
                "Accept": VERSION_HEADER,
                "Accept-Encoding": "gzip, deflate",
            },
            timeout=timeout,
            follow_redirects=True,
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

    def paginate(self, endpoint: str, params: dict | None = None, *, partial_ok: bool = False):
        """Audited wrapper around :meth:`_paginate`.

        Counts rows as pages stream through and records one EXPORT audit
        event when the generator finishes, errors, or is abandoned — this is
        the bulk-PHI choke point (visit form JSON, FLW identities).

        Args:
            partial_ok: Declare that this caller may stop consuming before the
                stream is exhausted — it is *sampling*, not exporting the whole
                dataset. Abandoning a generator makes Python throw
                ``GeneratorExit`` into it at the ``yield``, which is
                indistinguishable from a request being torn down mid-download
                (client closed the tab, gateway timed out). Only the caller
                knows which of the two it is, so only the caller can say:

                - ``partial_ok=True``  → early stop is a *successful* partial
                  export, tagged ``metadata["terminated"] == "early"``.
                - ``partial_ok=False`` (default) → an unrequested teardown, and
                  still recorded as a failure.
        """
        rows = 0
        error: str | None = None
        terminated_early = False
        try:
            for page in self._paginate(endpoint, params=params):
                rows += len(page)
                yield page
        except GeneratorExit:
            # Consumer stopped early (`break`/`return`) *or* the request died
            # mid-stream. Python cannot tell these apart — `partial_ok` is the
            # caller's declaration of which one this is.
            if partial_ok:
                terminated_early = True
            else:
                error = "GeneratorExit"
            raise
        except BaseException as exc:
            error = type(exc).__name__
            raise
        finally:
            _record_export_audit(endpoint, rows, error, terminated_early=terminated_early)

    def _paginate(self, endpoint: str, params: dict | None = None):
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
        # Inject default page_size only on the first request. The server preserves
        # it in `next` URLs, so subsequent pages keep the same size automatically.
        request_params = dict(params) if params else {}
        request_params.setdefault("page_size", DEFAULT_PAGE_SIZE)

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
            # Prod builds `next` URLs as http:// behind its proxy; following
            # them verbatim costs a 301 redirect on every page (doubling
            # round-trips on a 40-page visit crawl). Upgrade the scheme here.
            if url and url.startswith("http://"):
                url = "https://" + url[len("http://") :]
            request_params = None

    def fetch_all(self, endpoint: str, params: dict | None = None) -> list[dict]:
        """Materialize every page into a single list. Convenience for small responses."""
        rows: list[dict] = []
        for page in self.paginate(endpoint, params=params):
            rows.extend(page)
        return rows
