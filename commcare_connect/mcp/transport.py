"""MCP Streamable HTTP transport (JSON-RPC 2.0 over HTTP).

Implements the request/response subset of the spec sufficient for the labs
MCP server. No SSE / long-polling yet — tools are short-running.
"""
import json
import logging
import uuid

from django.http import HttpRequest, JsonResponse

from commcare_connect.labs.integrations.connect.api_client import LabsAPIError

from .models import MCPAuditLog
from .rate_limit import enforce_write_limit
from .tool_registry import MCPToolError, get_tool, list_tools

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"  # MCP spec version
SERVER_INFO = {"name": "connect_labs", "version": "0.1.0"}
CAPABILITIES = {"tools": {"listChanged": False}}


def handle_request(request: HttpRequest, user) -> JsonResponse:
    """Parse the JSON-RPC envelope, dispatch, and return the response.

    `user` is the authenticated Django User (populated by the auth layer).
    """
    try:
        body = json.loads(request.body or b"{}")
    except json.JSONDecodeError as e:
        return _jsonrpc_error(None, -32700, f"Parse error: {e}")

    if body.get("jsonrpc") != "2.0":
        return _jsonrpc_error(body.get("id"), -32600, "Invalid Request: jsonrpc must be 2.0")

    method = body.get("method")
    msg_id = body.get("id")
    params = body.get("params") or {}

    try:
        if method == "initialize":
            result = _handle_initialize(params)
        elif method == "tools/list":
            result = {"tools": list_tools()}
        elif method == "tools/call":
            result = _handle_tools_call(params, user)
        elif method == "ping":
            result = {}
        else:
            return _jsonrpc_error(msg_id, -32601, f"Method not found: {method}")
    except MCPToolError as e:
        logger.info("MCP tool error: %s %s", e.code, e.message)
        return JsonResponse(
            {
                "jsonrpc": "2.0",
                "id": msg_id,
                "result": {
                    "isError": True,
                    "content": [{"type": "text", "text": e.message}],
                    "structuredContent": {"error": {"code": e.code, "message": e.message, "details": e.details}},
                },
            }
        )
    except Exception as exc:
        request_id = uuid.uuid4().hex[:8]
        logger.exception("MCP internal error [request_id=%s]", request_id)
        return _internal_error_response(msg_id, exc, request_id)

    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})


def _internal_error_response(msg_id, exc: Exception, request_id: str) -> JsonResponse:
    """Build a -32603 envelope that surfaces enough detail for the client to debug.

    JSON-RPC 2.0 lets us populate `error.data` with arbitrary structured detail
    (https://www.jsonrpc.org/specification#error_object). Clients that key off
    the numeric code keep working; clients that care can read `error.data` for
    exception type, request id, and (for upstream HTTP failures) the upstream
    status + body.
    """
    details = {"exception_type": type(exc).__name__, "request_id": request_id}
    if isinstance(exc, LabsAPIError):
        if exc.status_code is not None:
            details["upstream_status"] = exc.status_code
        if exc.body is not None:
            details["upstream_body"] = exc.body
    detail_msg = str(exc) or "Internal error"
    return JsonResponse(
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "error": {
                "code": -32603,
                "message": f"Internal error: {type(exc).__name__}: {detail_msg}"[:500],
                "data": details,
            },
        }
    )


def _handle_initialize(params: dict) -> dict:
    client_version = params.get("protocolVersion", "unknown")
    logger.info("MCP initialize: client_version=%s", client_version)
    return {
        "protocolVersion": PROTOCOL_VERSION,
        "capabilities": CAPABILITIES,
        "serverInfo": SERVER_INFO,
    }


def _handle_tools_call(params: dict, user) -> dict:
    name = params.get("name")
    arguments = params.get("arguments") or {}

    if not name:
        raise MCPToolError("INVALID_SCHEMA", "tools/call missing required 'name' param")

    tool = get_tool(name)
    if tool is None:
        _log(user, name, arguments, success=False, error_code="NOT_FOUND")
        raise MCPToolError("NOT_FOUND", f"Unknown tool: {name}")

    if tool.is_write:
        try:
            enforce_write_limit(user)
        except MCPToolError as e:
            _log(user, name, arguments, success=False, error_code=e.code, is_write=True)
            raise

    try:
        result = tool.handler(user=user, **arguments)
    except MCPToolError as e:
        _log(user, name, arguments, success=False, error_code=e.code, is_write=tool.is_write)
        raise
    except Exception as e:
        _log(user, name, arguments, success=False, error_code="UPSTREAM_ERROR", is_write=tool.is_write)
        # Wrap the exception so the MCP client sees the full traceback (file +
        # line) inline — much faster to debug than poking through CloudWatch.
        # Synthetic/demo tools are the main caller; if you don't want this in
        # prod ALL responses, gate on a flag — but for now the diagnostic
        # value outweighs the noise.
        import traceback
        tb = traceback.format_exc()
        raise MCPToolError("UPSTREAM_ERROR", f"{type(e).__name__}: {e}\n{tb}") from e

    version_before = None
    version_after = None
    if isinstance(result, dict):
        version_before = result.get("_version_before")
        version_after = result.get("_version_after")
        # Strip private keys before returning to caller
        result = {k: v for k, v in result.items() if not k.startswith("_")}

    _log(
        user,
        name,
        arguments,
        success=True,
        is_write=tool.is_write,
        version_before=version_before,
        version_after=version_after,
    )
    return {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps(result)}],
        "structuredContent": result if isinstance(result, dict) else {"value": result},
    }


def _log(
    user,
    tool_name: str,
    arguments: dict,
    *,
    success: bool,
    is_write: bool = False,
    error_code: str = "",
    version_before: int | None = None,
    version_after: int | None = None,
) -> None:
    """Best-effort audit write. Never raises — failure to log must not
    abort a tool call."""
    try:
        MCPAuditLog.objects.create(
            user=user if (user and user.is_authenticated) else None,
            tool_name=tool_name,
            is_write=is_write,
            arguments=arguments if is_write else {},
            success=success,
            error_code=error_code or "",
            version_before=version_before,
            version_after=version_after,
        )
    except Exception:
        logger.exception("Failed to write MCPAuditLog row (non-fatal)")


def _jsonrpc_error(msg_id, code: int, message: str) -> JsonResponse:
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})
