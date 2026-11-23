"""What every worker tool stands on: the call's context, the registry, and argument helpers.

A tool is a function ``(ctx, **arguments) -> dict`` registered with `tool`, next to its code:

    @tool("One line the agent reads.", params(limit={"type": "integer"}))
    def worker_threads(ctx, limit=20): ...

Its name is the function's, so the registry, the schema and the code cannot disagree, and
``mcp.py`` validates each call's arguments against the schema before the function runs.
"""

from __future__ import annotations

import datetime as dt
from collections.abc import Callable
from dataclasses import dataclass

from django.utils import timezone
from django.utils.dateparse import parse_date, parse_datetime

from connect_labs.messaging.canopy_bridge.visibility import Scope, Scrubber
from connect_labs.messaging.models import Message, Thread


class ToolRefused(Exception):
    """A call labs will not answer. ``code`` is stable; the message is for the agent."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class Ctx:
    """Who a call is about and where it comes from: resolved by labs from canopy's subject.

    Every read goes through ``scope`` (``visibility.Scope``), the rules the Worker Brief follows.
    """

    scope: Scope
    subject: object
    playbook: dict

    @property
    def worker(self):
        return self.scope.worker

    @property
    def thread(self) -> Thread:
        return self.scope.thread

    @property
    def programs(self) -> frozenset:
        return self.scope.programs

    @property
    def scrub(self) -> Scrubber:
        return self.scope.scrub

    @property
    def author_ref(self) -> str:
        return f"{self.subject.agent}:{self.thread.handler_ref.partition(':')[2]}".rstrip(":")


# ---------------------------------------------------------------------------
# The registry
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class WorkerTool:
    name: str
    description: str
    input_schema: dict
    handler: Callable
    is_write: bool = False


#: Every worker tool, by name, filled by `tool` as ``reading``, ``memory`` and ``thread`` import.
TOOLS: dict[str, WorkerTool] = {}


def params(*, required: tuple[str, ...] = (), **properties) -> dict:
    """A tool's input schema: an object of exactly these properties."""
    schema = {"type": "object", "additionalProperties": False, "properties": properties}
    if required:
        schema["required"] = list(required)
    return schema


def tool(description: str, input_schema: dict | None = None, *, write: bool = False):
    """Register the decorated function as a worker tool, named after it."""

    def register(fn: Callable) -> Callable:
        if fn.__name__ in TOOLS:
            raise RuntimeError(f"worker tool {fn.__name__} is registered twice")
        TOOLS[fn.__name__] = WorkerTool(fn.__name__, description, input_schema or params(), fn, write)
        return fn

    return register


# ---------------------------------------------------------------------------
# Arguments and output
# ---------------------------------------------------------------------------


def iso(value):
    return value.isoformat() if value else None


def bounded_int(value, default, low, high) -> int:
    try:
        value = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def since(value):
    """An ISO 8601 date or time an agent passed, as an aware datetime (or None)."""
    if not value:
        return None
    parsed = parse_datetime(str(value))
    if parsed is None:
        day = parse_date(str(value))
        if day is None:
            raise ToolRefused("invalid", "since is an ISO 8601 date or time, e.g. 2026-11-01")
        parsed = dt.datetime.combine(day, dt.time.min)
    return parsed if timezone.is_aware(parsed) else timezone.make_aware(parsed, dt.UTC)


def author_of(message: Message) -> str:
    if message.direction == "in":
        return "worker"
    return f"{message.author_kind}{':' + message.author_ref if message.author_ref else ''}"
