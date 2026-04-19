"""MCP Streamable HTTP transport (JSON-RPC 2.0 over HTTP).

Implements the request/response subset of the spec sufficient for the labs
MCP server. No SSE / long-polling yet — tools are short-running.
"""
import json
import logging

from django.http import HttpRequest, JsonResponse

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
    except Exception:
        logger.exception("MCP internal error")
        return _jsonrpc_error(msg_id, -32603, "Internal error")

    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "result": result})


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
        raise MCPToolError("NOT_FOUND", f"Unknown tool: {name}")

    result = tool.handler(user=user, **arguments)
    return {
        "isError": False,
        "content": [{"type": "text", "text": json.dumps(result)}],
        "structuredContent": result if isinstance(result, dict) else {"value": result},
    }


def _jsonrpc_error(msg_id, code: int, message: str) -> JsonResponse:
    return JsonResponse({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})
