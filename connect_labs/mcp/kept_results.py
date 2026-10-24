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
  taken for "the person is looking". Any delegated read that is not a verified View counts
  as an agent's, for its clearance, its page and its audit record. A "no user visit
  data" caller never reads one. Every read, and every refused read, is audit-logged (``source=mcp``, the request's
  own id and path, how the token signed in, and what canopy stated).
* **Only for a reader.** ``data_query`` and ``workflow_run_indicators`` keep rows only for
  a caller that reads them back (canopy, or a client passing ``keep_result``); everyone
  else gets them inline and labs keeps no copy. ``targeting_pmc_states`` keeps its
  ranking for every caller: it is public data, and its View reads it on any host. At
  most ``KEPT_RESULT_MAX_BYTES`` (5 MB) of JSON per result. An agent's read is cut as
  its query was (``labsdata.service.shape_for_agent``).
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
#: A public result (``PUBLIC_KINDS``: the PMC ranking, no visit data) lasts a month, so a
#: card drawn from one does not die with the day.
PUBLIC_TTL_SECONDS = 30 * 24 * 60 * 60
#: When ``settings.KEPT_RESULT_MAX_BYTES`` is not set.
DEFAULT_MAX_BYTES = 5 * 1024 * 1024
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


#: Every refusal a read gives, as the MCP error's ``data.code`` (``mount``): what a View
#: decides on, never the words of the message.
REFUSALS = ("bad_uri", "not_found", "expired", "forbidden", "unavailable")
#: Why a read is refused when Connect could not say whether the reader may (``unavailable``).
UNAVAILABLE = "Connect couldn't confirm your access right now; try again"


class Refused(Exception):
    """Why a read is refused. ``code`` (one of ``REFUSALS``): ``bad_uri`` (not a kept
    result's URI), ``not_found``, ``expired``, ``forbidden`` or ``unavailable`` (Connect
    could not answer whether the reader may: still refused, for a reason that will pass).
    ``sensitive``: a ``forbidden`` that is labs' sensitive-data rule. ``str()`` is
    ``"<code>: <why>"``, the MCP error message a client gets; ``data`` the error's
    structured ``data``."""

    def __init__(self, code: str, message: str, *, sensitive: bool = False):
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.sensitive = sensitive

    @property
    def data(self) -> dict:
        return {"code": self.code, **({"sensitive": True} if self.sensitive else {})}


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
    ISO string), so what is stored is exactly what every reader gets back. A payload over
    ``KEPT_RESULT_MAX_BYTES`` is refused (``MCPToolError``), not kept."""
    from django.conf import settings
    from django.utils import timezone

    from connect_labs.audit_trail.context import get_audit_context

    from .models import KeptResult
    from .tool_registry import MCPToolError

    if user is None or getattr(user, "pk", None) is None:
        raise ValueError("a kept result needs its owner: the labs user the call ran as")
    text = json.dumps(payload, default=str)
    limit = int(getattr(settings, "KEPT_RESULT_MAX_BYTES", DEFAULT_MAX_BYTES) or DEFAULT_MAX_BYTES)
    size = len(text.encode())
    if size > limit:
        raise MCPToolError(
            "INVALID_SCHEMA",
            f"This result is {size / 1e6:.1f} MB, more than Labs keeps ({limit / 1e6:.0f} MB): ask for fewer "
            "rows or columns (aggregate, filter, or select fields from form_json instead of all of it).",
            {"reason": "result_too_large", "bytes": size, "max_bytes": limit},
        )
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
        payload=json.loads(text),
        # The audit context's id: the same one this call's MCPAuditLog row, audit-trail
        # events and any error report carry.
        tool_call_id=((ctx.request_id if ctx is not None else "") or "")[:255],
        sensitive=is_sensitive,
        opportunity_ids=opportunity_ids,
        through_canopy=delegated(),
        scope=access,
        created_at=now,
        expires_at=now + timedelta(seconds=PUBLIC_TTL_SECONDS if kind in PUBLIC_KINDS else TTL_SECONDS),
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


#: Column statistics (``column_stats``): how many columns get them, the cap on a
#: distinct count, and how many of a categorical column's values are named.
STATS_MAX_COLUMNS = 40
STATS_DISTINCT_CAP = 1000
STATS_TOP_VALUES = 5


def _value_type(values: list) -> str:
    """A column's type from its non-null values, when the query did not say."""
    if not values:
        return "text"
    if all(isinstance(v, bool) for v in values):
        return "bool"
    if all(isinstance(v, int) and not isinstance(v, bool) for v in values):
        return "integer"
    if all(isinstance(v, int | float) and not isinstance(v, bool) for v in values):
        return "numeric"
    if all(isinstance(v, dict | list) for v in values):
        return "json"
    if all(isinstance(v, str) and len(v) >= 10 and v[4:5] == "-" and v[7:8] == "-" for v in values):
        return "timestamp" if any(len(v) > 10 for v in values) else "date"
    return "text"


def _hashable(v):
    return json.dumps(v, sort_keys=True, default=str) if isinstance(v, dict | list) else v


def column_stats(columns: Sequence, rows: Sequence, column_types: Sequence | None = None, *, reveal: bool) -> list:
    """Per column, what an agent needs to read a result without its rows: ``type``,
    ``nulls``, ``distinct`` (capped at ``STATS_DISTINCT_CAP``, ``distinct_capped`` past
    it), ``min``/``max``/``mean`` for a numeric column, ``min``/``max`` for a date, and
    ``top_values`` for a categorical text column.

    ``reveal`` is the rule every sample follows (``labsdata.service.agent_sees_sensitive_rows``):
    False -- an agent labs has not cleared, over sensitive data -- gives each column's
    name and type only, no counts and no values, as ``data_describe`` gives such an agent
    structure only. Even when True, a text column's values are named only as
    ``data_describe`` names them (``labsdata.fields``): when it holds at most
    ``MAX_CATEGORIES`` distinct values, each seen at least ``MIN_VALUE_COUNT`` times --
    which admits "hospital / home / clinic" and never a name, a phone number or free
    text -- and a text column never gets a min or max, which would be two of its values.
    At most ``STATS_MAX_COLUMNS`` columns, so the facts stay within their budget."""
    from connect_labs.labsdata.fields import MAX_CATEGORIES, MAX_VALUE_LENGTH, MIN_VALUE_COUNT

    names = _column_names(columns)[:STATS_MAX_COLUMNS]
    types = list(column_types or [])
    rows = [r if not isinstance(r, Mapping) else [r.get(n) for n in names] for r in (rows or ())]
    out = []
    for i, name in enumerate(names):
        values = [r[i] if i < len(r) else None for r in rows]
        present = [v for v in values if v is not None]
        kind = types[i] if i < len(types) and types[i] else _value_type(present)
        stat: dict = {"name": name, "type": kind}
        if not reveal:
            out.append(stat)
            continue
        stat["nulls"] = len(values) - len(present)
        counts: dict = {}
        capped = False
        for v in present:
            key = _hashable(v)
            if key in counts or len(counts) < STATS_DISTINCT_CAP:
                counts[key] = counts.get(key, 0) + 1
            else:
                capped = True
        stat["distinct"] = len(counts)
        if capped:
            stat["distinct_capped"] = True
        numbers = [v for v in present if isinstance(v, int | float) and not isinstance(v, bool)]
        if kind in ("integer", "numeric") and numbers:
            stat["min"], stat["max"] = min(numbers), max(numbers)
            stat["mean"] = round(sum(numbers) / len(numbers), 4)
        elif kind in ("date", "timestamp") and present:
            texts = [str(v) for v in present]
            stat["min"], stat["max"] = min(texts), max(texts)
        elif kind == "text" and counts:
            categorical = len(counts) <= MAX_CATEGORIES and min(counts.values()) >= MIN_VALUE_COUNT
            if categorical and all(isinstance(k, str) and len(k) <= MAX_VALUE_LENGTH for k in counts):
                top = sorted(counts.items(), key=lambda kv: (-kv[1], str(kv[0])))[:STATS_TOP_VALUES]
                stat["top_values"] = [{"value": k, "count": c} for k, c in top]
        out.append(stat)
    return out


def facts(columns, rows, column_types=None, *, reveal: bool = True) -> dict:
    """What a canopy agent is told about a table instead of the table: ``summarize_rows``
    (the first ``INLINE_ROWS`` rows), ``column_stats`` and, when that is not every row,
    how to read the rest."""
    out = summarize_rows(columns, rows, sample=INLINE_ROWS)
    out["column_stats"] = column_stats(columns, rows, column_types, reveal=reveal)
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


def read(uri: str, reader: int | None):
    """The kept result ``uri`` names, as ``reader`` (a labs user id) may see it, or a
    named ``Refused``. ``may_read`` decides for everyone, its owner included: an owner who
    has since lost the opportunities a query read no longer reads its rows. It decides
    BEFORE expiry, so someone who may not read a result never learns whether it expired.

    The payload is not loaded (``contents_of`` reads only the page it serves)."""
    from .models import KeptResult

    result_id, _ = parse(uri)
    result = KeptResult.objects.defer("payload").filter(pk=result_id).first()
    if result is None:
        raise Refused("not_found", "there is no such result here")
    verdict = read_verdict(reader, result)
    if verdict == "unavailable":
        raise Refused("unavailable", UNAVAILABLE)
    if verdict is not None:
        raise Refused("forbidden", "this result is not yours to read")
    if _expired(result):
        raise Refused("expired", "this result has expired; run the query again")
    return result


def sort_of(columns, query: Mapping[str, str]) -> tuple[int, str] | None:
    """``(column index, "asc" | "desc")`` the query asks the rows sorted by (``sort``,
    ``dir``), or None for the order the tool gave. ``sort`` must name one of the result's
    ``columns`` and ``dir`` be asc or desc (default asc); anything else is
    ``Refused("bad_uri")``."""
    name = query.get("sort")
    direction = (query.get("dir") or "asc").lower()
    if not name:
        if query.get("dir"):
            raise Refused("bad_uri", "dir needs sort: name the column to sort by")
        return None
    names = _column_names(columns)
    if name not in names:
        raise Refused("bad_uri", f"sort {name[:100]!r} is not one of this result's columns")
    if direction not in ("asc", "desc"):
        raise Refused("bad_uri", "dir must be asc or desc")
    return names.index(name), direction


def _sort_key(value):
    """How a cell sorts: numbers by value, then text, nulls kept apart (always last)."""
    if isinstance(value, bool):
        return (1, "", str(value))
    if isinstance(value, int | float):
        return (0, value, "")
    if isinstance(value, dict | list):
        return (1, "", json.dumps(value, sort_keys=True, default=str))
    return (1, "", str(value))


def sorted_rows(rows: list, order: tuple[int, str] | None) -> list:
    """``rows`` in the ``order`` ``sort_of`` read (stable; nulls last either way)."""
    if order is None:
        return rows
    col, direction = order

    def cell(row):
        return row[col] if isinstance(row, list | tuple) and col < len(row) else None

    present = [r for r in rows if cell(r) is not None]
    nulls = [r for r in rows if cell(r) is None]
    return sorted(present, key=lambda r: _sort_key(cell(r)), reverse=direction == "desc") + nulls


def page(payload, query: Mapping[str, str]) -> tuple[object, dict]:
    """``(body, paging meta)``: a table payload's rows sorted as the query asks (``sort``,
    ``dir``: ``sort_of``) and sliced to the page it asks for (``page``, ``page_size``:
    default 100, at most 1000); anything else whole."""
    if not (isinstance(payload, Mapping) and isinstance(payload.get("rows"), list)):
        return payload, {}
    order = sort_of(payload.get("columns"), query)
    rows = sorted_rows(payload["rows"], order)

    def number(key: str, default: int) -> int:
        try:
            return int(query.get(key) or default)
        except (TypeError, ValueError):
            return default

    size = max(1, min(MAX_PAGE_SIZE, number("page_size", DEFAULT_PAGE_SIZE)))
    pages = max(1, math.ceil(len(rows) / size))
    current = max(1, min(pages, number("page", 1)))
    body = {**payload, "rows": rows[(current - 1) * size : current * size]}
    paging = {"page": current, "page_size": size, "pages": pages, "row_count": len(rows)}
    if order is not None:
        paging["sort"] = {"column": _column_names(payload.get("columns"))[order[0]], "dir": order[1]}
    return body, paging


def contents(uri: str, reader: int | None) -> list[dict]:
    """The ``resources/read`` answer for ``uri``, read as ``reader``: one JSON text
    content, a page of the rows when it is a table, with ``_meta[META_KEY]`` saying what it
    is (kind, tool call, created/expires, ``snapshot``, and the page)."""
    return contents_of(read(uri, reader), uri)


def page_of(result, query: Mapping[str, str]) -> tuple[object, dict]:
    """``page(result.payload, query)``, reading only that page's rows from the database
    when the payload is a table: a page of a 5 MB result moves one page, not the result.
    A sort (``sort_of``) is done there too, in SQL over the stored JSON, before the slice:
    every page is a page of the whole result in that order."""
    from django.db import connection

    if connection.vendor != "postgresql" or "payload" in result.__dict__:
        return page(result.payload, query)
    table = "mcp_kept_result"
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT CASE WHEN jsonb_typeof(payload->'rows') = 'array' THEN jsonb_array_length(payload->'rows') END, "
            f"payload->'columns' FROM {table} WHERE id = %s",
            [result.id],
        )
        count, columns = cursor.fetchone() or (None, None)
    if count is None:
        return page(result.payload, query)
    columns = json.loads(columns) if isinstance(columns, str) else columns
    # The page arithmetic, and the sort's checks, are `page`'s own, over a stand-in.
    _, paging = page({"columns": columns, "rows": [None] * count}, query)
    order = sort_of(columns, query)
    start = (paging["page"] - 1) * paging["page_size"]
    if order is None:
        ordering, params = "i", []
    else:
        # Nulls last whichever way; numbers by value before text (as `sorted_rows`); the
        # stored order breaks ties, so paging is stable. The column is a bound index.
        direction = "DESC" if order[1] == "desc" else "ASC"
        ordering = (
            "(e->%s IS NULL OR e->%s = 'null'::jsonb), "
            f"CASE WHEN jsonb_typeof(e->%s) = 'number' THEN 0 ELSE 1 END {direction}, "
            f"CASE WHEN jsonb_typeof(e->%s) = 'number' THEN (e->>%s)::numeric END {direction}, "
            f"e->>%s {direction}, i"
        )
        params = [order[0]] * 6
    with connection.cursor() as cursor:
        cursor.execute(
            f"SELECT payload - 'rows', COALESCE((SELECT jsonb_agg(e ORDER BY n) FROM ("
            f"SELECT e, row_number() OVER (ORDER BY {ordering}) AS n "
            f"FROM jsonb_array_elements(payload->'rows') WITH ORDINALITY AS t(e, i)) AS s "
            f"WHERE n > %s AND n <= %s), '[]'::jsonb) FROM {table} WHERE id = %s",
            [*params, start, start + paging["page_size"], result.id],
        )
        head, rows = cursor.fetchone()
    head = json.loads(head) if isinstance(head, str) else head
    rows = json.loads(rows) if isinstance(rows, str) else rows
    return {**head, "rows": rows}, paging


def contents_of(result, uri: str, *, for_agent: bool = False, paged=None) -> list[dict]:
    """``contents`` for a result already loaded and judged (``read``). ``for_agent`` cuts
    long cells and the page as an agent's query result is cut (``shape_for_agent``)."""
    _, query = parse(uri)
    body, paging = paged if paged is not None else page_of(result, query)
    if for_agent and paging and isinstance(body, dict):
        body = _shaped(body, paging)
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


class _Unanswered(Exception):
    """Connect (or labs' own copy of what Connect says) could not answer whether a reader
    may run something: not a no, but not a yes either."""


def may_read(reader: int | None, result) -> bool:
    """Could ``reader`` (an active labs user; the owner too) run it themselves?"""
    return read_verdict(reader, result) is None


def read_verdict(reader: int | None, result) -> str | None:
    """None when ``reader`` (an active labs user; the owner too) could run it themselves;
    ``"forbidden"`` when they could not; ``"unavailable"`` when Connect could not say.
    Both refuse. A yes is kept ``ACL_CACHE_SECONDS`` per (reader, result); neither no is."""
    from django.core.cache import cache

    user = _active_user(reader) if reader is not None else None
    if user is None:
        return "forbidden"
    key = f"labs-result-acl:{user.pk}:{result.id}"
    if cache.get(key):
        return None
    try:
        why = _could_run(user, result)
    except Exception:  # noqa: BLE001 -- a check that cannot answer says no, and says why
        logger.info("kept result ACL check could not answer for %s", getattr(result, "kind", ""), exc_info=True)
        return "unavailable"
    if why is None:
        cache.set(key, True, ACL_CACHE_SECONDS)
        return None
    return "forbidden"


def _unanswered(exc: Exception) -> bool:
    """Whether ``exc`` (raised checking a reader) means "could not tell" rather than "no":
    labs could not read the reader's scopes from Connect, Connect's API failed or was
    rate limited, or the reader's Connect sign-in could not be used. A refusal that names
    the reader (not held, not found in their scope, not shared) is a no."""
    from connect_labs.labsdata.scope import ScopeError

    from .tool_registry import MCPToolError

    if isinstance(exc, ScopeError):
        return exc.code == "scopes_unavailable"
    if isinstance(exc, MCPToolError):
        return exc.code in ("UPSTREAM_ERROR", "RATE_LIMITED")
    return True


def _could_run(user, result) -> str | None:
    """Why ``user`` could not have produced ``result`` themselves, or None. Raises
    ``_Unanswered`` when Connect could not say (``_unanswered``)."""
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
            if _unanswered(e):
                raise _Unanswered(str(e) or type(e).__name__) from e
            return str(e) or type(e).__name__
        return None
    if kind == KIND_INDICATORS:
        from .tools.workflow_run import _Run

        try:
            with _Run(user, int(access["run_id"]), access.get("opportunity_id"), access.get("program_id")):
                return None
        except Exception as e:  # noqa: BLE001 -- MCPToolError, no Connect token, ...
            if _unanswered(e):
                raise _Unanswered(str(e) or type(e).__name__) from e
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
    """Why this read of ``uri`` may not carry this result, or None (``read_judgement``)."""
    return read_judgement(result, access_token, uri)[0]


def read_judgement(result, access_token, uri: str = "") -> tuple[str | None, dict]:
    """``(why this read of uri may not carry this result or None, what canopy stated)``.
    Asked of every reader, the owner too.

    The second half is for the audit trail: for a delegated read, ``execution`` (the
    verified ``Canopy-Execution``, or why not), ``view`` when the person's View was asked
    about, and ``agent`` -- true when an agent's statement came with the read, whose
    page is then cut as its query was. The statement is verified once, here, whatever
    the result holds.

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

    claims = getattr(access_token, "claims", None) or {}
    delegated_read = claims.get("auth_method") == "delegated"
    if not result.sensitive and not delegated_read:
        return None, {}
    if result.sensitive and restricted_call(access_token):
        return "this result holds user visit data, which this token may not read", {}
    if not delegated_read:
        return None, {}
    read_uri = uri or f"{SCHEME}://{result.id}"
    call = execution.for_this_request(access_token, rpc=("resources/read", {"uri": read_uri}))
    # A delegated read is the person's own View only when canopy SAYS so, for this result
    # and this person (a verified ``Canopy-View``). Anything else -- an agent's statement,
    # a bad one, none at all -- is an agent's read: cut as an agent's page is, audited as
    # an agent's, and cleared for sensitive data only as an agent is.
    view = None
    if call.execution is None:
        view = execution.view_for_this_request(access_token, uri=read_uri, subject=_subject(access_token))
    is_view = view is not None and view.view is not None and _names(view.view.uri, result)
    stated = {"execution": call.audit(), "agent": not is_view}
    if view is not None:
        stated["view"] = view.audit()
    if not result.sensitive:
        return None, stated
    from connect_labs.labs import canopy_panel

    with execution.bound(call):
        clearance = canopy_panel.call_sensitive_data_clearance()
    if clearance.granted:
        return None, stated
    if is_view and result.through_canopy:
        # canopy's word that the person is looking admits it -- never the absence of a statement.
        return None, stated
    if view is not None:
        why = f"{clearance.reason}; not the person's own View ({view.refused or 'another result'})"
    else:
        why = clearance.reason
    return (
        "this result holds sensitive data, which reaches an agent through canopy only when Labs has cleared it, "
        f"and the person's View only when canopy says it is theirs ({why}); open it on the Data page instead"
    ), stated


def _shaped(body: dict, paging: dict) -> dict:
    """A page as an agent reads it: long cells cut and the page capped, with the notes the
    agent's query result carries (``labsdata.service.shape_for_agent``)."""
    from connect_labs.labsdata import service

    # The row-cap note was the query's to give, once: never again on every page.
    shaped = service.shape_for_agent({**body, "row_count": paging.get("row_count"), "truncated": False})
    shaped.pop("rows_returned", None)
    for key in ("row_count", "truncated"):
        if key in body:
            shaped[key] = body[key]
        else:
            shaped.pop(key, None)
    return shaped


def token_facts(access_token) -> dict:
    """What the audit trail records about the credential behind a read: how it signed in
    and the client (canopy's client id for a delegated grant). Never the token."""
    claims = getattr(access_token, "claims", None) or {}
    out = {"auth_method": str(claims.get("auth_method") or "")}
    client = getattr(access_token, "client_id", None)
    if client:
        out["client_id"] = str(client)[:255]
    return out


def record_read(
    result, reader_user, uri: str, *, facts: dict | None = None, count: int | None = None, paging: dict | None = None
) -> None:
    """Audit-trail a read of a kept result, like the read that made it. Never raises."""
    from connect_labs.audit_trail.models import Action
    from connect_labs.audit_trail.service import record

    access = result.access
    if paging is None:
        try:
            _, query = parse(uri)
            body, paging = page_of(result, query)
            count = len(body.get("rows") or []) if paging and isinstance(body, dict) else None
        except Exception:  # noqa: BLE001
            paging = {}
    opps = [o for o in (access.get("opportunity_ids") or []) if o is not None] or [None]
    for oid in opps:
        record(
            Action.READ,
            resource_type=RESOURCE_TYPE,
            resource_id=result.id,
            opportunity_id=oid,
            program_id=access.get("program_id"),
            record_count=count,
            metadata={"kind": result.kind, "owner": str(result.owner_id), **paging, **(facts or {})},
            user=reader_user,
        )


def record_refused(result, reader_user, uri: str, refused: Refused, *, facts: dict | None = None) -> None:
    """Audit-trail a refused read (``outcome`` failure): who asked, for what, and why. Never raises."""
    from connect_labs.audit_trail.models import Action, Outcome
    from connect_labs.audit_trail.service import record

    try:
        result_id = parse(uri)[0]
    except Refused:
        result_id = str(uri or "")[:100]
    access = getattr(result, "access", None) or {}
    opps = [o for o in (access.get("opportunity_ids") or []) if o is not None] or [None]
    for oid in opps:
        record(
            Action.READ,
            resource_type=RESOURCE_TYPE,
            resource_id=result_id,
            opportunity_id=oid,
            program_id=access.get("program_id"),
            outcome=Outcome.FAILURE,
            metadata={"refused": refused.code, "why": refused.message[:300], **(facts or {})},
            user=reader_user,
        )


def _access_only(uri: str):
    """The kept result ``uri`` names with only what an audit record needs (its id, kind
    and ``access``: opportunities, programme), never its payload; None when there is none."""
    from .models import KeptResult

    try:
        result_id, _ = parse(uri)
    except Refused:
        return None
    try:
        return (
            KeptResult.objects.only("id", "kind", "opportunity_ids", "scope", "sensitive", "through_canopy")
            .filter(pk=result_id)
            .first()
        )
    except Exception:  # noqa: BLE001 -- the audit record must not fail the refusal
        return None


def read_as(uri: str, reader: int | None, access_token) -> list[dict]:
    """A whole ``resources/read`` of a kept result, as ``reader``: the owner / ``may_read``
    / expiry check, labs' ``read_denial`` for this token, the page (cut for an agent), and
    the audit record -- of the read, or of its refusal."""
    reader_user = _active_user(reader) if reader is not None else None
    facts = token_facts(access_token)
    result = None
    try:
        result = read(uri, reader)
        denied = scope_denial(result, access_token)
        stated: dict = {}
        sensitive_rule = False
        if not denied:
            denied, stated = read_judgement(result, access_token, uri)
            sensitive_rule = bool(denied)  # read_judgement refuses only a sensitive result
        facts.update(stated)
        if denied:
            raise Refused("forbidden", denied, sensitive=sensitive_rule)
        _, query = parse(uri)
        body, paging = page_of(result, query)
        count = len(body.get("rows") or []) if paging and isinstance(body, dict) else None
        out = contents_of(result, uri, for_agent=bool(stated.get("agent")), paged=(body, paging))
    except Refused as exc:
        # A refusal before the result was judged (not hers to read, expired, Connect
        # unavailable) still says which opportunities and programme it was for.
        record_refused(result if result is not None else _access_only(uri), reader_user, uri, exc, facts=facts)
        raise
    record_read(result, reader_user, uri, facts=facts, count=count, paging=paging)
    return out


def sweep() -> int:
    """Delete expired results; how many went."""
    from django.utils import timezone

    from .models import KeptResult

    deleted, _ = KeptResult.objects.filter(expires_at__lte=timezone.now()).delete()
    return deleted


# --- the MCP resource -----------------------------------------------------------------


def read_resource(uri: str, reader: int | None, access_token) -> list[dict]:
    """``read_as`` as an MCP ``resources/read`` runs it: in its own audit context
    (``source=mcp``, a request id, ``path`` ``mcp:resources/read``), as a tool call is, so
    the read's audit events say who, from where and through what."""
    import uuid

    from connect_labs.audit_trail.context import audit_context

    user = _active_user(reader) if reader is not None else None
    with audit_context(user=user, source="mcp", request_id=f"mcp:{uuid.uuid4().hex}", path="mcp:resources/read"):
        return read_as(uri, reader, access_token)


#: The JSON-RPC error code of each refusal: MCP's "resource not found" (-32002) for a
#: result that is not there, invalid params for a URI that is not one; the rest are
#: refusals of a resource that exists.
INTERNAL_ERROR = -32603
_ERROR_CODES = {"not_found": -32002, "bad_uri": -32602}


def mount(server) -> object:
    """Register ``labs-result://{id}{?page,page_size,sort,dir}`` on the FastMCP server: answers
    ``resources/read`` as the reader (``read_as``), each refusal an MCP error whose message
    is ``"<code>: <why>"`` and whose ``data`` is ``{"code": <one of REFUSALS>}`` (plus
    ``"sensitive": true`` for labs' sensitive-data rule); also listed by
    ``resources/templates/list``."""
    from asgiref.sync import sync_to_async
    from fastmcp.resources import ResourceContent, ResourceResult
    from mcp.shared.exceptions import MCPError

    from .server import _closing_connections

    async def labs_result(id: str, page: str = "", page_size: str = "", sort: str = "", dir: str = ""):
        from urllib.parse import urlencode

        query = urlencode(
            [(k, v) for k, v in (("page", page), ("page_size", page_size), ("sort", sort), ("dir", dir)) if v]
        )
        uri = f"{SCHEME}://{id}" + (f"?{query}" if query else "")
        # Read on the event loop: the request's token, before the work moves to a thread.
        access_token = _access_token()
        reader = reader_id()
        try:
            out = await sync_to_async(_closing_connections(read_resource), thread_sensitive=True)(
                uri, reader, access_token
            )
        except Refused as exc:
            # The refusal's code is structured (`data.code`) for a View to decide on; the
            # message keeps "<code>: <why>" for a host that passes on only the message.
            raise MCPError(_ERROR_CODES.get(exc.code, INTERNAL_ERROR), str(exc), exc.data) from None
        return ResourceResult(
            contents=[ResourceContent(content=c["text"], mime_type=c["mimeType"], meta=c.get("_meta")) for c in out]
        )

    return server.resource(
        f"{SCHEME}://{{id}}{{?page,page_size,sort,dir}}",
        name="labs_results",
        mime_type=MIME_TYPE,
        description=(
            "A result a labs tool produced (a query's rows, a run's indicators), kept for a day for the person "
            f"it ran as. A table pages with ?page=N&page_size=M (default {DEFAULT_PAGE_SIZE}, max {MAX_PAGE_SIZE}), "
            "and sorts the whole result before paging with &sort=<column>&dir=asc|desc."
        ),
    )(labs_result)
