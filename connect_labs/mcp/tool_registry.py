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
    #: Run as a durable job instead of inside the request (``connect_labs/mcp/jobs.py``).
    long_running: bool = False
    #: Where a long-running tool's job runs: "worker" (the Celery queue) or "standalone"
    #: (a Fargate task of its own, for jobs of an hour or more).
    job_runner: str = "worker"


JOB_RUNNERS = ("worker", "standalone")

#: The argument every long-running tool takes, added to its schema by ``register``.
WAIT_PARAM = {
    "type": "boolean",
    "default": True,
    "description": (
        "true (default): wait briefly (about half a minute) and return the result if the job has "
        "finished by then; otherwise return a job_id to poll with job_status. false: return the "
        "job_id at once. The job runs either way, and survives a labs deploy."
    ),
}


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
    long_running: bool = False,
    job_runner: str = "worker",
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

    Set long_running=True for a tool that can take minutes. The call then does not run
    the handler in the web request -- which a deploy, a client timeout or the web
    tier's request cap can cut off with the result lost -- but queues it as a durable
    job (``connect_labs/mcp/jobs.py``) that runs AS the caller, under the same
    restricted-scope rules, and survives a deploy. The call returns
    ``{job_id, state, poll_with: "job_status"}`` (or the result itself, if the job
    finishes within a short wait), and the tool gains a ``wait`` argument. The handler
    is unchanged, but it must be safe to run again from the start: a job interrupted by
    a deploy is run again. ``job_runner="standalone"`` runs it on a Fargate task of its
    own instead of the worker service -- only for jobs of an hour or more.
    """
    if job_runner not in JOB_RUNNERS:
        raise ValueError(f"job_runner must be one of {JOB_RUNNERS}, not {job_runner!r}")

    def decorator(fn: Callable) -> Callable:
        if name in _REGISTRY:
            raise ValueError(f"Tool {name!r} already registered")
        schema = input_schema
        if long_running:
            schema = {**input_schema, "properties": {**input_schema.get("properties", {}), "wait": WAIT_PARAM}}
        _REGISTRY[name] = Tool(
            name=name,
            description=description,
            input_schema=schema,
            handler=fn,
            is_write=is_write,
            wants_progress=wants_progress,
            meta=meta,
            text=text,
            long_running=long_running,
            job_runner=job_runner,
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
