"""Titles for links staff paste into review comments, so a comment can show a card.

Only public pages give a title: Labs fetches a link as nobody, so a private Google Doc, Sheet or
Drive file answers with Google's sign-in page and gets no title (the page then shows its type).

Fetching a URL someone typed is how a server is made to call inside its own network. So:
- https only, port 443, no user or password in the URL;
- every host is resolved and checked by ``hqdata.netguard.check_host`` (no loopback, link-local,
  metadata, private or labs-database addresses), and the request goes to the address that was
  checked, never to the name again, with TLS still verified against the name;
- every redirect hop is checked the same way, at most three of them;
- three seconds to connect, four to read, and at most 256 KB read.
Answers are cached for a day (an hour when there is no title).
"""

from __future__ import annotations

import hashlib
import html
import logging
import re
from urllib.parse import urljoin, urlsplit

import certifi
import urllib3
from django.core.cache import cache

from connect_labs.hqdata import netguard

logger = logging.getLogger(__name__)

MAX_URL = 2000
MAX_BYTES = 256 * 1024
MAX_REDIRECTS = 3
TITLE_MAX = 200
#: Hosts whose page is a sign-in screen, never the document's title.
SIGN_IN_HOSTS = {"accounts.google.com", "login.microsoftonline.com", "id.atlassian.com"}
_SIGN_IN_TITLE = re.compile(r"\b(sign[ -]?in|log[ -]?in)\b", re.I)
_OG_TITLE = re.compile(
    r"<meta[^>]+(?:property|name)\s*=\s*[\"']og:title[\"'][^>]*content\s*=\s*[\"']([^\"']*)[\"']"
    r"|<meta[^>]+content\s*=\s*[\"']([^\"']*)[\"'][^>]*(?:property|name)\s*=\s*[\"']og:title[\"']",
    re.I,
)
_TITLE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)


class Refused(ValueError):
    """A link Labs will not fetch."""


def check_url(url: str) -> str:
    """The URL as Labs would fetch it, or Refused."""
    url = (url or "").strip()
    if not url or len(url) > MAX_URL:
        raise Refused("Give a link of up to 2000 characters.")
    parts = urlsplit(url)
    if parts.scheme != "https" or not parts.hostname:
        raise Refused("Only https links get a preview.")
    if parts.username or parts.password:
        raise Refused("Links with a user name or password get no preview.")
    try:
        port = parts.port
    except ValueError:
        raise Refused("That link's port is not valid.")
    if port not in (None, 443):
        raise Refused("Only links on the standard https port get a preview.")
    return url


def title_from_html(text: str) -> str | None:
    """The page's og:title, else its <title>; None for none, or for a sign-in page."""
    m = _OG_TITLE.search(text)
    raw = (m.group(1) or m.group(2)) if m else None
    if not raw:
        m = _TITLE.search(text)
        raw = m.group(1) if m else None
    if not raw:
        return None
    title = re.sub(r"\s+", " ", html.unescape(raw)).strip()
    if not title or _SIGN_IN_TITLE.search(title):
        return None
    return title[:TITLE_MAX]


def _get(url: str, resolver=None):
    """(status, headers, body) for one hop, made to the checked address."""
    parts = urlsplit(url)
    host = parts.hostname.lower()
    address = netguard.check_host(host, **({"resolver": resolver} if resolver else {}))
    pool = urllib3.HTTPSConnectionPool(
        address,
        port=443,
        server_hostname=host,
        assert_hostname=host,
        cert_reqs="CERT_REQUIRED",
        ca_certs=certifi.where(),
        timeout=urllib3.Timeout(connect=3, read=4),
        retries=False,
        maxsize=1,
    )
    path = (parts.path or "/") + (f"?{parts.query}" if parts.query else "")
    try:
        r = pool.urlopen(
            "GET",
            path,
            headers={"Host": host, "User-Agent": "Connect Labs link preview", "Accept": "text/html"},
            redirect=False,
            preload_content=False,
        )
        try:
            body = r.read(MAX_BYTES) if r.status == 200 else b""
        finally:
            r.release_conn()
        return r.status, r.headers, body
    finally:
        pool.close()


def fetch_title(url: str, *, get=_get) -> str | None:
    """The title of a public page, following at most three checked redirects; None otherwise."""
    current = check_url(url)
    for _ in range(MAX_REDIRECTS + 1):
        host = urlsplit(current).hostname.lower()
        if host in SIGN_IN_HOSTS:
            return None
        status, headers, body = get(current)
        if status in (301, 302, 303, 307, 308):
            nxt = urljoin(current, headers.get("Location") or "")
            current = check_url(nxt)
            continue
        if status != 200 or "html" not in (headers.get("Content-Type") or "").lower():
            return None
        charset = "utf-8"
        m = re.search(r"charset=([\w-]+)", headers.get("Content-Type") or "", re.I)
        if m:
            charset = m.group(1)
        try:
            text = body.decode(charset, errors="replace")
        except LookupError:
            text = body.decode("utf-8", errors="replace")
        return title_from_html(text)
    return None


def preview(url: str) -> dict:
    """{"url", "title"} for a link, cached. Refused for a link Labs will not fetch."""
    url = check_url(url)
    key = "audit_reviews:link:" + hashlib.sha256(url.encode()).hexdigest()
    hit = cache.get(key)
    if hit is not None:
        return {"url": url, "title": hit or None}
    try:
        title = fetch_title(url)
    except (Refused, netguard.HostRefused):
        title = None
    except Exception:  # a page that will not answer is a card without a title, never an error
        logger.info("audit_reviews: no title for a link", exc_info=True)
        title = None
    cache.set(key, title or "", 86400 if title else 3600)
    return {"url": url, "title": title}
