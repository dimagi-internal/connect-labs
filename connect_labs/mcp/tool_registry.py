"""Tool registry for the labs MCP server.

Tools register themselves at import time. Each tool is a callable with a
JSON-schema for its parameters and a human-readable description.
"""

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any


@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict
    handler: Callable[..., Any]
    is_write: bool = False
    wants_progress: bool = False
    #: The tool's ``_meta`` on ``tools/list`` -- e.g. ``{"ui": {...}}`` for an MCP Apps
    #: tool (``connect_labs/mcp/ui``). None for most tools.
    meta: dict | None = None
    #: Renders the text ``content`` block from the result dict. None (most tools): the
    #: result as JSON. A tool whose result carries something bulky for a View (an
    #: inline picture) gives a short description here, so the text a non-UI client
    #: reads stays readable and small.
    text: Callable[[dict], str] | None = None


_REGISTRY: dict[str, Tool] = {}


def register(
    *,
    name: str,
    description: str,
    input_schema: dict,
    is_write: bool = False,
    wants_progress: bool = False,
    meta: dict | None = None,
    text: Callable[[dict], str] | None = None,
) -> Callable[[Callable], Callable]:
    """Decorator that registers a tool handler.

    Set is_write=True for tools that mutate labs state. Write tools are
    subject to per-user rate limiting and have their full arguments captured
    in the audit log.

    Set wants_progress=True for tools that iterate over a work list for minutes
    at a time. The server then passes a ``progress`` callable as a keyword
    argument, and the handler is expected to call it per item — MCP clients
    apply an idle-output timeout (300s by default in Claude Code) and will kill
    a silent call whose work has already succeeded (connect-labs#1220).
    ``progress`` is passed OUT OF BAND, never through ``input_schema``: it is
    not a caller-supplied argument and must not appear in the advertised schema
    or the audit log.

    ``meta`` is the tool's ``_meta`` as ``tools/list`` advertises it (MCP Apps:
    ``{"ui": {"resourceUri": ..., "visibility": [...]}}``); ``text`` renders the
    result's text content when the JSON dump is not what a reader should get.
    """

    def decorator(fn: Callable) -> Callable:
        if name in _REGISTRY:
            raise ValueError(f"Tool {name!r} already registered")
        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=fn,
            is_write=is_write,
            wants_progress=wants_progress,
            meta=meta,
            text=text,
        )
        return fn

    return decorator


def list_tools() -> list[dict]:
    """Return tool catalog in MCP tools/list shape."""
    out = []
    for t in _REGISTRY.values():
        entry = {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
        if t.meta:
            entry["_meta"] = t.meta
        out.append(entry)
    return out


def get_tool(name: str) -> Tool | None:
    return _REGISTRY.get(name)


class MCPToolError(Exception):
    """Raised by tool handlers to return a structured error.

    Attributes:
        code: One of INVALID_JSX, INVALID_SCHEMA, NOT_FOUND, PERMISSION_DENIED,
              VERSION_CONFLICT, RATE_LIMITED, UPSTREAM_ERROR.
        message: Human-readable message.
        details: Optional dict with error-specific details.
    """

    def __init__(self, code: str, message: str, details: dict | None = None):
        super().__init__(message)
        self.code = code
        self.message = message
        self.details = details or {}
