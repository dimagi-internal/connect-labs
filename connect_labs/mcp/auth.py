"""PAT authentication for the MCP endpoint.

Accepts `Authorization: Bearer <raw_token>`. Populates request.mcp_user on
success; returns 401 with a JSON body on failure.
"""

from django.http import JsonResponse

from . import token_scopes
from .models import MCPAccessToken


def authenticate_request(request) -> tuple[object, JsonResponse | None]:
    """Verify the Authorization header.

    Returns (user, None) on success. Returns (None, JsonResponse) on failure
    where the response is a 401 the view should return directly.
    """
    header = request.headers.get("authorization", "")
    if not header.lower().startswith("bearer "):
        return None, _unauthorized("Missing Bearer token")

    raw = header[len("Bearer ") :].strip()
    token = MCPAccessToken.verify(raw)
    if token is None:
        return None, _unauthorized("Invalid or expired token")
    # Callers of this verifier (outside the MCP server's tool gate) act with the
    # user's full reach, so only a full-access token may pass.
    if token.scope != token_scopes.FULL:
        return None, JsonResponse(
            {"error": {"code": "PERMISSION_DENIED", "message": "This token's scope does not allow this endpoint."}},
            status=403,
        )

    token.touch()
    return token.user, None


def _unauthorized(message: str) -> JsonResponse:
    response = JsonResponse(
        {"error": {"code": "PERMISSION_DENIED", "message": message}},
        status=401,
    )
    response["WWW-Authenticate"] = 'Bearer realm="labs-mcp"'
    return response
