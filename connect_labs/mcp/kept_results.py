"""Kept results: a tool returns a summary and a ``labs-result://`` link to what it produced.

A tool that produces rows (``data_query``, ``workflow_run_indicators``) or a result a
View draws (``targeting_pmc_states``) keeps it here (``models.KeptResult``), for the
person the call ran as, for a day, and returns an MCP ``resource_link`` naming it beside
a short summary. Any MCP client -- Claude Code, ACE, canopy -- reads it back with
``resources/read``, as itself, a page at a time
(``labs-result://<id>?page=2&page_size=100``); the result Views do the same. canopy is
one client: it carries the link and never the rows, so ``site.json`` declares the
``labs-result`` scheme (``labs/canopy.py:host_settings``) and canopy keeps the links in
a private conversation.

* **Who owns it.** The labs user the call ran as (every MCP token here, a PAT, an OAuth
  sign-in or canopy's delegated grant, resolves to one).
* **Who may read it** (``may_read``, the owner included): an active labs user who could
  run the same thing themselves -- holds every opportunity a query read, can open the run
  its indicators came from -- now, not when it was made (a yes is cached a minute). Public
  results (``targeting_pmc_states``) any signed-in labs user may read. A token reads back
  only what a tool in its scope keeps (``scope_denial``).
* **What a read may carry** (``read_denial``, asked of EVERY reader, the owner
  included): a result holding labs' sensitive data (rows from production visits) goes
  back through canopy only where the call that read it could: an agent whose
  ``Canopy-Execution`` statement clears it for sensitive data, or the person's own View
  -- which canopy STATES with a signed ``Canopy-View`` for this resource and this person
  (``execution.view_for_this_request``) -- of a result a cleared agent already received
  through canopy. A delegated read with no statement is refused: an absence is never
  taken for "the person is looking". A "no user visit data" caller never reads one.
  Every read is audit-logged.
* **Snapshot, not fresh.** A read serves what the tool produced: the View and the agent
  must agree on the rows the agent reasoned on. Paging is over that snapshot. Labs never
  changes a kept result, so it never sends canopy a "result changed" push.

How much a model gets inline is ``INLINE_ROWS``: see ``facts``.
"""

from __future__ import annotations

import json
import logging
import math
import secrets
from collections.abc import Mapping, Sequence
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

logger = logging.getLogger(__name__)

#: The URI scheme of a kept result. ``site.json`` declares it (``RESOURCE_SCHEMES``).
SCHEME = "labs-result"
#: A day: long enough to come back to a conversation, short enough that a result does not
#: become a standing copy of production rows.
TTL_SECONDS = 24 * 60 * 60
MIME_TYPE = "application/json"

#: Rows per page of a table result, unless the reader asks for another size.
DEFAULT_PAGE_SIZE = 100
MAX_PAGE_SIZE = 1000
#: The ``_meta`` key a read's content carries about the result (kind, expiry, paging).
META_KEY = "labs/result"

#: How many rows a canopy agent gets inline with the facts. A result this short (most
#: aggregates, which labs' guidance asks for) reaches the model whole and needs no read;
#: a longer one reaches it as a sample of this many, with the link to page through the
#: rest (``site_read``). Twenty rows of a GROUP BY is an answer; twenty rows of a
#: visit-level SELECT is enough to see its shape without filling the context.
INLINE_ROWS = 20

#: Audit-trail resource type for a read of a kept result.
RESOURCE_TYPE = "labs_result"

#: What each kind of result is, for its ``name`` and the read rules below.
KIND_QUERY = "data_query"
KIND_INDICATORS = "workflow_run_indicators"
KIND_PMC = "pmc_states"
PUBLIC_KINDS = frozenset({KIND_PMC})

_ID_BYTES = 18


class Refused(Exception):
    """Why a read is refused. ``code``: ``bad_uri`` (not a kept result's URI),
    ``not_found``, ``expired`` or ``forbidden``. ``str()`` is ``"<code>: <why>"``, the
    MCP error message a client gets."""

    def __init__(self, code: str, message: str):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


# --- who is asking --------------------------------------------------------------------


def _access_token():
    try:
        from . import server

        # Through ``server`` so the in-process test bridge (testing.py), which patches
        # that binding, is seen here as well.
        return server.get_access_token()
    except Exception:  # noqa: BLE001 -- no MCP request in flight
        return None


def reader_id() -> int | None:
    """The labs user this MCP request runs as, by its token's claims; None outside one.

    No database, so it can be read on the event loop."""
    claims = getattr(_access_token(), "claims", None) or {}
    uid = claims.get("user_id")
    if uid is None:
        uid = claims.get("sub")
    try:
        return int(uid)
    except (TypeError, ValueError):
        return None


def delegated() -> bool:
    """True when canopy is calling as a visitor (the delegated grant)."""
    claims = getattr(_access_token(), "claims", None) or {}
    return claims.get("auth_method") == "delegated"


def _active_user(user_id):
    from connect_labs.users.models import User

    try:
        return User.objects.get(pk=int(user_id), is_active=True)
    except (TypeError, ValueError, User.DoesNotExist):
        return None


# --- writing --------------------------------------------------------------------------


def sensitive(opportunity_ids) -> bool:
    """Whether rows read from these opportunities are labs' sensitive data (not all generated)."""
    from connect_labs.labs.synthetic.provenance import all_generated

    ids = [i for i in (opportunity_ids or []) if i is not None]
    return not all_generated(ids)


def resource_link(uri: str, name: str, *, description: str = "", mime_type: str = MIME_TYPE) -> dict:
    """An MCP ``resource_link`` content item (MCP 2025-06-18)."""
    link = {"type": "resource_link", "uri": uri, "name": name, "mimeType": mime_type}
    if description:
        link["description"] = description
    return link


def save(user, kind: str, payload: dict, *, access: dict, name: str, description: str = "") -> dict:
    """Keep ``payload`` for ``user`` and return the ``resource_link`` naming it.

    ``access`` is what the read rules need: ``opportunity_ids``, ``sensitive`` and
    anything else a rule asks (a run's ``run_id``, ``opportunity_id``, ``program_id``).
    Whether this call was canopy's is recorded too: a sensitive result made through
    canopy went to an agent labs had cleared, which is what lets the person's own View
    read it back (``read_denial``). The payload is stored as JSON holds it (a date as its
    ISO string), so what is stored is exactly what every reader gets back."""
    from django.utils import timezone

    from connect_labs.audit_trail.context import get_audit_context

    from .models import KeptResult

    if user is None or getattr(user, "pk", None) is None:
        raise ValueError("a kept result needs its owner: the labs user the call ran as")
    access = dict(access or {})
    opportunity_ids = [i for i in (access.pop("opportunity_ids", None) or [])]
    is_sensitive = bool(access.pop("sensitive", True))
    ctx = get_audit_context()
    now = timezone.now()
    row = KeptResult.objects.create(
        id=secrets.token_urlsafe(_ID_BYTES),
        owner=user,
        kind=str(kind)[:100],
        name=str(name or kind or "result")[:200],
        description=str(description or "")[:500],
        payload=json.loads(json.dumps(payload, default=str)),
        # The audit context's id: the same one this call's MCPAuditLog row, audit-trail
        # events and any error report carry.
        tool_call_id=((ctx.request_id if ctx is not None else "") or "")[:255],
        sensitive=is_sensitive,
        opportunity_ids=opportunity_ids,
        through_canopy=delegated(),
        scope=access,
        created_at=now,
        expires_at=now + timedelta(seconds=TTL_SECONDS),
    )
    return resource_link(f"{SCHEME}://{row.id}", row.name, description=row.description, mime_type=row.mime_type)


def with_link(result: dict, link: dict) -> dict:
    """``result`` with its link: ``result_uri`` for a reader of the JSON, and ``_links``,
    which the server (``_run_registry_tool_inner``) puts in the content as ``resource_link``
    items and strips from what the tool returns."""
    return {**result, "result_uri": link["uri"], "_links": [link]}


def _column_names(columns: Sequence) -> list[str]:
    out = []
    for c in columns or ():
        if isinstance(c, Mapping):
            out.append(str(c.get("name") or c.get("key") or c.get("id") or ""))
        else:
            out.append(str(c))
    return out


def summarize_rows(columns: Sequence, rows: Sequence, sample: int = 5) -> dict:
    """What a model is told about a table instead of the table: how many rows, the
    columns, the first ``sample`` rows as dicts by column name, and ``truncated`` when
    that sample is not every row.

    ``columns`` are names or ``{"name": ...}`` mappings; ``rows`` are sequences in column
    order or mappings by name."""
    names = _column_names(columns)
    rows = list(rows or ())
    take = max(0, int(sample))

    def as_dict(row) -> dict:
        if isinstance(row, Mapping):
            return {n: row.get(n) for n in names} if names else dict(row)
        return dict(zip(names, row))

    return {
        "row_count": len(rows),
        "columns": names,
        "sample_rows": [as_dict(r) for r in rows[:take]],
        "truncated": len(rows) > take,
    }


def facts(columns, rows) -> dict:
    """What a canopy agent is told about a table instead of the table: ``summarize_rows``
    (the first ``INLINE_ROWS`` rows) and, when that is not every row, how to read the rest."""
    out = summarize_rows(columns, rows, sample=INLINE_ROWS)
    if out["truncated"]:
        out["read_more"] = (
            f"Only the first {INLINE_ROWS} of {out['row_count']} rows are here. Every row is kept at "
            "`result_uri`: read it with site_read, a page at a time (append ?page=2, ?page_size=up to 1000), "
            "rather than asking again."
        )
    return out


# --- reading --------------------------------------------------------------------------


def parse(uri: str) -> tuple[str, dict[str, str]]:
    """``(id, query)`` of a kept result's URI, or ``Refused("bad_uri")``."""
    parts = urlsplit(str(uri or ""))
    if parts.scheme.lower() != SCHEME or not parts.netloc or parts.path not in ("", "/"):
        raise Refused("bad_uri", f"{str(uri)[:100]!r} is not one of Labs' results")
    return parts.netloc, {k: v[-1] for k, v in parse_qs(parts.query).items()}


def _expired(result) -> bool:
    from django.utils import timezone

    return result.expires_at <= timezone.now()


def stored(uri: str):
    """The live kept result ``uri`` names (any page of it), or None for one that is not
    labs', unknown or expired."""
    from .models import KeptResult

    try:
        result_id, _ = parse(uri)
    except Refused:
        return None
    result = KeptResult.objects.filter(pk=result_id).first()
    if result is None or _expired(result):
        return None
    return result


def read(uri: str, reader: int | None):
    """The kept result ``uri`` names, as ``reader`` (a labs user id) may see it, or a
    named ``Refused``. ``may_read`` decides for everyone, its owner included: an owner who
    has since lost the opportunities a query read no longer reads its rows."""
    from .models import KeptResult

    result_id, _ = parse(uri)
    result = KeptResult.objects.filter(pk=result_id).first()
    if result is None:
        raise Refused("not_found", "there is no such result here")
    if _expired(result):
        raise Refused("expired", "this result has expired; run the query again")
    if not may_read(reader, result):
        raise Refused("forbidden", "this result is not yours to read")
    return result


def page(payload, query: Mapping[str, str]) -> tuple[object, dict]:
    """``(body, paging meta)``: a table payload's rows sliced to the page the query asks
    for (``page``, ``page_size``: default 100, at most 1000); anything else whole."""
    if not (isinstance(payload, Mapping) and isinstance(payload.get("rows"), list)):
        return payload, {}
    rows = payload["rows"]

    def number(key: str, default: int) -> int:
        try:
            return int(query.get(key) or default)
        except (TypeError, ValueError):
            return default

    size = max(1, min(MAX_PAGE_SIZE, number("page_size", DEFAULT_PAGE_SIZE)))
    pages = max(1, math.ceil(len(rows) / size))
    current = max(1, min(pages, number("page", 1)))
    body = {**payload, "rows": rows[(current - 1) * size : current * size]}
    return body, {"page": current, "page_size": size, "pages": pages, "row_count": len(rows)}


def contents(uri: str, reader: int | None) -> list[dict]:
    """The ``resources/read`` answer for ``uri``, read as ``reader``: one JSON text
    content, a page of the rows when it is a table, with ``_meta[META_KEY]`` saying what it
    is (kind, tool call, created/expires, ``snapshot``, and the page)."""
    return contents_of(read(uri, reader), uri)


def contents_of(result, uri: str) -> list[dict]:
    """``contents`` for a result already loaded and judged (``read``)."""
    _, query = parse(uri)
    body, paging = page(result.payload, query)
    meta = {
        "kind": result.kind,
        "tool_call_id": result.tool_call_id,
        "created_at": result.created_at.isoformat(),
        "expires_at": result.expires_at.isoformat(),
        "snapshot": True,
        **paging,
    }
    text = body if isinstance(body, str) else json.dumps(body, default=str)
    return [{"uri": uri, "mimeType": result.mime_type, "text": text, "_meta": {META_KEY: meta}}]


#: How long a reader's "could run it" answer is trusted (``may_read``): a View pages
#: through a result in quick reads, and each check may ask Connect.
ACL_CACHE_SECONDS = 60


def may_read(reader: int | None, result) -> bool:
    """Could ``reader`` (an active labs user; the owner too) run it themselves? A yes is
    kept ``ACL_CACHE_SECONDS`` per (reader, result); a no is asked again."""
    from django.core.cache import cache

    user = _active_user(reader) if reader is not None else None
    if user is None:
        return False
    key = f"labs-result-acl:{user.pk}:{result.id}"
    if cache.get(key):
        return True
    try:
        ok = _could_run(user, result) is None
    except Exception:  # noqa: BLE001 -- a check that cannot answer says no
        logger.info("kept result ACL check failed for %s", getattr(result, "kind", ""), exc_info=True)
        return False
    if ok:
        cache.set(key, True, ACL_CACHE_SECONDS)
    return ok


def _could_run(user, result) -> str | None:
    """Why ``user`` could not have produced ``result`` themselves, or None."""
    access = result.access
    kind = result.kind
    if kind in PUBLIC_KINDS:
        return None
    if kind == KIND_QUERY:
        from connect_labs.labs.access.scopes import Caller
        from connect_labs.labs.connect_tokens import get_valid_access_token
        from connect_labs.labsdata import scope

        try:
            scope.resolve(Caller(user=user, access_token=get_valid_access_token(user)), access["opportunity_ids"])
        except Exception as e:  # noqa: BLE001 -- ScopeError, no Connect token, ...
            return str(e) or type(e).__name__
        return None
    if kind == KIND_INDICATORS:
        from .tools.workflow_run import _Run

        try:
            with _Run(user, int(access["run_id"]), access.get("opportunity_id"), access.get("program_id")):
                return None
        except Exception as e:  # noqa: BLE001 -- MCPToolError, no Connect token, ...
            return str(e) or type(e).__name__
    return f"no read rule for {kind!r} results"


#: The tool that keeps each kind of result: a token reads a result back only when it may
#: call that tool (``scope_denial``).
KIND_TOOLS = {KIND_QUERY: "data_query", KIND_INDICATORS: "workflow_run_indicators", KIND_PMC: "targeting_pmc_states"}


def scope_denial(result, access_token) -> str | None:
    """Why this token's tool scope does not reach this result, or None. A token -- a
    delegated grant's, a restricted PAT -- reads back only what a tool it may call keeps
    (``server.allowed_tools``); a full PAT or sign-in reaches every tool."""
    from .server import allowed_tools

    permitted = allowed_tools(access_token)
    if permitted is None:
        return None
    tool = KIND_TOOLS.get(result.kind)
    if tool is None or tool not in permitted:
        return f"this token may not call {tool or result.kind}, so it may not read what it kept"
    return None


def _subject(access_token) -> str:
    """The labs user a token serves, as canopy names them in a statement (the delegated
    grant's subject: the user's id)."""
    claims = getattr(access_token, "claims", None) or {}
    uid = claims.get("user_id")
    if uid is None:
        uid = claims.get("sub")
    return "" if uid is None else str(uid)


def _names(uri: str, result) -> bool:
    try:
        return parse(uri)[0] == result.id
    except Refused:
        return False


def read_denial(result, access_token, uri: str = "") -> str | None:
    """Why this read of ``uri`` may not carry this result, or None. Asked of every reader,
    the owner too.

    Only a result holding sensitive data is judged. A restricted ("no user visit data")
    call never reads one. A delegated call (canopy) reads one only when canopy says who is
    reading, positively: its ``Canopy-Execution`` statement clears an agent for sensitive
    data (an agent's ``site_read``), or its ``Canopy-View`` statement says this is the
    person's own View, for THIS result and THIS person, and the result already went
    through canopy to a cleared agent. No statement, or one that does not verify (bad,
    expired, replayed, for another resource or person), is refused, as the call that made
    it would have been."""
    from . import execution
    from .server import restricted_call

    if not result.sensitive:
        return None
    if restricted_call(access_token):
        return "this result holds user visit data, which this token may not read"
    claims = getattr(access_token, "claims", None) or {}
    if claims.get("auth_method") != "delegated":
        return None
    from connect_labs.labs import canopy_panel

    read_uri = uri or f"{SCHEME}://{result.id}"
    call = execution.for_this_request(access_token, rpc=("resources/read", {"uri": read_uri}))
    with execution.bound(call):
        clearance = canopy_panel.call_sensitive_data_clearance()
    if clearance.granted:
        return None
    if call.execution is None and result.through_canopy:
        # No agent's statement verified: only canopy's word that the person is looking
        # admits it -- never the absence of a statement.
        view = execution.view_for_this_request(
            access_token, uri=uri or f"{SCHEME}://{result.id}", subject=_subject(access_token)
        )
        if view.view is not None and _names(view.view.uri, result):
            return None
        why = f"{clearance.reason}; not the person's own View ({view.refused or 'another result'})"
    else:
        why = clearance.reason
    return (
        "this result holds sensitive data, which reaches an agent through canopy only when Labs has cleared it, "
        f"and the person's View only when canopy says it is theirs ({why}); open it on the Data page instead"
    )


def record_read(result, reader_user, uri: str) -> None:
    """Audit-trail a read of a kept result, like the read that made it. Never raises."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record

    access = result.access
    try:
        _, query = parse(uri)
        body, paging = page(result.payload, query)
    except Exception:  # noqa: BLE001
        body, paging = None, {}
    count = len(body.get("rows") or []) if paging and isinstance(body, dict) else None
    opps = [o for o in (access.get("opportunity_ids") or []) if o is not None] or [None]
    for oid in opps:
        record(
            Action.READ,
            resource_type=RESOURCE_TYPE,
            resource_id=result.id,
            opportunity_id=oid,
            program_id=access.get("program_id"),
            record_count=count,
            metadata={"kind": result.kind, "owner": str(result.owner_id), **paging},
            user=reader_user,
        )


def read_as(uri: str, reader: int | None, access_token) -> list[dict]:
    """A whole ``resources/read`` of a kept result, as ``reader``: the owner / ``may_read``
    / expiry check, labs' ``read_denial`` for this token, the page, and the audit record."""
    result = read(uri, reader)
    denied = scope_denial(result, access_token) or read_denial(result, access_token, uri)
    if denied:
        raise Refused("forbidden", denied)
    out = contents_of(result, uri)
    record_read(result, _active_user(reader) if reader is not None else None, uri)
    return out


def sweep() -> int:
    """Delete expired results; how many went."""
    from django.utils import timezone

    from .models import KeptResult

    deleted, _ = KeptResult.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted


# --- the MCP resource -----------------------------------------------------------------


def mount(server) -> object:
    """Register ``labs-result://{id}{?page,page_size}`` on the FastMCP server: answers
    ``resources/read`` as the reader (``read_as``), each refusal an MCP error whose message
    is ``"<code>: <why>"``; also listed by ``resources/templates/list``."""
    from asgiref.sync import sync_to_async
    from fastmcp.exceptions import ResourceError
    from fastmcp.resources import ResourceContent, ResourceResult

    from .server import _closing_connections

    async def labs_result(id: str, page: str = "", page_size: str = ""):
        query = "&".join(f"{k}={v}" for k, v in (("page", page), ("page_size", page_size)) if v)
        uri = f"{SCHEME}://{id}" + (f"?{query}" if query else "")
        # Read on the event loop: the request's token, before the work moves to a thread.
        access_token = _access_token()
        reader = reader_id()
        try:
            out = await sync_to_async(_closing_connections(read_as), thread_sensitive=True)(uri, reader, access_token)
        except Refused as exc:
            raise ResourceError(str(exc)) from None
        return ResourceResult(
            contents=[ResourceContent(content=c["text"], mime_type=c["mimeType"], meta=c.get("_meta")) for c in out]
        )

    return server.resource(
        f"{SCHEME}://{{id}}{{?page,page_size}}",
        name="labs_results",
        mime_type=MIME_TYPE,
        description=(
            "A result a labs tool produced (a query's rows, a run's indicators), kept for a day for the person "
            f"it ran as. A table pages with ?page=N&page_size=M (default {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE})."
        ),
    )(labs_result)
