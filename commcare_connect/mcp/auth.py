"""PAT authentication for the MCP endpoint.

Accepts `Authorization: Bearer <raw_token>`. Populates request.mcp_user on
success; returns 401 with a JSON body on failure.
"""
from django.http import JsonResponse

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

    token.touch()
    return token.user, None


def _unauthorized(message: str) -> JsonResponse:
    response = JsonResponse(
        {"error": {"code": "PERMISSION_DENIED", "message": message}},
        status=401,
    )
    response["WWW-Authenticate"] = 'Bearer realm="labs-mcp"'
    return response
