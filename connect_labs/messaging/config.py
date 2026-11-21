"""Messaging's settings, per program, in labs Settings (`scope_config`).

``messaging.settings`` (program layer) is the program's own switch and the name its channel
shows; it is OFF until someone turns it on, so a program never starts messaging its Connect
Workers by accident.

Read here as the SYSTEM (handlers, tasks and the webhooks run with no person): a program's
own layer is stored by program id, so no org tree is needed to read it.
"""

from __future__ import annotations

import copy

from connect_labs.scope_config.namespaces import Namespace, register_namespace

SETTINGS = "messaging.settings"

SETTINGS_NAMESPACE = register_namespace(
    Namespace(
        key=SETTINGS,
        label="Messaging",
        description=(
            "Whether labs may message this program's Connect Workers over PersonalID, and how its channel is "
            "named in their app. Off until turned on."
        ),
        schema={
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "title": "Messaging on",
                    "description": "Off: nothing is sent to this program's Connect Workers, whatever starts it.",
                },
                "channel_name": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 80,
                    "title": "Channel name",
                    "description": "What the Connect Worker's app calls this channel, e.g. the program's name.",
                },
                "recently_coached_days": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 365,
                    "title": "Recently coached (days)",
                    "description": (
                        "How long after a coaching conversation closes the Connect Worker counts as recently "
                        "coached (the messaging.recently_coached facet a follow-up playbook can match)."
                    ),
                },
                "timezone": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 64,
                    "title": "Default time zone",
                    "description": (
                        "For quiet hours, when a Connect Worker's own time zone is not known (e.g. Africa/Lagos)."
                    ),
                },
            },
            "additionalProperties": False,
        },
        defaults={"enabled": False, "channel_name": "Connect", "timezone": "UTC", "recently_coached_days": 14},
        layers=frozenset({"program"}),
    )
)


def program_layer(namespace_key: str, program_id) -> dict:
    """The program's own stored layer of `namespace_key` (no defaults), or {}."""
    from connect_labs.scope_config.models import ScopeConfig

    if program_id is None:
        return {}
    row = ScopeConfig.objects.filter(namespace=namespace_key, scope_type="program", scope_key=str(program_id)).first()
    return copy.deepcopy(row.data) if row else {}


def program_settings(program_id) -> dict:
    return {**SETTINGS_NAMESPACE.defaults, **program_layer(SETTINGS, program_id)}


def program_enabled(program_id) -> bool:
    return bool(program_settings(program_id).get("enabled"))


def max_turns_for(thread) -> int | None:
    """The thread's playbook's limit on automated messages, or None (no playbook, or no limit)."""
    from connect_labs.messaging import playbooks

    playbook = playbooks.for_thread(thread)
    if playbook is None:
        return None
    return (playbook.get("caps") or {}).get("max_turns")
