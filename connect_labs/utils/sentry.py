"""Sentry event enrichment and noise filtering for the labs deployment.

Two problems this solves.

**Attribution — why every issue showed ``Users: 0``.** Sentry's Django
integration only attaches ``request.user`` when ``send_default_pii=True``
(see ``sentry_sdk.integrations.django._set_user_info``) — a switch that also
ships cookies, request bodies and client IPs. Labs sits next to PHI, so that
switch stays off. Instead we attach *only* the acting identity, explicitly,
from the audit-trail contextvar the codebase already opens at every edge:

* web requests — ``AuditTrailMiddleware``
* MCP tool calls — ``audit_context()`` in ``connect_labs.mcp.server``
* Celery tasks — the ``task_prerun`` receiver in ``audit_trail.signals``

Reusing that single source means Sentry and the HIPAA audit trail can never
disagree about who did something. Explicitly-set ``user`` fields survive
``send_default_pii=False``: the SDK's ``EventScrubber`` denylists
``ip_address``/``remote_addr``, not ``id``.

**Id only — no username, no email, no IP.** Sentry keys its "Users" count
and per-user filtering off ``user.id`` alone, so anything more only puts
people's identifiers in Sentry's store and in every export of it. To go from
an id to a person, use the labs admin, or pivot on the ``labs.request_id`` tag
into the audit trail — which records the same identity for the same action.

**Scrubbing — Sentry is a debugging store, not a data store.** Labs handles
programme, beneficiary and frontline-worker data (forms, visits, cases,
images). None of it may leave with an event. Settings turn off frame local
variables (``include_local_variables=False``), request bodies and default
PII; on top of that every event, transaction and breadcrumb goes through
``scrub_event`` / ``before_breadcrumb`` here, which keep the *shape* of a
failure (exception type, stack, route template, timings, release) and drop
the content: query strings (from ``request`` and from any URL in a message,
breadcrumb or span), request data and cookies, every header but a small
allowlist, user fields other than ``id``, Celery task arguments, and
payload-looking ``extra`` keys. ``labs.path`` is the route template
(``labs/workflow/<int:pk>/``) for web requests, never the concrete path,
which can carry record ids and worker usernames.

**Noise.** Two high-volume issue classes were drowning real errors: internet
scanners opening websockets against the ALB, and FastMCP's own ``ERROR`` log
for every ``ToolError`` we raise — including plain input-validation errors
("Provide exactly one of opportunity_id / program_id"), which are 4xx-shaped
user errors, not defects. Genuine tool failures are still reported: the
``UPSTREAM_ERROR`` branch in ``mcp.server`` logs them itself, with the
original traceback and (now) full user attribution.

Enrichment is best-effort by contract — a bug in it must never swallow the
error being reported, so each step is independently guarded. Scrubbing is
the opposite: it fails closed. If scrubbing raises, the event is dropped,
because an unscrubbed event is the one thing this module must never send.
"""

from __future__ import annotations

import logging
import re

logger = logging.getLogger(__name__)

# FastMCP's tool dispatcher logs an ERROR for every ToolError raised out of a
# handler (``_call_tool``: ``except FastMCPError: logger.exception(...)``, and
# ToolError is a FastMCPError). Ours are raised deliberately: MCPToolError (bad
# input / not found / permission / rate limit) is a 4xx-shaped user error, and
# the UPSTREAM_ERROR branch already reports the underlying exception itself with
# a real traceback. Either way the FastMCP-logged copy is duplicate or noise,
# and it arrives unattributed — it is logged after our audit context is gone.
# Scoped to ToolError so a genuine FastMCP-internal failure still reports.
_DUPLICATE_TOOL_ERROR_LOGGER = "fastmcp.server.server"

# Ambient internet noise: scanners and misrouted proxies open websocket
# connections against the ALB, which Django's ASGI handler rejects. Nothing is
# broken and there is no action to take.
_WEBSOCKET_REJECTION = "Django can only handle ASGI/HTTP connections"


def _exception_type_and_value(event, hint) -> tuple[str, str]:
    """(type_name, message) for the event's exception, or ("", "")."""
    exc_info = (hint or {}).get("exc_info")
    if exc_info and len(exc_info) >= 2 and exc_info[1] is not None:
        return type(exc_info[1]).__name__, str(exc_info[1])
    try:
        values = event.get("exception", {}).get("values") or []
        if values:
            return values[-1].get("type") or "", values[-1].get("value") or ""
    except AttributeError:
        pass
    return "", ""


def _is_noise(event, hint) -> bool:
    exc_type, exc_value = _exception_type_and_value(event, hint)
    if exc_type == "ToolError" and event.get("logger") == _DUPLICATE_TOOL_ERROR_LOGGER:
        return True
    if exc_type == "ValueError" and _WEBSOCKET_REJECTION in exc_value:
        return True
    return False


def _current_audit_context():
    """The audit context for this request/task, with its user resolved.

    Web requests carry a lazy ``request.user``; resolving it can touch the
    session store and the DB, which may itself fail while an error is being
    handled (a broken transaction, a dead connection). Failure here just means
    an unattributed event, never a lost one.
    """
    try:
        from connect_labs.audit_trail.context import get_audit_context
        from connect_labs.audit_trail.service import resolve_context_user

        ctx = get_audit_context()
        if ctx is None:
            return None
        try:
            resolve_context_user(ctx)
        except Exception:  # noqa: BLE001 - unattributed beats undelivered
            pass
        return ctx
    except Exception:  # noqa: BLE001 - audit_trail unavailable (e.g. early boot)
        return None


def _attribute(event, ctx) -> None:
    """Stamp the acting user and labs scope onto the event."""
    if ctx.user_id is not None:
        # `setdefault`-style: never clobber a user another integration resolved.
        user = event.setdefault("user", {})
        user.setdefault("id", str(ctx.user_id))

    tags = event.setdefault("tags", {})
    if ctx.source:
        tags.setdefault("labs.source", ctx.source)
    if ctx.request_id:
        tags.setdefault("labs.request_id", ctx.request_id)
    # For MCP this is "mcp:<tool_name>" and for Celery the task's dotted path —
    # the single most useful grouping dimension outside a web request.
    path = _scope_path(ctx)
    if path:
        tags.setdefault("labs.path", path)

    labs_context = getattr(getattr(ctx, "request", None), "labs_context", None)
    if isinstance(labs_context, dict):
        for key in ("opportunity_id", "program_id", "organization_id"):
            value = labs_context.get(key)
            if value is not None:
                tags.setdefault(f"labs.{key}", str(value))


def _route_template(request) -> str:
    """The matched URL pattern (``labs/workflow/<int:pk>/``), or ""."""
    route = getattr(getattr(request, "resolver_match", None), "route", None)
    return f"/{route.lstrip('/')}" if route else ""


def _scope_path(ctx) -> str:
    """A grouping path that names *what* ran, never *which record*.

    For MCP (``mcp:<tool>``) and Celery (the task's dotted path) the audit
    context's path is already a name. For a web request it is the concrete
    ``request.path`` — ids, worker usernames — so use the route template, or
    nothing if the request never resolved (a 404, a middleware failure).
    """
    request = getattr(ctx, "request", None)
    if request is not None:
        return _route_template(request)
    return ctx.path or ""


# --- scrubbing --------------------------------------------------------------

FILTERED = "[Filtered]"

# Headers worth keeping on an event: they say what kind of client and request
# it was, and carry nothing about who or what data. Everything else (Cookie,
# Authorization, X-CSRFToken, Referer — a full URL with its query —,
# X-Forwarded-For, custom tokens) is dropped.
_HEADER_ALLOWLIST = frozenset({"user-agent", "content-type", "content-length", "accept", "host"})

# request.env keys that are configuration, not client identity.
_ENV_ALLOWLIST = frozenset({"SERVER_NAME", "SERVER_PORT"})

# ``extra`` keys that name a payload rather than describe a failure.
_PAYLOAD_KEYS = frozenset(
    {
        "args",
        "kwargs",
        "body",
        "data",
        "payload",
        "form",
        "form_json",
        "params",
        "query",
        "query_string",
        "request",
        "response",
        "json",
        "content",
        "rows",
        "record",
        "records",
    }
)

# Span/breadcrumb data keys the SDK fills with a URL's query and fragment.
_QUERY_DATA_KEYS = ("http.query", "http.fragment")

# A URL (absolute, or a path) followed by a query or fragment. The URL part is
# kept; everything from the ``?``/``#`` to the next whitespace or quote goes.
_URL_WITH_QUERY = re.compile(r"((?:[a-zA-Z][a-zA-Z0-9+.-]*://|/)[^\s?#\"'<>]*)[?#][^\s\"'<>]*")


def strip_query(text):
    """Replace the query/fragment of every URL inside ``text`` with [Filtered]."""
    if not isinstance(text, str) or ("?" not in text and "#" not in text):
        return text
    return _URL_WITH_QUERY.sub(lambda m: f"{m.group(1)}?{FILTERED}", text)


def _strip_url(url):
    """A single URL with its query and fragment removed outright."""
    if not isinstance(url, str):
        return url
    return url.split("#", 1)[0].split("?", 1)[0]


def _scrub_request(request: dict, template: str) -> None:
    for key in ("query_string", "data", "cookies"):
        request.pop(key, None)
    if "url" in request:
        url = _strip_url(request["url"])
        if template and isinstance(url, str) and "://" in url:
            scheme, rest = url.split("://", 1)
            url = f"{scheme}://{rest.split('/', 1)[0]}{template}"
        request["url"] = url
    headers = request.get("headers")
    if isinstance(headers, dict):
        request["headers"] = {k: v for k, v in headers.items() if str(k).lower() in _HEADER_ALLOWLIST}
    elif headers is not None:
        request.pop("headers", None)
    env = request.get("env")
    if isinstance(env, dict):
        request["env"] = {k: v for k, v in env.items() if k in _ENV_ALLOWLIST}


def _scrub_extra(extra: dict) -> dict:
    clean = {}
    for key, value in extra.items():
        if key == "celery-job" and isinstance(value, dict):
            # The Celery integration attaches the task's args and kwargs —
            # opportunity ids sometimes, visit/form payloads other times.
            clean[key] = {k: v for k, v in value.items() if k == "task_name"}
        elif str(key).lower() in _PAYLOAD_KEYS:
            clean[key] = FILTERED
        else:
            clean[key] = strip_query(value)
    return clean


def _scrub_frames(container) -> None:
    """Belt and braces with include_local_variables=False: no frame vars."""
    for value in (container or {}).get("values") or []:
        for frame in (value.get("stacktrace") or {}).get("frames") or []:
            frame.pop("vars", None)


def scrub_breadcrumb(crumb):
    """Remove query strings from a breadcrumb's URL, data and message."""
    if not isinstance(crumb, dict):
        return crumb
    data = crumb.get("data")
    if isinstance(data, dict):
        for key in _QUERY_DATA_KEYS:
            data.pop(key, None)
        if "url" in data:
            data["url"] = _strip_url(data["url"])
        for key, value in list(data.items()):
            data[key] = strip_query(value)
    if "message" in crumb:
        crumb["message"] = strip_query(crumb["message"])
    return crumb


def _scrub_span(span: dict) -> None:
    if "description" in span:
        span["description"] = strip_query(span["description"])
    data = span.get("data")
    if isinstance(data, dict):
        for key in _QUERY_DATA_KEYS:
            data.pop(key, None)
        if "url" in data:
            data["url"] = _strip_url(data["url"])


def scrub_event(event, template: str = ""):
    """Strip content from an event or transaction, keeping its shape.

    ``template`` is the route template of the request being handled, when
    known; it replaces the concrete path in ``request.url``.
    """
    request = event.get("request")
    if isinstance(request, dict):
        _scrub_request(request, template)

    user = event.get("user")
    if isinstance(user, dict):
        if user.get("id") is not None:
            event["user"] = {"id": user["id"]}
        else:
            event.pop("user", None)

    extra = event.get("extra")
    if isinstance(extra, dict):
        event["extra"] = _scrub_extra(extra)

    if "message" in event:
        event["message"] = strip_query(event["message"])
    logentry = event.get("logentry")
    if isinstance(logentry, dict):
        for key in ("message", "formatted"):
            if key in logentry:
                logentry[key] = strip_query(logentry[key])
        # Log-call arguments are interpolated values — often the data itself.
        if logentry.get("params"):
            logentry["params"] = []

    exception = event.get("exception")
    if isinstance(exception, dict):
        for value in exception.get("values") or []:
            if "value" in value:
                value["value"] = strip_query(value["value"])
        _scrub_frames(exception)
    _scrub_frames(event.get("threads"))

    breadcrumbs = event.get("breadcrumbs")
    crumbs = breadcrumbs.get("values") if isinstance(breadcrumbs, dict) else breadcrumbs
    for crumb in crumbs or []:
        scrub_breadcrumb(crumb)

    for span in event.get("spans") or []:
        if isinstance(span, dict):
            _scrub_span(span)

    tags = event.get("tags")
    if isinstance(tags, dict):
        for key, value in list(tags.items()):
            tags[key] = strip_query(value)

    return event


def _current_route_template() -> str:
    try:
        ctx = _current_audit_context()
        return _route_template(getattr(ctx, "request", None)) if ctx is not None else ""
    except Exception:  # noqa: BLE001
        return ""


def _scrub_or_drop(event, template: str):
    """Scrub; if scrubbing itself fails, drop the event rather than leak it."""
    try:
        return scrub_event(event, template)
    except Exception:  # noqa: BLE001 - a raw event is the one thing we never send
        logger.warning("sentry scrub failed; event dropped", exc_info=True)
        return None


def before_breadcrumb(crumb, hint):
    """Sentry ``before_breadcrumb`` hook: no query strings in breadcrumbs."""
    try:
        return scrub_breadcrumb(crumb)
    except Exception:  # noqa: BLE001
        return None


def before_send_transaction(event, hint):
    """Sentry ``before_send_transaction`` hook (traces are off by default)."""
    return _scrub_or_drop(event, _current_route_template())


def before_send(event, hint):
    """Sentry ``before_send`` hook: drop known noise, attribute, then scrub."""
    try:
        if _is_noise(event, hint):
            return None
    except Exception:  # noqa: BLE001 - when in doubt, keep the event
        pass

    try:
        ctx = _current_audit_context()
        if ctx is not None:
            _attribute(event, ctx)
    except Exception:  # noqa: BLE001 - enrichment must never drop an event
        pass

    return _scrub_or_drop(event, _current_route_template())
