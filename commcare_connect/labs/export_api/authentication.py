"""DRF authentication for the /api/export/ surface.

Reuses the MCP Personal Access Token machinery: an external consumer (Scout)
sends ``Authorization: Bearer <pat>`` exactly as it would against the MCP server.
"""
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from commcare_connect.mcp.models import MCPAccessToken

_BEARER_PREFIX = "bearer "


class MCPTokenAuthentication(BaseAuthentication):
    """Authenticate via an MCP PAT in the Authorization header.

    Returns ``None`` (no credentials) when the header is absent so DRF falls
    through to a 401 carrying our ``authenticate_header``. Raises
    ``AuthenticationFailed`` (also 401) when a token is present but invalid.
    """

    realm = "labs-export"

    def authenticate(self, request):
        header = request.headers.get("authorization", "")
        if not header.lower().startswith(_BEARER_PREFIX):
            return None
        raw = header[len(_BEARER_PREFIX) :].strip()
        token = MCPAccessToken.verify(raw)
        if token is None:
            raise AuthenticationFailed("Invalid or expired token")
        token.touch()
        return (token.user, token)

    def authenticate_header(self, request):
        return f'Bearer realm="{self.realm}"'
