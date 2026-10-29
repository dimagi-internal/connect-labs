"""Logging filter: outbound-request log lines without query strings or record ids.

httpx logs every request at INFO (`HTTP Request: GET <url> "HTTP/1.1 200 OK"`),
and the web/worker log groups keep those lines for 731 days. The URL's query
string carries FLW usernames, entity/case ids and search terms (`?username=`,
`?case_id=`), so the line keeps its debugging value -- method, host, path shape,
status, and the count of calls (docs/PERFORMANCE_RUNBOOK.md counts them) -- but
the query is dropped and id-like path segments become `{id}`.
"""

from __future__ import annotations

import logging
import re
from urllib.parse import urlsplit

_ID_SEGMENT = re.compile(
    r"^(?:\d+"  # integer ids
    r"|[0-9a-fA-F]{8}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{4}-?[0-9a-fA-F]{12}"  # uuids
    r"|[0-9a-fA-F]{24,}"  # long hex (CommCare doc ids)
    r"|[A-Za-z0-9_-]{32,})$"  # tokens
)
_URL_IN_TEXT = re.compile(r"https?://[^\s\"'<>]+")


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
        if record.args:
            args = record.args if isinstance(record.args, tuple) else (record.args,)
            record.args = tuple(scrub_urls_in_text(a) if isinstance(a, str) else _maybe_url(a) for a in args)
        if isinstance(record.msg, str):
            record.msg = scrub_urls_in_text(record.msg)
        return True


def _maybe_url(value):
    # httpx passes an httpx.URL object, not a str.
    if type(value).__name__ == "URL":
        return scrub_url(str(value))
    return value
