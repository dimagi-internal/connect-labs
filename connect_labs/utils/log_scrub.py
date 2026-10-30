"""Logging filter: outbound-request log lines without query strings or record ids.

httpx logs every request at INFO (`HTTP Request: GET <url> "HTTP/1.1 200 OK"`),
and the web/worker log groups keep those lines for 731 days. The URL's query
string carries FLW usernames, entity/case ids and search terms (`?username=`,
`?case_id=`), so the line keeps its debugging value -- method, host, path shape,
status, and the count of calls (docs/PERFORMANCE_RUNBOOK.md counts them) -- but
the query is dropped and id-like path segments become `{id}`.

Dropping the query also dropped the one signal tools/kill_stampede_triage.py
used to see the RawVisitCache stampede (#1361): `page_size=2500` paginated
fetches. So when a request had a query, the line gets a short suffix of
NON-identifying facts about it, derived before the query is discarded:

    ... "HTTP/1.1 200 OK" [query: page_size=2500 paginated=true has_cursor=true]

`page_size` is a configured page length (an integer, never a data value).
`has_cursor` says only whether a resume cursor (`last_id`, `cursor`, ...) was
present -- never its value, which is a record id. No other query key or value
is ever logged. The same facts ride on the record as attributes
(`outbound_page_size`, `outbound_paginated`, `outbound_has_cursor`) for any
structured handler.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import parse_qsl, urlsplit

_ID_SEGMENT = re.compile(
    r"^(?:\d+"  # integer ids
    r"|[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"  # uuids
    r"|[0-9a-fA-F]{24,}"  # long hex (CommCare doc ids)
    r"|[A-Za-z0-9_-]{32,})$"  # tokens
)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")

# Query keys whose PRESENCE (never value) means "resume from a position".
_CURSOR_KEYS = frozenset({"last_id", "cursor", "after", "before", "offset", "next", "page_token"})
_PAGE_SIZE_KEYS = ("page_size", "limit")
# A page length is a small config integer; anything else is not reported.
_PAGE_SIZE_VALUE = re.compile(r"^\d{1,6}$")


def query_facts(url) -> dict | None:
    """Non-identifying facts about a URL's query, or None if it has no query.

    Returns only: page_size (int, if a page_size/limit param is a plain small
    integer), paginated (bool) and has_cursor (bool -- presence, not value).
    """
    try:
        query = urlsplit(str(url)).query
    except ValueError:
        return None
    if not query:
        return None
    pairs = parse_qsl(query, keep_blank_values=True)
    keys = {k for k, _ in pairs}
    page_size = None
    for key in _PAGE_SIZE_KEYS:
        value = next((v for k, v in pairs if k == key), None)
        if value is not None and _PAGE_SIZE_VALUE.match(value):
            page_size = int(value)
            break
    has_cursor = bool(keys & _CURSOR_KEYS)
    facts = {"paginated": page_size is not None or has_cursor, "has_cursor": has_cursor}
    if page_size is not None:
        facts = {"page_size": page_size, **facts}
    return facts


def format_query_facts(facts: dict) -> str:
    return "[query: " + " ".join(f"{k}={str(v).lower()}" for k, v in facts.items()) + "]"


def scrub_url(url: str) -> str:
    try:
        parts = urlsplit(str(url))
    except ValueError:
        return "<unparseable url>"
    if not parts.scheme:
        return str(url).split("?", 1)[0]
    path = "/".join("{id}" if _ID_SEGMENT.match(seg) else seg for seg in parts.path.split("/"))
    marker = "?…" if parts.query else ""
    return f"{parts.scheme}://{parts.hostname or ''}{path}{marker}"


def scrub_urls_in_text(text: str) -> str:
    return _URL_IN_TEXT.sub(lambda m: scrub_url(m.group(0)), text)


class OutboundUrlScrubFilter(logging.Filter):
    """Rewrites URLs in httpx/httpcore/urllib3/requests records; passes everything."""

    PREFIXES = ("httpx", "httpcore", "urllib3", "requests")

    def filter(self, record: logging.LogRecord) -> bool:
        if not record.name.startswith(self.PREFIXES):
            return True
        if getattr(record, "_outbound_scrubbed", False):
            return True
        record._outbound_scrubbed = True
        facts = _first_query_facts(record)
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = tuple(scrub_urls_in_text(a) if isinstance(a, str) else _maybe_url(a) for a in args)
        if isinstance(record.msg, str):
            record.msg = scrub_urls_in_text(record.msg)
            if facts:
                # Literal text appended to the format string: no '%' can appear in it.
                record.msg = f"{record.msg} {format_query_facts(facts)}"
        if facts:
            record.outbound_page_size = facts.get("page_size")
            record.outbound_paginated = facts["paginated"]
            record.outbound_has_cursor = facts["has_cursor"]
        return True


def _first_query_facts(record: logging.LogRecord) -> dict | None:
    """Facts for the first URL in the record (httpx logs one URL per line)."""
    args = record.args if isinstance(record.args, tuple) else ((record.args,) if record.args else ())
    for candidate in (*args, record.msg):
        if type(candidate).__name__ == "URL":
            return query_facts(str(candidate))
        if isinstance(candidate, str):
            match = _URL_IN_TEXT.search(candidate)
            if match:
                return query_facts(match.group(0))
    return None


def _maybe_url(value):
    # httpx passes an httpx.URL object, not a str.
    if type(value).__name__ == "URL":
        return scrub_url(str(value))
    return value
