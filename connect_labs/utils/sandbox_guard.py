"""A server-side guard for the page sandbox, independent of the browser.

Pages' render code runs in an opaque-origin frame (workflow/sandbox.py). Whether such
a frame's requests carry the labs session cookie is left to each browser's cookie
rules, and those differ: WebKit computes the site for cookies from the top document,
so Safari may send the ``SameSite=Lax`` session from a frame embedded in a labs page.
So the session is not left to them:

* ``SandboxOriginGuardMiddleware`` refuses any SESSION-authenticated request that
  carries ``Origin: null`` (what an opaque origin sends), except the frame shell
  itself, and any unsafe-method session request whose ``Sec-Fetch-Site`` is
  ``cross-site``.
* ``require_same_origin`` adds an ``Origin`` check to the ``@csrf_exempt`` endpoints
  the workflow runner reaches, which have no CSRF check to lean on.

Bearer, DPoP and PAT requests that carry no session cookie are not affected.
"""

from __future__ import annotations

import functools
from urllib.parse import urlsplit

from django.conf import settings
from django.http import JsonResponse

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def _session_authenticated(request) -> bool:
    """A session cookie came with the request and authenticated a user.

    Decided by the cookie, never by an ``Authorization`` header beside it: Django
    authenticates from the session whatever else the request carries, so a header
    must not switch the guard off.
    """
    if settings.SESSION_COOKIE_NAME not in request.COOKIES:
        return False
    user = getattr(request, "user", None)
    return bool(user is not None and getattr(user, "is_authenticated", False))


def _refuse(reason: str) -> JsonResponse:
    return JsonResponse({"error": reason}, status=403)


class SandboxOriginGuardMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        from connect_labs.workflow.sandbox import SHELL_PATH

        if _session_authenticated(request):
            if request.headers.get("origin") == "null" and not request.path.startswith(SHELL_PATH):
                return _refuse("A request from an opaque origin does not use your labs session.")
            if (
                request.method not in SAFE_METHODS
                and request.headers.get("sec-fetch-site", "").lower() == "cross-site"
            ):
                return _refuse("A cross-site write does not use your labs session.")
        return self.get_response(request)


def _origin(url: str) -> str:
    parts = urlsplit(url or "")
    return f"{parts.scheme}://{parts.netloc}".lower() if parts.scheme and parts.netloc else ""


def allowed_origins(request) -> set[str]:
    out = {f"{request.scheme}://{request.get_host()}".lower()}
    public = _origin(getattr(settings, "LABS_PUBLIC_URL", "") or "")
    if public:
        out.add(public)
    for trusted in getattr(settings, "CSRF_TRUSTED_ORIGINS", []) or []:
        if "*" not in trusted:
            out.add(trusted.rstrip("/").lower())
    return out


def require_same_origin(view):
    """Refuse an unsafe-method request whose ``Origin`` is not labs' own.

    For ``@csrf_exempt`` views a browser reaches. A request with no ``Origin`` at all
    (a server-to-server client) is let through: every browser sends ``Origin`` on a
    cross-origin write, and ``null`` from an opaque one.
    """

    @functools.wraps(view)
    def wrapped(request, *args, **kwargs):
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            if origin is not None and origin.lower() not in allowed_origins(request):
                return _refuse("This request did not come from a labs page.")
        return view(request, *args, **kwargs)

    return wrapped
