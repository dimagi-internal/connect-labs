"""worker_* MCP tools for staff, run as the person: a Connect Worker's profile, and queueing a sync.

Both answer only within the caller's own Connect access (`workers.access`): a worker
on none of the caller's opportunities is NOT_FOUND, exactly like one labs does not
know. Results never carry a phone number, a suspension reason, a restricted event or
a private note -- an MCP result reaches an agent's context, and those are for staff
reading the Connect Worker page.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.urls import reverse

from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import MCPToolError, register

from .access import accessible_opportunity_ids, view_for
from .models import ConnectWorker
from .profile import build_profile

NOT_FOUND = "No Connect Worker by that username on an opportunity you can access."


def _page_url(username: str) -> str:
    path = reverse("workers:worker", args=[username])
    base = (getattr(settings, "LABS_PUBLIC_URL", "") or "").rstrip("/")
    return base + path if base else path


@register(
    name="worker_get",
    description=(
        "A Connect Worker's profile by PersonalID username, as you may see it: memberships on the "
        "opportunities you can access (with lifecycle stage and learn/verification/payment/task facets), "
        "a timeline summary (counts by kind, newest events), facts, notes and what the profile covers. "
        "Optional program_id or opportunity_id narrows it; kinds filters the timeline. Never includes "
        "phone numbers, suspension reasons or private notes (open `url` for the full page)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "username": {"type": "string", "description": "PersonalID username (Connect username)."},
            "program_id": {"type": "integer"},
            "opportunity_id": {"type": "integer"},
            "kinds": {"type": "array", "items": {"type": "string"}},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        "required": ["username"],
        "additionalProperties": False,
    },
)
def worker_get(user, username, program_id=None, opportunity_id=None, kinds=None, limit=50) -> dict[str, Any]:
    worker = ConnectWorker.objects.filter(username=(username or "").strip(), erased_at__isnull=True).first()
    if worker is None:
        raise MCPToolError("NOT_FOUND", NOT_FOUND)
    view = view_for(user, worker, access_token=require_connect_token(user), on_page=False)
    if view.unknowable and not view.any:
        raise MCPToolError("UPSTREAM_ERROR", "Could not ask Connect what you may access; try again.")
    if not view.any:
        raise MCPToolError("NOT_FOUND", NOT_FOUND)
    if program_id is not None:
        from .models import WorkerMembership

        in_program = set(
            WorkerMembership.objects.filter(worker=worker, program_id=program_id).values_list(
                "opportunity_id", flat=True
            )
        )
        view = type(view)(
            opportunity_ids=frozenset(view.opportunity_ids & in_program),
            program_ids=frozenset({program_id}) & view.program_ids,
        )
        if not view.any:
            raise MCPToolError("NOT_FOUND", f"{NOT_FOUND} (in program {program_id})")
    if opportunity_id is not None and int(opportunity_id) not in view.opportunity_ids:
        raise MCPToolError("NOT_FOUND", f"{NOT_FOUND} (on opportunity {opportunity_id})")
    profile = build_profile(
        worker, view, kinds=kinds or None, opportunity_id=opportunity_id, limit=max(1, min(int(limit or 50), 200))
    )
    profile["url"] = _page_url(worker.username)
    return profile


@register(
    name="worker_sync",
    description=(
        "Queue a fresh read from Connect for one Connect Worker (username) or one opportunity "
        "(opportunity_id) you can access. Returns at once with a task id; the profile updates within "
        "minutes. With username, discover=true also searches every opportunity labs can read for them "
        "(one request per opportunity)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "username": {"type": "string"},
            "opportunity_id": {"type": "integer"},
            "discover": {"type": "boolean", "default": False},
        },
        "additionalProperties": False,
    },
    is_write=True,
)
def worker_sync(user, username=None, opportunity_id=None, discover=False) -> dict[str, Any]:
    from .tasks import sync_opportunity_task, sync_worker_task

    if bool(username) == (opportunity_id is not None):
        raise MCPToolError("BAD_REQUEST", "Give exactly one of username or opportunity_id.")
    token = require_connect_token(user)
    if opportunity_id is not None:
        allowed, _ = accessible_opportunity_ids(user, [opportunity_id], access_token=token)
        if int(opportunity_id) not in allowed:
            raise MCPToolError("NOT_FOUND", f"Opportunity {opportunity_id} is not one you can access.")
        result = sync_opportunity_task.delay(int(opportunity_id))
        return {"queued": True, "task_id": result.id, "opportunity_id": int(opportunity_id)}

    worker = ConnectWorker.objects.filter(username=username.strip(), erased_at__isnull=True).first()
    if worker is None or not view_for(user, worker, access_token=token).any:
        raise MCPToolError(
            "NOT_FOUND",
            f"{NOT_FOUND} To bring in a worker labs has not seen yet, sync their opportunity by opportunity_id.",
        )
    result = sync_worker_task.delay(worker.username, discover=bool(discover))
    return {"queued": True, "task_id": result.id, "username": worker.username, "url": _page_url(worker.username)}
