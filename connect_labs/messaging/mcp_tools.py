"""messaging_* -- labs Messaging over MCP, as the person calling.

Playbooks (list / get / set / dry_run), threads (list / get), starting a playbook (preview,
then confirm with the token the preview returned -- show the person the preview and get
their yes in between), a staff reply from the Inbox, and closing a conversation. Every tool runs as the caller and
sees only programs and opportunities they hold (`messaging/access.py`).

Not on the no-user-visit-data endpoint: a thread is a Connect Worker's own words.
"""

from __future__ import annotations

from connect_labs.labs.access.scopes import Caller
from connect_labs.mcp.connect_token import require_connect_token
from connect_labs.mcp.tool_registry import MCPToolError, register

_PROGRAM = {"type": "integer", "minimum": 1, "description": "The program's Connect id."}
_KEY = {"type": "string", "pattern": "^[a-z0-9][a-z0-9_-]{0,63}$", "description": "The playbook's key."}
_WORKERS = {
    "type": "array",
    "minItems": 1,
    "maxItems": 200,
    "description": "PersonalID usernames, or {username, opportunity_id} to name the opportunity.",
    "items": {
        "anyOf": [
            {"type": "string", "minLength": 1, "maxLength": 150},
            {
                "type": "object",
                "properties": {
                    "username": {"type": "string", "minLength": 1, "maxLength": 150},
                    "opportunity_id": {"type": "integer"},
                },
                "required": ["username"],
                "additionalProperties": False,
            },
        ]
    },
}

_CODES = {
    "forbidden": "PERMISSION_DENIED",
    "not_found": "NOT_FOUND",
    "invalid": "INVALID_SCHEMA",
    "confirm_mismatch": "VERSION_CONFLICT",
}


def _caller(user) -> Caller:
    return Caller(user=user, access_token=require_connect_token(user))


def _run(fn, *args, **kwargs):
    from connect_labs.messaging import access
    from connect_labs.messaging.service import MessagingError
    from connect_labs.scope_config import service as settings_service

    try:
        return fn(*args, **kwargs)
    except MessagingError as e:
        raise MCPToolError(_CODES.get(e.code, "BAD_REQUEST"), e.public_message, {"code": e.code}) from None
    except access.NoAccess as e:
        raise MCPToolError("NOT_FOUND", f"No such conversation that you can see ({e}).") from None
    except settings_service.Forbidden as e:
        raise MCPToolError("PERMISSION_DENIED", str(e)) from None
    except settings_service.Invalid as e:
        raise MCPToolError("INVALID_SCHEMA", str(e)) from None
    except settings_service.VersionConflict as e:
        raise MCPToolError("VERSION_CONFLICT", str(e)) from None


def _require_program(caller, program_id) -> None:
    from connect_labs.messaging import access

    reason = access.may_use_program(caller, program_id)
    if reason:
        raise MCPToolError("PERMISSION_DENIED", reason)


def _brief(key: str, playbook: dict) -> dict:
    return {
        "key": key,
        "version": playbook.get("version"),
        "label": playbook.get("label") or key,
        "direction": playbook.get("direction"),
        "enabled": playbook.get("enabled", True) is not False,
        "trigger": playbook.get("trigger"),
        "handler": playbook.get("handler"),
        "approval": playbook.get("approval"),
        "priority": playbook.get("priority"),
    }


@register(
    name="messaging_playbook_list",
    description=(
        "A program's messaging playbooks (labs Messaging with Connect Workers over PersonalID): key, version, "
        "direction, trigger, handler, approval. Also says whether messaging is on for the program."
    ),
    input_schema={
        "type": "object",
        "properties": {"program_id": _PROGRAM},
        "required": ["program_id"],
        "additionalProperties": False,
    },
)
def messaging_playbook_list(user, program_id):
    from connect_labs.messaging import config, flows, playbooks, service

    caller = _caller(user)
    _require_program(caller, program_id)
    return {
        "program_id": program_id,
        "messaging_on": service.enabled() and config.program_enabled(program_id),
        "flows": sorted(flows.FLOWS),
        "playbooks": [_brief(k, p) for k, p in sorted(playbooks.all_for(program_id).items())],
    }


@register(
    name="messaging_playbook_get",
    description="One messaging playbook in full, as stored (see messaging_playbook_set for its shape).",
    input_schema={
        "type": "object",
        "properties": {"program_id": _PROGRAM, "key": _KEY},
        "required": ["program_id", "key"],
        "additionalProperties": False,
    },
)
def messaging_playbook_get(user, program_id, key):
    from connect_labs.messaging import playbooks

    caller = _caller(user)
    _require_program(caller, program_id)
    playbook = playbooks.get(program_id, key)
    if playbook is None:
        raise MCPToolError("NOT_FOUND", f"Program {program_id} has no messaging playbook {key!r}.")
    return {"program_id": program_id, "key": key, "playbook": playbook}


@register(
    name="messaging_playbook_set",
    description=(
        "Create or replace one messaging playbook of a program (null removes it); the version moves on by itself. "
        "Shape: {direction: outbound|inbound, trigger (outbound): {type: person|schedule|state_event|api, ...}, "
        "eligible: {stages, facets}, handler: {kind: agent, agent, skill}|{kind: flow, key}|{kind: human, queue}|"
        "{kind: none}, opening (verbatim; {first_name}, {name}, {program} filled in), approval: {mode: "
        "per_send|per_batch|auto_within_caps, approved_by}, caps: {max_turns, threads_per_worker_per_week, "
        "quiet_hours: {start, end}}, exit, handoffs, context, tools, variants, priority, enabled}. Automatic "
        "triggers need auto_within_caps with approved_by. Validated, attributed and undoable (labs Settings, "
        "namespace messaging.playbooks)."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": _PROGRAM,
            "key": _KEY,
            "playbook": {"type": ["object", "null"], "description": "The whole playbook, or null to remove it."},
        },
        "required": ["program_id", "key", "playbook"],
        "additionalProperties": False,
    },
    is_write=True,
)
def messaging_playbook_set(user, program_id, key, playbook):
    from connect_labs.messaging import playbooks

    if playbook is not None and "version" not in playbook:
        playbook = {**playbook, "version": 1}
    stored = _run(playbooks.set_playbook, _caller(user), program_id, key, playbook, via="mcp:messaging_playbook_set")
    return {"program_id": program_id, "key": key, "playbook": stored or None}


@register(
    name="messaging_playbook_dry_run",
    description=(
        "Who in the program is eligible for a playbook right now, and the exact opening each would get (or why "
        "each other Connect Worker is left out: eligibility, caps, consent, quiet hours). Sends nothing."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": _PROGRAM,
            "key": _KEY,
            "context": {"type": "object", "description": "Facts a flow fills in, e.g. {item, days_left}."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 1000, "default": 200},
        },
        "required": ["program_id", "key"],
        "additionalProperties": False,
    },
)
def messaging_playbook_dry_run(user, program_id, key, context=None, limit=200):
    from connect_labs.messaging import router

    return _run(router.dry_run, _caller(user), program_id=program_id, playbook_key=key, limit=limit, context=context)


@register(
    name="messaging_thread_list",
    description=(
        "Conversations with Connect Workers you can see, waiting-for-a-person first: id, Connect Worker, program, "
        "playbook, handler, state, why it waits, last message time."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": _PROGRAM,
            "playbook": {"type": "string"},
            "handler": {"enum": ["agent", "flow", "human", "none"]},
            "state": {"enum": ["open", "closed", "all"], "default": "open"},
            "needs_attention": {"type": "boolean"},
            "worker": {"type": "string", "description": "A username, or part of a name."},
            "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 50},
        },
        "additionalProperties": False,
    },
)
def messaging_thread_list(
    user, program_id=None, playbook=None, handler=None, state="open", needs_attention=None, worker=None, limit=50
):
    from connect_labs.messaging import inbox

    rows = _run(
        inbox.threads,
        _caller(user),
        program=program_id,
        playbook=playbook,
        handler=handler,
        state=state,
        attention=needs_attention,
        worker=worker,
    )
    return {"threads": [_thread_brief(t) for t in rows[:limit]]}


def _thread_brief(thread) -> dict:
    return {
        "id": thread.pk,
        "worker": {"username": thread.worker.username, "name": thread.worker.name},
        "program_id": thread.program_id,
        "opportunity_id": thread.opportunity_id,
        "direction": thread.direction,
        "playbook": thread.playbook_key or None,
        "playbook_version": thread.playbook_version,
        "variant": thread.variant or None,
        "handler": {"kind": thread.handler_kind, "ref": thread.handler_ref},
        "taken_over_by": thread.taken_over_by or None,
        "state": thread.state,
        "needs_attention": thread.needs_attention,
        "attention_reason": thread.attention_reason or None,
        "opened_at": thread.opened_at.isoformat(),
        "closed_at": thread.closed_at.isoformat() if thread.closed_at else None,
        "close_reason": thread.close_reason or None,
        "last_message_at": thread.last_message_at.isoformat() if thread.last_message_at else None,
    }


@register(
    name="messaging_thread_get",
    description=(
        "One conversation with a Connect Worker: its facts, how it started and was approved, and the transcript "
        "(every message, any author, with delivery status; notes are staff-only and never sent)."
    ),
    input_schema={
        "type": "object",
        "properties": {"thread_id": {"type": "integer", "minimum": 1}},
        "required": ["thread_id"],
        "additionalProperties": False,
    },
)
def messaging_thread_get(user, thread_id):
    from connect_labs.messaging import inbox

    thread = _run(inbox.get_thread, _caller(user), thread_id)
    return {
        **_thread_brief(thread),
        "started_by": thread.started_by,
        "approval": thread.approval,
        "state_snapshot": thread.state_snapshot,
        "summaries": [
            {"text": s.text, "author_kind": s.author_kind, "outcome": s.outcome, "at": s.created_at.isoformat()}
            for s in thread.summaries.all()[:10]
        ],
        "messages": [
            {
                "id": m.pk,
                "direction": m.direction,
                "author_kind": m.author_kind,
                "author_ref": m.author_ref,
                "text": m.text,
                "at": m.created_at.isoformat(),
                "delivery_status": m.delivery_status,
                "visibility": m.visibility,
                **({"failure_reason": m.failure_reason} if m.failure_reason else {}),
                **({"held_until": m.scheduled_for.isoformat()} if m.scheduled_for else {}),
            }
            for m in thread.messages.order_by("created_at", "id")
        ],
    }


@register(
    name="messaging_start",
    description=(
        "Start a program's outbound messaging playbook with Connect Workers, in two calls. Without `confirm`: a "
        "preview -- each worker's exact opening, or why they are left out -- and a single-use `confirm` token. "
        "SHOW THE PERSON THE PREVIEW AND GET THEIR YES, then call again with the same arguments and `confirm` to "
        "send. A per_send playbook starts one Connect Worker per call."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "program_id": _PROGRAM,
            "playbook": _KEY,
            "workers": _WORKERS,
            "context": {"type": "object", "description": "Facts a flow fills in, e.g. {item, days_left}."},
            "confirm": {"type": "string", "description": "The token from this call's preview. Omit to preview."},
        },
        "required": ["program_id", "playbook", "workers"],
        "additionalProperties": False,
    },
    is_write=True,
)
def messaging_start(user, program_id, playbook, workers, context=None, confirm=None):
    from connect_labs.messaging import router

    caller = _caller(user)
    if not confirm:
        return _run(
            router.preview, caller, program_id=program_id, playbook_key=playbook, workers=workers, context=context
        )
    return _run(
        router.commit,
        caller,
        program_id=program_id,
        playbook_key=playbook,
        workers=workers,
        context=context,
        confirm=confirm,
        via="mcp:messaging_start",
    )


@register(
    name="messaging_inbox_reply",
    description=(
        "Reply to a Connect Worker in a conversation, as you (a staff reply in the labs Inbox). Replying to a "
        "conversation an agent or flow is handling takes it over. Show the person the text and get their yes "
        "before sending: it goes to the Connect Worker's phone."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "thread_id": {"type": "integer", "minimum": 1},
            "text": {"type": "string", "minLength": 1, "maxLength": 4000},
        },
        "required": ["thread_id", "text"],
        "additionalProperties": False,
    },
    is_write=True,
)
def messaging_inbox_reply(user, thread_id, text):
    from connect_labs.messaging import inbox

    message = _run(inbox.reply, _caller(user), thread_id, text)
    return {
        "thread_id": thread_id,
        "message_id": message.pk,
        "delivery_status": message.delivery_status,
        "text": message.text,
    }


@register(
    name="messaging_thread_close",
    description=(
        "Close a conversation with a Connect Worker, as you (the labs Inbox's 'Close conversation'). Its handler "
        "stops -- an agent's answer still on its way is never sent -- and the worker can be sent a new opening "
        "(messaging_start refuses one while a conversation is open). Messaging stays on for the worker: this is "
        "not a STOP. Closing a closed conversation changes nothing. Returns the conversation's new state."
    ),
    input_schema={
        "type": "object",
        "properties": {
            "thread_id": {"type": "integer", "minimum": 1},
            "reason": {"type": "string", "maxLength": 150, "description": "Why, kept with the conversation."},
        },
        "required": ["thread_id"],
        "additionalProperties": False,
    },
    is_write=True,
)
def messaging_thread_close(user, thread_id, reason=None):
    from connect_labs.messaging import inbox

    thread = _run(inbox.close, _caller(user), thread_id, reason or "", via="MCP")
    return _thread_brief(thread)
