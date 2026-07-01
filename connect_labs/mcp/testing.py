"""In-process test bridge for the FastMCP server.

The MCP protocol endpoint is now a FastMCP Streamable-HTTP ASGI app mounted in
``config.asgi`` — it is NOT reachable through Django's synchronous test client
(no ``reverse("mcp:endpoint")`` anymore). The tool/auth/audit/rate-limit logic,
however, all lives in ``server._run_registry_tool`` + the PAT lookup, and that
is what the unit tests care about.

``call_tool`` drives that exact production logic IN-PROCESS and SYNCHRONOUSLY:

  1. resolves the raw PAT through the same ``MCPAccessToken.verify`` + ``touch``
     the production verifier uses (so invalid/revoked/expired tokens behave
     exactly as over HTTP),
  2. installs a FastMCP ``AccessToken`` for that user into the SDK context var
     so ``server.current_user()`` recovers the caller,
  3. runs the registry tool (write-limit + handler + audit + private-key
     stripping),
  4. re-wraps the outcome into the same JSON-RPC-shaped envelope the old
     hand-rolled transport returned, so existing assertions
     (``result.structuredContent`` / ``result.isError`` / ``error.code``)
     keep working.

IMPORTANT — runs synchronously on the caller's thread (no ``sync_to_async``
threadpool hop). Production resolves the user via ``sync_to_async`` inside the
MCP event loop, but in tests that would push the ORM onto a different thread
than pytest-django's transaction connection and the seeded rows would be
invisible. Calling the sync core directly keeps test DB visibility intact while
still exercising the real verify/run/audit code.

This is a TEST helper only — production traffic goes through FastMCP's own
Streamable-HTTP handler, which builds the real protocol envelope.
"""

from __future__ import annotations

import json

from fastmcp.exceptions import ToolError
from fastmcp.server.auth import AccessToken
from fastmcp.server.dependencies import _task_access_token

from .models import MCPAccessToken
from .server import PAT_SCOPES, _run_registry_tool, _write_audit, current_user
from .tool_registry import MCPToolError, get_tool


def _verify(raw: str | None) -> AccessToken | None:
    """Synchronous mirror of CommCarePATVerifier.verify_token (no threadpool)."""
    if not raw:
        return None
    token = MCPAccessToken.verify(raw)
    if token is None:
        return None
    token.touch()
    user = token.user
    return AccessToken(
        token=raw,
        client_id=str(user.pk),
        scopes=PAT_SCOPES,
        claims={
            "sub": str(user.pk),
            "user_id": user.pk,
            "username": getattr(user, "username", "") or "",
            "auth_method": "pat",
        },
    )


def call_tool(raw_pat: str | None, tool_name: str, arguments: dict | None = None) -> dict:
    """Invoke a tool exactly as the FastMCP server would, in-process.

    Returns a JSON-RPC-shaped dict:
      * unauthenticated/invalid token -> {"error": {"code": "PERMISSION_DENIED", ...}}
      * unknown tool   -> {"result": {"isError": True, "structuredContent":
                          {"error": {"code": "NOT_FOUND", ...}}}}
      * tool failure   -> {"result": {"isError": True, "structuredContent":
                          {"error": {"code": ..., "message": ...}}}}
      * success        -> {"result": {"isError": False, "content": [...],
                          "structuredContent": {...}}}
    """
    arguments = arguments or {}

    access = _verify(raw_pat)
    if access is None:
        return {"error": {"code": "PERMISSION_DENIED", "message": "Invalid or expired token"}}

    ctx_token = _task_access_token.set(access)
    try:
        spec = get_tool(tool_name)
        if spec is None:
            _write_audit(current_user(), tool_name, arguments, success=False, error_code="NOT_FOUND")
            return {
                "result": {
                    "isError": True,
                    "structuredContent": {"error": {"code": "NOT_FOUND", "message": f"Unknown tool: {tool_name}"}},
                }
            }

        try:
            result = _run_registry_tool(spec, arguments)
        except ToolError as e:
            code = "UPSTREAM_ERROR"
            details: dict = {}
            if isinstance(e.__cause__, MCPToolError):
                code = e.__cause__.code
                details = e.__cause__.details or {}
            error = {"code": code, "message": str(e)}
            if details:
                error["details"] = details
            return {
                "result": {
                    "isError": True,
                    "structuredContent": {"error": error},
                }
            }
    finally:
        _task_access_token.reset(ctx_token)

    return {
        "result": {
            "isError": False,
            "content": list(result.content or []),
            "structuredContent": result.structured_content,
        }
    }


def text_content(envelope: dict) -> str:
    """Convenience: pull the JSON text blob out of a success envelope."""
    for block in envelope["result"]["content"]:
        text = block.get("text") if isinstance(block, dict) else getattr(block, "text", None)
        if text is not None:
            return text
    return json.dumps(envelope["result"].get("structuredContent"))
