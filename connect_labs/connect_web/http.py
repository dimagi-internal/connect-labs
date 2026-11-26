"""The one HTTP layer the clients share: pooled connections, time budgets, error parsing, GET retries.

**Pooled connections.** One `httpx.Client` per process for PersonalID / Connect / HQ and a
separate one for Formplayer (`shared_client`), so a page that makes several calls pays the TLS
handshake once, not per call. ``httpx.Client`` is thread-safe; its pool is bounded (`LIMITS`).

Tests put an ``httpx.MockTransport`` in ``HTTP_TRANSPORT`` (as messaging's PersonalID client
does), so no test ever reaches a real server; the shared clients are rebuilt whenever that
transport changes.

**Time budgets.** Every outbound operation has a total budget (`DEFAULT_BUDGET_SECONDS`), not a
per-attempt timeout that retries can multiply: each attempt's timeout is cut to what is left, and
a retry is only started when there is time for it. The connect timeout is short (`CONNECT_TIMEOUT`).

**Retries.** Only an idempotent GET is retried, and only on a connection error, a 502/503/504 —
or a read timeout when the caller allows it (`retry_read_timeout`; a slow list call does not: if
Connect took the whole read timeout once, asking again only doubles the wait). A POST is never
retried here — a repeated recovery would burn a backup-code attempt, a repeated claim or
confirmation could act twice.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

import httpx

from .errors import ServiceError

logger = logging.getLogger(__name__)

#: Tests set an ``httpx.MockTransport`` here.
HTTP_TRANSPORT: httpx.BaseTransport | None = None

CONNECT_TIMEOUT = 3.0
#: The longest one read may take, within the operation's budget.
READ_TIMEOUT = 15.0
#: The total time one operation (all its attempts) may take.
DEFAULT_BUDGET_SECONDS = 20.0
TIMEOUT = httpx.Timeout(READ_TIMEOUT, connect=CONNECT_TIMEOUT)
LIMITS = httpx.Limits(max_connections=50, max_keepalive_connections=20, keepalive_expiry=30.0)
GET_ATTEMPTS = 3
_RETRY_STATUSES = {502, 503, 504}
_BACKOFF_SECONDS = 0.5
#: Don't start an attempt with less time than this left in the budget.
_MIN_ATTEMPT_SECONDS = 0.5

_clients: dict[str, tuple[object, httpx.Client]] = {}
_clients_lock = threading.Lock()


def shared_client(name: str = "default") -> httpx.Client:
    """The process's pooled client called `name` (rebuilt when a test swaps `HTTP_TRANSPORT`)."""
    transport = HTTP_TRANSPORT
    entry = _clients.get(name)
    if entry is not None and entry[0] is transport:
        return entry[1]
    with _clients_lock:
        entry = _clients.get(name)
        if entry is not None and entry[0] is transport:
            return entry[1]
        client = httpx.Client(timeout=TIMEOUT, limits=LIMITS, transport=transport, follow_redirects=False)
        _clients[name] = (transport, client)
        # The old client is left to the garbage collector: another thread may still be using it.
        return client


def client() -> httpx.Client:
    return shared_client("default")


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


def attempt_timeout(remaining: float, read: float = READ_TIMEOUT) -> httpx.Timeout:
    """One attempt's timeout: the usual limits, cut to what is left of the budget."""
    remaining = max(remaining, 0.1)
    return httpx.Timeout(min(read, remaining), connect=min(CONNECT_TIMEOUT, remaining))


def request(
    method: str,
    url: str,
    error_cls: type[ServiceError],
    *,
    log_name: str,
    budget: float = DEFAULT_BUDGET_SECONDS,
    read_timeout: float = READ_TIMEOUT,
    retry_read_timeout: bool = True,
    **kwargs,
) -> httpx.Response:
    """Send one request within `budget` seconds; GETs retry on transient failures. Raises `error_cls`.

    `log_name` is a fixed label for log lines (never the URL's query or any header).
    """
    is_get = method.upper() == "GET"
    attempts = GET_ATTEMPTS if is_get else 1
    deadline = time.monotonic() + budget
    last_exc: Exception | None = None
    last_response: httpx.Response | None = None
    for attempt in range(1, attempts + 1):
        remaining = deadline - time.monotonic()
        if attempt > 1 and remaining < _MIN_ATTEMPT_SECONDS:
            break
        try:
            response = client().request(method, url, timeout=attempt_timeout(remaining, read_timeout), **kwargs)
        except httpx.HTTPError as exc:
            last_exc = exc
            logger.info("connect_web %s: %s (attempt %d/%d)", log_name, type(exc).__name__, attempt, attempts)
            if isinstance(exc, httpx.ReadTimeout) and not retry_read_timeout:
                break
        else:
            if response.status_code in _RETRY_STATUSES and attempt < attempts:
                logger.info(
                    "connect_web %s: HTTP %d (attempt %d/%d)", log_name, response.status_code, attempt, attempts
                )
                last_exc = None
                last_response = response
            else:
                raise_for(response, error_cls)
                return response
        if attempt < attempts:
            pause = _BACKOFF_SECONDS * attempt
            if time.monotonic() + pause + _MIN_ATTEMPT_SECONDS >= deadline:
                break
            time.sleep(pause)
    if last_exc is None and last_response is not None:
        # Out of budget after a retryable status: report that status.
        raise_for(last_response, error_cls)
    if isinstance(last_exc, httpx.TimeoutException):
        raise error_cls(f"{error_cls.service} took too long to answer") from None
    raise error_cls(f"{error_cls.service} could not be reached ({type(last_exc).__name__})") from None
