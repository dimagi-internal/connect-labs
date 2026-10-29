"""How much of the web tier's traffic is ONE requester, per worker, per minute.

Why this exists
---------------
Five labs incidents (2026-09-01, 10-13 twice, 10-14, 10-29 / #2769) were one user or
one script fetching audit or workflow data in a loop. Each took a full investigation
to reach "one requester is 95% of traffic", because nothing recorded traffic by who
sent it: ``Labs/Requests`` (#2771) is by route, and the observe role cannot read raw
logs. This makes it one query.

Two outputs, every ``WINDOW_S`` seconds, from each web worker process:

* **EMF metrics** in ``Labs/Requests`` with NO dimensions (an identity dimension
  would be a billed metric per user and would put the identity into a metric):
  ``TopRequesterShare`` (Percent of this worker's authenticated requests in the
  window that came from its busiest requester), ``TopRequesterRequests``,
  ``ActiveRequesters`` and ``WindowRequests``. Read ``Maximum`` across workers, and
  read the share WITH the count -- one person alone at 3 a.m. is 100% of five
  requests.
* **A ``top_requester`` log line** on the telemetry stream, only when the busiest
  requester made at least ``LOG_MIN_REQUESTS`` requests in the window, naming it,
  its user-agent CLASS (browser / curl / python / node / agent / other / none --
  never the user-agent string) and the route template it hit most.

Who is named, and who is hashed
-------------------------------
A Dimagi staff or system account (``is_dimagi_staff``: a Dimagi sign-in domain or
Django ``is_staff``) is named by its labs username -- staff identities are not
sensitive. ANYONE ELSE -- a partner, a programme manager, anyone who might be a
frontline worker -- is a stable short hash, ``u:<12 hex>``, an HMAC of the username
keyed by ``SECRET_KEY``: stable across deploys so it can be followed, and not
reversible by hashing a list of guessed usernames. Anonymous requests are counted
apart and never form the "top requester" (they are scanners and health checks).

Health checks and unresolved paths are not counted at all: the ALB's checks are
~75% of all requests and would drown every real requester.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import os
import threading
import time
from collections import Counter

from django.conf import settings

logger = logging.getLogger("connect_labs.telemetry.request")
# A child of the per-request metrics logger: same handler, distinct name.
metrics_logger = logging.getLogger("connect_labs.telemetry.metrics.requesters")

# Off under test settings: the window is process-wide, so in a long test run it
# would flush a top_requester line into whichever unrelated test crossed 60 s.
ENABLED = bool(getattr(settings, "TELEMETRY_REQUESTER_CONCENTRATION", True))
WINDOW_S = float(getattr(settings, "TELEMETRY_REQUESTER_WINDOW_S", 60))
LOG_MIN_REQUESTS = int(getattr(settings, "TELEMETRY_TOP_REQUESTER_LOG_MIN", 20))
METRICS_NAMESPACE = "Labs/Requests"
METRIC_NAMES = ("TopRequesterShare", "TopRequesterRequests", "ActiveRequesters", "WindowRequests")
# Route templates (never raw paths) that are not a requester's traffic.
_SKIP_ROUTES = frozenset({"health/", "unmatched", "robots.txt"})
USER_AGENT_CLASSES = ("agent", "curl", "python", "node", "browser", "other", "none")


def user_agent_class(user_agent: str | None) -> str:
    """A fixed vocabulary for the client kind. The raw string is never kept."""
    ua = (user_agent or "").lower()
    if not ua:
        return "none"
    if "claude" in ua or "anthropic" in ua or "mcp" in ua:
        return "agent"
    if "curl" in ua or "wget" in ua:
        return "curl"
    if any(t in ua for t in ("python", "httpx", "aiohttp", "urllib")):
        return "python"
    if any(t in ua for t in ("node", "axios", "undici", "got (")):
        return "node"
    if "mozilla" in ua:
        return "browser"
    return "other"


def requester_label(user) -> tuple[str | None, str]:
    """``(label, kind)``: staff by name, everyone else by keyed hash, anonymous as None."""
    if user is None or not getattr(user, "is_authenticated", False):
        return None, "anonymous"
    username = getattr(user, "username", None) or ""
    from connect_labs.utils.dimagi_user import is_dimagi_staff

    if username and is_dimagi_staff(user):
        return username, "staff"
    key = (getattr(settings, "SECRET_KEY", "") or "labs").encode()
    digest = hmac.new(key, username.encode(), hashlib.sha256).hexdigest()[:12]
    return f"u:{digest}", "hashed"


class _Window:
    def __init__(self):
        self.lock = threading.Lock()
        self.reset(time.monotonic())

    def reset(self, now: float) -> None:
        self.started = now
        self.by_requester: Counter = Counter()
        self.kind: dict[str, str] = {}
        self.ua: dict[str, Counter] = {}
        self.routes: dict[str, Counter] = {}
        self.anonymous = 0


_window = _Window()


def observe(request, route: str) -> None:
    """Count one finished request; flush the window when it is due. Never raises."""
    try:
        if not ENABLED or route in _SKIP_ROUTES:
            return
        label, kind = requester_label(getattr(request, "user", None))
        ua = user_agent_class(getattr(request, "META", {}).get("HTTP_USER_AGENT"))
        now = time.monotonic()
        snapshot = None
        with _window.lock:
            if label is None:
                _window.anonymous += 1
            else:
                _window.by_requester[label] += 1
                _window.kind[label] = kind
                _window.ua.setdefault(label, Counter())[ua] += 1
                _window.routes.setdefault(label, Counter())[route] += 1
            if now - _window.started >= WINDOW_S:
                snapshot = _summarise(now)
                _window.reset(now)
        if snapshot is not None:
            _emit(snapshot)
    except Exception:
        logger.debug("requester concentration failed", exc_info=True)


def _summarise(now: float) -> dict | None:
    total = sum(_window.by_requester.values())
    if not total:
        return None
    top, top_n = _window.by_requester.most_common(1)[0]
    route, route_n = _window.routes[top].most_common(1)[0]
    return {
        "event": "top_requester",
        "window_s": int(now - _window.started),
        "pid": os.getpid(),
        "requests": total,
        "anonymous_requests": _window.anonymous,
        "distinct_requesters": len(_window.by_requester),
        "top_requester": top,
        "top_requester_kind": _window.kind[top],
        "top_user_agent_class": _window.ua[top].most_common(1)[0][0],
        "top_requests": top_n,
        "top_share_pct": round(100.0 * top_n / total, 1),
        "top_route": route,
        "top_route_requests": route_n,
    }


def _emit(s: dict) -> None:
    if getattr(settings, "TELEMETRY_ROUTE_METRICS", True):
        values = {
            "TopRequesterShare": s["top_share_pct"],
            "TopRequesterRequests": s["top_requests"],
            "ActiveRequesters": s["distinct_requesters"],
            "WindowRequests": s["requests"],
        }
        units = {"TopRequesterShare": "Percent"}
        line = {
            "_aws": {
                "Timestamp": int(time.time() * 1000),
                "CloudWatchMetrics": [
                    {
                        "Namespace": METRICS_NAMESPACE,
                        # The empty dimension set: one series for the whole tier.
                        "Dimensions": [[]],
                        "Metrics": [{"Name": k, "Unit": units.get(k, "Count")} for k in values],
                    }
                ],
            },
            **values,
        }
        metrics_logger.info(json.dumps(line, separators=(",", ":")))
    if s["top_requests"] >= LOG_MIN_REQUESTS:
        logger.warning(json.dumps(s))
