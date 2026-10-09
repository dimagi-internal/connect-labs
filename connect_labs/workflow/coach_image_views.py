"""``GET /labs/coach-image/<token>/``: the coaching picture a session links to.

No login and no session: Open Chat Studio fetches it with a ``coach-images`` Personal
Access Token (``mcp/token_scopes.COACH_IMAGES``) as ``Authorization: Bearer <raw>``,
and nothing else is accepted -- not a full-access token either, so a key that leaks
from an OCS team can draw pictures and do nothing more, and a person's own key is
never asked for here. The path is in the labs OAuth middleware's skip list
(``labs/oauth_session.py``), so a signed-in staffer opening a link is not logged out.

Authentication is checked first and refuses with 401 (no or unknown token) or 403
(a token of another scope); only then is the link read, and a link that is altered,
expired or not ours gets one uniform 404, like Pulse's public links.

A signed-in Labs user may also open a picture in a browser, so a person (or an agent
in the canopy panel beside a run) can see it before sending. That path asks for a live
Labs sign-in -- the ``labs_oauth`` session this path's middleware skip does not check
for us -- and opens only a link that names an opportunity the user can see
(``coach_image.attachment``), the same access check the MCP tools apply. A link with
no opportunity in it opens for Open Chat Studio's token only, so links issued before
this path existed stay exactly as closed as they were. A request carrying a Bearer
header is always judged as Open Chat Studio's, never as a browser's.
"""

from __future__ import annotations

from django.http import Http404, HttpResponse, JsonResponse
from django.utils import timezone
from django.views.decorators.http import require_GET

from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken
from connect_labs.workflow import coach_image as images

_BEARER = "bearer "


def _refuse(status: int, message: str) -> JsonResponse:
    response = JsonResponse({"error": {"code": "PERMISSION_DENIED", "message": message}}, status=status)
    if status == 401:
        response["WWW-Authenticate"] = 'Bearer realm="labs-coach-image"'
    return response


def _has_live_labs_session(request) -> bool:
    """Signed in to Labs, with a Labs sign-in that has not expired. The labs OAuth
    middleware skips this path (a staffer opening a link must not be logged out), so
    nothing upstream has checked the session; an expired one is refused, not renewed."""
    user = getattr(request, "user", None)
    if not user or not user.is_authenticated:
        return False
    labs_oauth = request.session.get("labs_oauth") or {}
    return bool(labs_oauth) and timezone.now().timestamp() < (labs_oauth.get("expires_at") or 0)


def _png(payload: dict) -> HttpResponse:
    try:
        data = images.png_for(payload)
    except images.BadImageLink:
        raise Http404("No such picture")
    response = HttpResponse(data, content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response


def _for_signed_in_user(request, token: str):
    if not _has_live_labs_session(request):
        return _refuse(401, "Sign in to Labs to see this picture.")
    try:
        payload = images.unsign(token)
    except images.BadImageLink:
        raise Http404("No such picture")
    opportunity_id = payload.get("opportunity_id")
    if not isinstance(opportunity_id, int):
        return _refuse(403, "This picture can only be fetched by Open Chat Studio.")

    from connect_labs.mcp.tool_registry import MCPToolError
    from connect_labs.mcp.tools.synthetic import _require_opportunity_access

    try:
        _require_opportunity_access(request.user, opportunity_id)
    except MCPToolError as e:
        if e.code == "UPSTREAM_ERROR":
            return JsonResponse({"error": {"code": e.code, "message": "Could not check access; retry."}}, status=503)
        return _refuse(403, "You do not have access to this picture's opportunity.")
    return _png(payload)


@require_GET
def coach_image(request, token: str):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER):
        return _for_signed_in_user(request, token)
    pat = MCPAccessToken.verify(header[len(_BEARER) :].strip())
    if pat is None:
        return _refuse(401, "Invalid or expired token")
    if pat.scope != token_scopes.COACH_IMAGES:
        return _refuse(403, "Only a coaching-pictures token may fetch coaching pictures.")
    pat.touch()

    try:
        payload = images.unsign(token)
    except images.BadImageLink:
        # One answer for altered, expired and foreign links alike.
        raise Http404("No such picture")

    return _png(payload)
