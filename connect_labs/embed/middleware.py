"""Let an embed session act as its person -- on its own routes, and nowhere else.

Runs after the labs auth and context middlewares. A request carrying a
``labs_embed_*`` cookie whose session is live, and whose path is that session's frame
or a route its kind allows (``kinds.EmbedKind.allowed``), is REPLACED for the length
of the request, never merged:

* ``request.user`` is the session's person;
* ``request.session`` is an in-memory session that is never saved, holding the
  person's current Connect token as ``labs_oauth`` -- so every read goes to prod as
  them and prod's access checks apply unchanged;
* ``request.labs_context`` is the resource's scope, and ``request.labs_embed`` the session.

On any other path the cookie is ignored: it carries no authority there, and the request
is whatever it would have been without it (in another site's frame, anonymous).

Writes: a kind may allow a read that is a POST (a query with a body). Django's CSRF
check cannot run for it (the CSRF cookie is ``SameSite=Lax``, so a cross-site frame has
none); instead the request's ``Origin`` must be labs' own, which the frame's own
``fetch`` always sends and a form on another site cannot.

The real session object is put back before the response leaves, so SessionMiddleware
never saves, or sets a cookie for, the embed's.
"""

from __future__ import annotations

import logging

from django.conf import settings
from django.http import JsonResponse

from connect_labs.embed import sessions
from connect_labs.embed.kinds import get_kind

logger = logging.getLogger(__name__)

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def embed_enabled() -> bool:
    return bool(getattr(settings, "LABS_EMBED_ENABLED", True))


def frame_path(session) -> str:
    from django.urls import reverse

    return reverse("embed:frame", args=[session.client.client_id, session.kind, session.resource_id])


def _session_class():
    from django.contrib.sessions.backends.base import SessionBase

    class EmbedRequestSession(SessionBase):
        """A request's session that exists only in memory and is never saved."""

        def __init__(self, data: dict):
            super().__init__(session_key=None)
            self._session_cache = dict(data)

        def exists(self, session_key):
            return False

        def create(self):
            pass

        def save(self, must_create=False):
            pass

        def delete(self, session_key=None):
            pass

        def load(self):
            return {}

        def cycle_key(self):
            pass

        def flush(self):
            self._session_cache = {}

    return EmbedRequestSession


def _matching_session(request):
    """The live embed session among the request's cookies that authorises this path."""
    for name, value in request.COOKIES.items():
        if not name.startswith(sessions.COOKIE_PREFIX):
            continue
        session = sessions.lookup(value)
        if session is None or sessions.cookie_name(session.kind, session.resource_id) != name:
            continue
        kind = get_kind(session.kind)
        if kind is None:
            continue
        if request.path == frame_path(session) and request.method in SAFE_METHODS:
            return session, kind
        if kind.allowed(session, request.method, request.path):
            return session, kind
    return None, None


class EmbedSessionMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if not embed_enabled() or not any(k.startswith(sessions.COOKIE_PREFIX) for k in request.COOKIES):
            return self.get_response(request)
        session, kind = _matching_session(request)
        if session is None:
            return self.get_response(request)

        if request.method not in SAFE_METHODS:
            from connect_labs.utils.sandbox_guard import allowed_origins

            if (request.headers.get("origin") or "").lower() not in allowed_origins(request):
                return JsonResponse({"error": "An embedded read must come from the labs frame."}, status=403)

        from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token
        from connect_labs.labs.models import UserConnectToken

        try:
            access_token = get_valid_access_token(session.user)
            expires_at = UserConnectToken.objects.get(user=session.user).expires_at.timestamp()
        except (ConnectTokenError, UserConnectToken.DoesNotExist):
            # The person's Connect sign-in has lapsed: the frame asks its parent to start over.
            return JsonResponse({"error": "This embed can no longer reach Connect as you."}, status=401)

        labs_oauth = {"access_token": access_token, "expires_at": expires_at}
        # What login stores beside the token: the person's organisations, programmes and
        # opportunities, which labs' own access checks read (labs/access/scopes.py). Cached
        # per person by the fetch itself, so this is not a Connect call per request.
        from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

        org_data = fetch_user_organization_data(access_token, owner=session.user.username)
        if org_data is not None:
            labs_oauth["organization_data"] = org_data

        original_session = getattr(request, "session", None)
        request.user = session.user
        request._cached_user = session.user
        request.session = _session_class()({"labs_oauth": labs_oauth})
        request.labs_context = kind.scope(session.entry)
        request.labs_embed = session
        if request.method not in SAFE_METHODS:
            request._dont_enforce_csrf_checks = True
        try:
            response = self.get_response(request)
        finally:
            if original_session is not None:
                request.session = original_session
        return response
