"""DRF authentication for the /api/export/ surface.

Reuses the MCP Personal Access Token machinery: an external consumer (Scout)
sends ``Authorization: Bearer <pat>`` exactly as it would against the MCP server.
"""

from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed, PermissionDenied

from connect_labs.mcp import token_scopes
from connect_labs.mcp.models import MCPAccessToken

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
        # This API serves visit data (user_visits, completed_works, ...), so only a
        # full-access token may use it. Anything else is refused, including a
        # scope added later that nobody taught this check about.
        if token.scope != token_scopes.FULL:
            raise PermissionDenied("This token cannot use the export API: it has no access to user visit data.")
        if getattr(token.user, "mcp_no_uservisit_data", False):
            raise PermissionDenied("Your account has no access to user visit data, so it cannot use the export API.")
        token.touch()
        return (token.user, token)

    def authenticate_header(self, request):
        return f'Bearer realm="{self.realm}"'


class MCPTokenScheme(OpenApiAuthenticationExtension):
    """Document the PAT bearer scheme in the OpenAPI spec (auto-discovered)."""

    target_class = "connect_labs.labs.export_api.authentication.MCPTokenAuthentication"
    name = "MCPToken"

    def get_security_definition(self, auto_schema):
        return {
            "type": "http",
            "scheme": "bearer",
            "description": "MCP Personal Access Token. Mint one at /labs/mcp/tokens/.",
        }
