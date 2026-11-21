"""Playbooks: what a program's conversations with its Connect Workers are for, and their rules.

A program's playbooks live in labs Settings, namespace ``messaging.playbooks``, program layer:
``{"playbooks": {<key>: <playbook>}}``. A playbook says:

    version        an integer; `set_playbook` raises it whenever anything else changes, and a
                   thread records the key@version it opened under (`playbook_at` reads an old
                   version back from the Settings history)
    direction      outbound (labs begins) | inbound (the Connect Worker begins)
    trigger        outbound only: person | schedule {every: daily|weekly, hour, weekday} |
                   state_event {kinds, stages} | api
    eligible       over the worker's `WorkerState`: {stages: [...], facets: {"tasks.overdue": {"gte": 1}}}
    handler        {kind: agent, agent, skill} | {kind: flow, key} | {kind: human, queue} | {kind: none}
    opening        sent verbatim to open an outbound thread ({first_name}, {name}, {program} and
                   the opener's context keys are filled in); a flow can supply its own
    approval       {mode: per_send | per_batch | auto_within_caps, approved_by, approved_at};
                   automatic triggers need auto_within_caps, which needs approved_by
    caps           {max_turns, threads_per_worker_per_week, quiet_hours: {start, end}} -- quiet
                   hours are in the worker's time zone (else the program's)
    exit           {keywords, idle_hours}
    handoffs       [{to: human | <playbook key>, when: {keywords}, note}]
    context        {depth: thread | program | worker, other_programs, program} -- ``program`` is the
                   program's own context for an agent (script, app summary, persona notes; Markdown,
                   at most 4 KB), put in every Worker Brief
    tools          the worker tools an agent on this playbook may use (subject-bound, canopy side)
    variants       [{key, weight, opening?, handler?}], assigned by a hash of the worker's username
    priority       inbound playbooks are tried lowest first (Settings stores maps, so order is explicit)
    enabled        default true

Validation is the namespace's schema plus `_check` (rules a schema cannot say).
"""

from __future__ import annotations

import copy
import hashlib
import re

from connect_labs.scope_config.namespaces import Namespace, register_namespace
from connect_labs.workers.models import Stage

PLAYBOOKS = "messaging.playbooks"

KEY_PATTERN = r"^[a-z0-9][a-z0-9_-]{0,63}$"
APPROVAL_MODES = ("per_send", "per_batch", "auto_within_caps")
TRIGGERS = ("person", "schedule", "state_event", "api")
AUTOMATIC_TRIGGERS = frozenset({"schedule", "state_event", "api"})
HANDLER_KINDS = ("agent", "flow", "human", "none")

#: The worker tools a playbook may grant an agent (the canopy bridge serves them, bound to the subject).
WORKER_TOOLS = (
    "worker_profile",
    "worker_timeline",
    "worker_threads",
    "worker_thread",
    "worker_history_search",
    "worker_tasks",
    "worker_learn",
    "worker_visits",
    "worker_payments",
    "worker_facts",
    "worker_fact_record",
    "worker_fact_retract",
    "thread_record_outcome",
    "thread_handoff",
    "thread_close",
)

_TIME = {"type": "string", "pattern": r"^([01][0-9]|2[0-3]):[0-5][0-9]$"}
_HANDLER = {
    "type": "object",
    "properties": {
        "kind": {"enum": list(HANDLER_KINDS)},
        "agent": {"type": "string", "minLength": 1, "maxLength": 100},
        "skill": {"type": "string", "minLength": 1, "maxLength": 100},
        "key": {"type": "string", "minLength": 1, "maxLength": 64},
        "queue": {"type": "string", "minLength": 1, "maxLength": 64},
    },
    "required": ["kind"],
    "additionalProperties": False,
}
_COMPARISON = {
    "type": "object",
    "properties": {op: {} for op in ("eq", "ne", "gt", "gte", "lt", "lte", "in", "exists")},
    "minProperties": 1,
    "additionalProperties": False,
}
_KEYWORDS = {"type": "array", "items": {"type": "string", "minLength": 1, "maxLength": 40}, "maxItems": 20}

PLAYBOOK_SCHEMA = {
    "type": "object",
    "properties": {
        "version": {"type": "integer", "minimum": 1},
        "label": {"type": "string", "maxLength": 120},
        "description": {"type": "string", "maxLength": 1000},
        "enabled": {"type": "boolean"},
        "priority": {"type": "integer", "minimum": 0, "maximum": 1000},
        "direction": {"enum": ["outbound", "inbound"]},
        "trigger": {
            "type": "object",
            "properties": {
                "type": {"enum": list(TRIGGERS)},
                "every": {"enum": ["daily", "weekly"]},
                "hour": {"type": "integer", "minimum": 0, "maximum": 23},
                "weekday": {"type": "integer", "minimum": 0, "maximum": 6},
                "kinds": {"type": "array", "items": {"type": "string", "maxLength": 40}, "maxItems": 20},
                "stages": {"type": "array", "items": {"enum": list(Stage.values)}},
            },
            "required": ["type"],
            "additionalProperties": False,
        },
        "eligible": {
            "type": "object",
            "properties": {
                "stages": {"type": "array", "items": {"enum": list(Stage.values)}, "minItems": 1},
                "facets": {"type": "object", "additionalProperties": _COMPARISON},
            },
            "additionalProperties": False,
        },
        "handler": _HANDLER,
        "opening": {"type": "string", "minLength": 1, "maxLength": 2000},
        "approval": {
            "type": "object",
            "properties": {
                "mode": {"enum": list(APPROVAL_MODES)},
                "approved_by": {"type": "string", "minLength": 1, "maxLength": 200},
                "approved_at": {"type": "string", "maxLength": 40},
            },
            "required": ["mode"],
            "additionalProperties": False,
        },
        "caps": {
            "type": "object",
            "properties": {
                "max_turns": {"type": "integer", "minimum": 1, "maximum": 200},
                "threads_per_worker_per_week": {"type": "integer", "minimum": 0, "maximum": 50},
                "quiet_hours": {
                    "type": "object",
                    "properties": {"start": _TIME, "end": _TIME},
                    "required": ["start", "end"],
                    "additionalProperties": False,
                },
            },
            "additionalProperties": False,
        },
        "exit": {
            "type": "object",
            "properties": {"keywords": _KEYWORDS, "idle_hours": {"type": "integer", "minimum": 1, "maximum": 24 * 60}},
            "additionalProperties": False,
        },
        "handoffs": {
            "type": "array",
            "maxItems": 20,
            "items": {
                "type": "object",
                "properties": {
                    "to": {"type": "string", "minLength": 1, "maxLength": 64},
                    "when": {
                        "type": "object",
                        "properties": {"keywords": _KEYWORDS},
                        "required": ["keywords"],
                        "additionalProperties": False,
                    },
                    "note": {"type": "string", "maxLength": 300},
                },
                "required": ["to", "when"],
                "additionalProperties": False,
            },
        },
        "context": {
            "type": "object",
            "properties": {
                "depth": {"enum": ["thread", "program", "worker"]},
                "other_programs": {"type": "boolean"},
                # The program's own context for an agent: its script, an app summary, persona notes
                # (Markdown). Put in every Worker Brief as its own section (canopy_bridge/brief.py).
                "program": {"type": "string", "maxLength": 4096},
            },
            "additionalProperties": False,
        },
        "tools": {"type": "array", "items": {"enum": list(WORKER_TOOLS)}, "uniqueItems": True},
        "variants": {
            "type": "array",
            "maxItems": 10,
            "items": {
                "type": "object",
                "properties": {
                    "key": {"type": "string", "pattern": KEY_PATTERN},
                    "weight": {"type": "integer", "minimum": 1, "maximum": 1000},
                    "opening": {"type": "string", "minLength": 1, "maxLength": 2000},
                    "handler": _HANDLER,
                },
                "required": ["key", "weight"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["version", "direction", "handler"],
    "additionalProperties": False,
}

LAYER_SCHEMA = {
    "type": "object",
    "properties": {
        "playbooks": {
            "type": "object",
            "propertyNames": {"pattern": KEY_PATTERN},
            "additionalProperties": PLAYBOOK_SCHEMA,
        }
    },
    "additionalProperties": False,
}


def _check_handler(where: str, handler: dict) -> None:
    from connect_labs.messaging import flows

    kind = handler["kind"]
    if kind == "agent" and not (handler.get("agent") and handler.get("skill")):
        raise ValueError(f"{where}: an agent handler names its agent and skill")
    if kind == "flow":
        if not handler.get("key"):
            raise ValueError(f"{where}: a flow handler names its flow (key)")
        if handler["key"] not in flows.FLOWS:
            raise ValueError(f"{where}: no flow {handler['key']!r}; flows: {', '.join(sorted(flows.FLOWS))}")
    allowed = {"agent": {"agent", "skill"}, "flow": {"key"}, "human": {"queue"}, "none": set()}[kind]
    stray = {k for k in handler if k != "kind"} - allowed
    if stray:
        raise ValueError(f"{where}: a {kind} handler does not take {', '.join(sorted(stray))}")


def check_playbook(key: str, playbook: dict, siblings: dict | None = None) -> None:
    """The rules a schema cannot say about one playbook. Raises ValueError with a message for a person."""
    from connect_labs.messaging import flows

    where = f"playbooks.{key}"
    _check_handler(f"{where}.handler", playbook["handler"])
    outbound = playbook["direction"] == "outbound"
    trigger = playbook.get("trigger") or {}
    approval = playbook.get("approval") or {}
    if outbound:
        if not trigger:
            raise ValueError(f"{where}: an outbound playbook says what starts it (trigger)")
        if not approval:
            raise ValueError(f"{where}: an outbound playbook says how its messages are approved (approval)")
        handler = playbook["handler"]
        flow = flows.FLOWS.get(handler.get("key")) if handler["kind"] == "flow" else None
        if not playbook.get("opening") and flow is None:
            raise ValueError(f"{where}: an outbound playbook needs an opening, sent verbatim")
        if trigger["type"] in AUTOMATIC_TRIGGERS and approval.get("mode") != "auto_within_caps":
            raise ValueError(
                f"{where}: a {trigger['type']} trigger starts conversations with nobody clicking, so its approval "
                "must be auto_within_caps (approved by a named person)"
            )
        if trigger["type"] == "schedule" and "hour" not in trigger:
            raise ValueError(f"{where}.trigger: a schedule says the hour (UTC) it runs")
        if trigger["type"] == "schedule" and trigger.get("every") == "weekly" and "weekday" not in trigger:
            raise ValueError(f"{where}.trigger: a weekly schedule says the weekday (0 = Monday)")
        if trigger["type"] == "state_event" and not trigger.get("kinds"):
            raise ValueError(f"{where}.trigger: a state_event trigger names the event kinds it starts on")
    elif trigger or approval:
        raise ValueError(
            f"{where}: an inbound playbook is started by the Connect Worker; it has no trigger or approval"
        )
    if approval.get("mode") == "auto_within_caps" and not approval.get("approved_by"):
        raise ValueError(
            f"{where}.approval: auto_within_caps needs approved_by, the person who approved sending without a click"
        )
    for i, variant in enumerate(playbook.get("variants") or []):
        if variant.get("handler"):
            _check_handler(f"{where}.variants[{i}].handler", variant["handler"])
    keys = [v["key"] for v in playbook.get("variants") or []]
    if len(keys) != len(set(keys)):
        raise ValueError(f"{where}.variants: each variant has its own key")
    for handoff in playbook.get("handoffs") or []:
        target = handoff["to"]
        if target != "human" and siblings is not None and target not in siblings:
            raise ValueError(f"{where}.handoffs: no playbook {target!r} to hand off to (or 'human')")
        if target == key:
            raise ValueError(f"{where}.handoffs: a playbook cannot hand off to itself")


def _check_layer(data: dict) -> None:
    playbooks = (data or {}).get("playbooks") or {}
    for key, playbook in playbooks.items():
        check_playbook(key, playbook, playbooks)


NAMESPACE = register_namespace(
    Namespace(
        key=PLAYBOOKS,
        label="Messaging playbooks",
        description=(
            "What this program's conversations with its Connect Workers are for: who is eligible, who answers "
            "(an agent, a flow, a person), the opening, how sends are approved, and the caps."
        ),
        schema=LAYER_SCHEMA,
        defaults={"playbooks": {}},
        layers=frozenset({"program"}),
        check=_check_layer,
    )
)


# ---------------------------------------------------------------------------
# Reading
# ---------------------------------------------------------------------------


def all_for(program_id) -> dict[str, dict]:
    """Every playbook the program has, by key (as stored, including disabled ones)."""
    from connect_labs.messaging.config import program_layer

    return dict((program_layer(PLAYBOOKS, program_id).get("playbooks") or {}).items())


def get(program_id, key: str) -> dict | None:
    playbook = all_for(program_id).get(key)
    return copy.deepcopy(playbook) if playbook else None


def is_enabled(playbook: dict) -> bool:
    return playbook.get("enabled", True) is not False


def inbound_for(program_id) -> list[tuple[str, dict]]:
    """The program's enabled inbound playbooks, in the order they are tried."""
    found = [(k, p) for k, p in all_for(program_id).items() if p.get("direction") == "inbound" and is_enabled(p)]
    return sorted(found, key=lambda item: (item[1].get("priority", 100), item[0]))


def playbook_at(program_id, key: str, version) -> dict | None:
    """The playbook as it was at `version` -- now, or from the Settings history."""
    from connect_labs.scope_config.models import ScopeConfigChange

    current = get(program_id, key)
    if current is not None and (version is None or current.get("version") == version):
        return current
    if program_id is None:
        return None
    changes = ScopeConfigChange.objects.filter(
        config__namespace=PLAYBOOKS, config__scope_type="program", config__scope_key=str(program_id)
    ).order_by("-version")
    for change in changes[:200]:
        old = ((change.after or {}).get("playbooks") or {}).get(key)
        if old and old.get("version") == version:
            return copy.deepcopy(old)
    return None


def for_thread(thread) -> dict | None:
    """The playbook a thread runs under, at the version it opened with, or None."""
    if not thread.playbook_key:
        return None
    return playbook_at(thread.program_id, thread.playbook_key, thread.playbook_version)


# ---------------------------------------------------------------------------
# Variants, openings
# ---------------------------------------------------------------------------


def assign_variant(playbook: dict, key: str, username: str) -> dict | None:
    """The worker's variant: stable for (playbook, worker), in proportion to the weights."""
    variants = playbook.get("variants") or []
    if not variants:
        return None
    total = sum(v["weight"] for v in variants)
    point = int.from_bytes(hashlib.sha256(f"{key}:{username}".encode()).digest()[:8], "big") % total
    for variant in variants:
        if point < variant["weight"]:
            return variant
        point -= variant["weight"]
    return variants[-1]


_PLACEHOLDER = re.compile(r"\{([a-z_][a-z0-9_]*)\}")


def fill(text: str, values: dict) -> str:
    """Fill `{name}` placeholders the values know; leave any other brace as written."""
    return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text or "")


# ---------------------------------------------------------------------------
# Changing (as a person, through Settings: membership, history, undo)
# ---------------------------------------------------------------------------


def _content(playbook: dict) -> dict:
    return {k: v for k, v in playbook.items() if k != "version"}


def set_playbook(caller, program_id, key: str, playbook: dict | None, *, via: str = "") -> dict:
    """Create, replace or (with None) remove one playbook; returns it as stored (or {}).

    The version moves on by itself: kept when nothing else changed, else one past the stored
    one (or the caller's, if higher).
    """
    from connect_labs.scope_config import service as settings_service
    from connect_labs.scope_config.merge import diff_patch
    from connect_labs.scope_config.scopes import Scope

    if not re.match(KEY_PATTERN, key or ""):
        raise settings_service.Invalid(f"a playbook key is lowercase letters, digits, - and _ (not {key!r})")
    scope = Scope.of("program", program_id)
    current = all_for(program_id)
    old = current.get(key)
    if playbook is None:
        patch = {"playbooks": {key: None}}
    else:
        new = copy.deepcopy(playbook)
        if old is None:
            new["version"] = max(int(new.get("version") or 1), 1)
        elif _content(old) == _content(new):
            new["version"] = old["version"]
        else:
            new["version"] = max(int(old.get("version") or 0) + 1, int(new.get("version") or 0))
        patch = {"playbooks": {key: diff_patch(old or {}, new) if old else new}}
    settings_service.update(PLAYBOOKS, scope, patch, caller, via=via)
    return get(program_id, key) or {}
