"""The one HTTP layer the three clients share: timeouts, error parsing, retries on GETs only.

Tests put an ``httpx.MockTransport`` in ``HTTP_TRANSPORT`` (as messaging's PersonalID client
does), so no test ever reaches a real server.

Retries: only an idempotent GET is retried, and only on a network error, a timeout or a 502/503/
504. A POST is never retried here — a repeated recovery would burn a backup-code attempt, a
repeated claim or confirmation could act twice.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from .errors import ServiceError

logger = logging.getLogger(__name__)

#: Tests set an ``httpx.MockTransport`` here.
HTTP_TRANSPORT: httpx.BaseTransport | None = None

TIMEOUT = httpx.Timeout(15.0, connect=5.0)
GET_ATTEMPTS = 3
_RETRY_STATUSES = {502, 503, 504}
_BACKOFF_SECONDS = 0.5


def client() -> httpx.Client:
    return httpx.Client(timeout=TIMEOUT, transport=HTTP_TRANSPORT, follow_redirects=False)


def json_or_none(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        return None


def seconds(value) -> int:
    """A token's ``expires_in``, or 0 (the caller's default) when missing or not a number."""
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def error_code_of(body: Any) -> str:
    """The code an error body carries: ``error_code`` (connect-id, Connect) or OAuth's ``error``."""
    if isinstance(body, dict):
        for key in ("error_code", "error"):
            value = body.get(key)
            if isinstance(value, str) and value:
                return value
    return ""


def raise_for(response: httpx.Response, error_cls: type[ServiceError]) -> None:
    """Raise `error_cls` for a non-2xx answer, with the status and code only (never the body)."""
    if response.is_success:
        return
    body = json_or_none(response)
    extra = {}
    if isinstance(body, dict):
        extra = {k: body[k] for k in ("attempts_left", "retry_after_seconds") if k in body}
    raise error_cls(status=response.status_code, error_code=error_code_of(body), **extra)


def request(
    method: str,
    url: str,
    error_cls: type[ServiceError],
    *,
    log_name: str,
    **kwargs,
) -> httpx.Response:
    """Send one request; GETs retry on transient failures. Raises `error_cls` on failure.

    `log_name` is a fixed label for log lines (never the URL's query or any header).
    """
    attempts = GET_ATTEMPTS if method.upper() == "GET" else 1
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            with client() as http:
                response = http.request(method, url, **kwargs)
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.info("connect_web %s: %s (attempt %d/%d)", log_name, type(exc).__name__, attempt, attempts)
        else:
            if response.status_code in _RETRY_STATUSES and attempt < attempts:
                logger.info(
                    "connect_web %s: HTTP %d (attempt %d/%d)", log_name, response.status_code, attempt, attempts
                )
            else:
                raise_for(response, error_cls)
                return response
        if attempt < attempts:
            time.sleep(_BACKOFF_SECONDS * attempt)
    raise error_cls(f"{error_cls.service} could not be reached ({type(last_exc).__name__})") from None
