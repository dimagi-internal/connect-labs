"""MCP-side helper for looking up the calling user's Connect OAuth token.

Raises MCPToolError(PERMISSION_DENIED) — appropriate for the MCP client to see —
rather than the lower-level ConnectTokenError.
"""
from connect_labs.labs.connect_tokens import ConnectTokenError, get_valid_access_token

from .tool_registry import MCPToolError


def require_connect_token(user) -> str:
    """Return a valid Connect access_token for `user` or raise MCPToolError.

    Call this from every tool handler that needs to talk to Connect.
    """
    try:
        return get_valid_access_token(user)
    except ConnectTokenError as e:
        raise MCPToolError("PERMISSION_DENIED", str(e))
