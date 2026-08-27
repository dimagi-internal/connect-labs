"""Allowlist for CommCare HQ hosts we will make outbound requests to.

`download_ccz` builds its URL from `hq_server.url` on a LabsRecord — a value
that arrives over the API rather than from configuration — and then fetches it
with no restriction on where it points. That is a server-side request forgery
sink: anything that can write that record chooses a host the labs server will
connect to, from inside the VPC (audit item L, #1032).

The private-IP check in `solicitations.views.validate_url_safe` is the general
defense and is used elsewhere, but it is the wrong shape here. We are not
accepting arbitrary user URLs and filtering out the dangerous ones — we are
talking to CommCare HQ, a short known set of hosts. An allowlist states that,
and unlike a blocklist it does not have to anticipate the next bypass (a
redirect, a DNS rebind, a public host that proxies inward).
"""

from __future__ import annotations

import logging
from urllib.parse import urlparse

from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED_HQ_HOSTS = (
    "www.commcarehq.org",
    "commcarehq.org",
    "staging.commcarehq.org",
    "india.commcarehq.org",
    "eu.commcarehq.org",
)


def allowed_hq_hosts() -> set[str]:
    configured = getattr(settings, "ALLOWED_HQ_HOSTS", None)
    return {h.strip().lower() for h in (configured or DEFAULT_ALLOWED_HQ_HOSTS) if h and h.strip()}


def is_allowed_hq_url(url: str) -> bool:
    """True if ``url`` is an https URL on an allowlisted CommCare HQ host.

    Scheme is checked too: an ``http://`` or ``file://`` URL on an allowlisted
    host is still not something to fetch credentials-adjacent data over.
    """
    try:
        parsed = urlparse(url or "")
    except Exception:
        return False
    if parsed.scheme != "https":
        return False
    host = (parsed.hostname or "").lower()
    return bool(host) and host in allowed_hq_hosts()


def reject_disallowed_hq_url(url: str) -> str | None:
    """Return an error message if ``url`` is not an allowlisted HQ URL, else None."""
    if is_allowed_hq_url(url):
        return None
    host = "<unparseable>"
    try:
        host = urlparse(url or "").hostname or "<none>"
    except Exception:
        pass
    logger.warning("Blocked outbound HQ request to disallowed host %r", host)
    return f"Refusing to contact {host!r}: not an allowlisted CommCare HQ host. Set ALLOWED_HQ_HOSTS to permit it."
