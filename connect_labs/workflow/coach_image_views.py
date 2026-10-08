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
"""

from __future__ import annotations

from django.http import Http404, HttpResponse, JsonResponse
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


@require_GET
def coach_image(request, token: str):
    header = request.headers.get("authorization", "")
    if not header.lower().startswith(_BEARER):
        return _refuse(401, "Missing Bearer token")
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

    response = HttpResponse(images.render_png(payload), content_type="image/png")
    response["Cache-Control"] = "private, no-store"
    response["X-Robots-Tag"] = "noindex, nofollow"
    return response
