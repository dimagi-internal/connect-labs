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


_REGISTRY: dict[str, Tool] = {}


def register(
    *,
    name: str,
    description: str,
    input_schema: dict,
) -> Callable[[Callable], Callable]:
    """Decorator that registers a tool handler.

    The handler receives kwargs matching `input_schema.properties` plus a
    `user` kwarg (Django User, from the authenticated PAT). It should return
    a JSON-serializable value on success or raise MCPToolError on failure.
    """

    def decorator(fn: Callable) -> Callable:
        if name in _REGISTRY:
            raise ValueError(f"Tool {name!r} already registered")
        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema=input_schema,
            handler=fn,
        )
        return fn

    return decorator


def list_tools() -> list[dict]:
    """Return tool catalog in MCP tools/list shape."""
    return [
        {
            "name": t.name,
            "description": t.description,
            "inputSchema": t.input_schema,
        }
        for t in _REGISTRY.values()
    ]


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
