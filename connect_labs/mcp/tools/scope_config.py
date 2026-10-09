"""Settings over MCP: read, change, history and undo of a scope's config (scope_config/).

Config holds ids, labels and switches, never visits, so every tool here is on
the no-user-visit-data endpoint too (token_scopes.py).
"""

from __future__ import annotations

from connect_labs.labs.access.scopes import Caller
from connect_labs.scope_config import service
from connect_labs.scope_config.namespaces import all_namespaces
from connect_labs.scope_config.scopes import TYPES, Scope

from ..connect_token import require_connect_token
from ..tool_registry import MCPToolError, register

_SCOPE_PROPS = {
    "namespace": {
        "type": "string",
        "description": "Which settings, e.g. 'supply' or 'labs'. See labs_config_namespaces.",
    },
    "scope_type": {"type": "string", "enum": list(TYPES)},
    "scope_key": {
        "type": "string",
        "description": "An organisation's slug, a programme's or opportunity's id, or a username.",
    },
}
_REQUIRED = ["namespace", "scope_type", "scope_key"]


def _caller(user) -> Caller:
    return Caller(user=user, access_token=require_connect_token(user))


def _scope(scope_type, scope_key) -> Scope:
    try:
        return Scope.of(scope_type, scope_key)
    except ValueError as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc)) from None


def _run(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except KeyError as exc:
        raise MCPToolError("NOT_FOUND", str(exc).strip("'\"")) from None
    except service.Forbidden as exc:
        raise MCPToolError("PERMISSION_DENIED", str(exc)) from None
    except service.VersionConflict as exc:
        raise MCPToolError("VERSION_CONFLICT", str(exc)) from None
    except service.Invalid as exc:
        raise MCPToolError("INVALID_SCHEMA", str(exc)) from None


@register(
    name="labs_config_namespaces",
    description=(
        "The settings namespaces labs knows (e.g. 'supply': Supply's tabs; 'labs': the page a scope opens on), "
        "each with the layers that may set it and the JSON schema of one layer."
    ),
    input_schema={"type": "object", "properties": {}, "additionalProperties": False},
)
def labs_config_namespaces(user):
    return {
        "namespaces": [
            {
                "key": ns.key,
                "label": ns.label,
                "description": ns.description,
                "layers": sorted(ns.layers),
                "defaults": ns.defaults,
                "schema": ns.schema,
            }
            for ns in all_namespaces()
        ]
    }


@register(
    name="labs_config_get",
    description=(
        "A scope's settings in one namespace: the resolved value (defaults <- organisation <- programme <- "
        "opportunity), which layer set each key (provenance), every layer's own data and version, and this "
        "scope's own layer (`data`, `version`) -- pass that version to labs_config_set."
    ),
    input_schema={"type": "object", "properties": _SCOPE_PROPS, "required": _REQUIRED, "additionalProperties": False},
)
def labs_config_get(user, namespace, scope_type, scope_key):
    return _run(service.get, namespace, _scope(scope_type, scope_key), _caller(user))


@register(
    name="labs_config_set",
    description=(
        "Change a scope's own settings layer with a JSON merge patch: maps merge by key, lists replace whole, "
        "null removes a key. Pass expected_version (the layer's `version` from labs_config_get) so a change "
        "made meanwhile by someone else is refused rather than overwritten. Attributed and undoable."
    ),
    input_schema={
        "type": "object",
        "properties": {
            **_SCOPE_PROPS,
            "patch": {"type": "object"},
            "expected_version": {"type": "integer", "minimum": 0},
        },
        "required": [*_REQUIRED, "patch", "expected_version"],
        "additionalProperties": False,
    },
    is_write=True,
)
def labs_config_set(user, namespace, scope_type, scope_key, patch, expected_version):
    return _run(
        service.update,
        namespace,
        _scope(scope_type, scope_key),
        patch,
        _caller(user),
        expected_version=expected_version,
        via="mcp:labs_config_set",
    )


@register(
    name="labs_config_history",
    description="The changes to a scope's own settings layer, newest first: who, when, how, and before/after.",
    input_schema={"type": "object", "properties": _SCOPE_PROPS, "required": _REQUIRED, "additionalProperties": False},
)
def labs_config_history(user, namespace, scope_type, scope_key):
    return {"changes": _run(service.history, namespace, _scope(scope_type, scope_key), _caller(user))}


@register(
    name="labs_config_undo",
    description=(
        "Put a scope's settings layer back as it was before a change (default: the latest one). Recorded as a "
        "new change, so it can itself be undone."
    ),
    input_schema={
        "type": "object",
        "properties": {**_SCOPE_PROPS, "change_id": {"type": "integer", "minimum": 1}},
        "required": _REQUIRED,
        "additionalProperties": False,
    },
    is_write=True,
)
def labs_config_undo(user, namespace, scope_type, scope_key, change_id=None):
    return _run(
        service.undo,
        namespace,
        _scope(scope_type, scope_key),
        _caller(user),
        change_id=change_id,
        via="mcp:labs_config_undo",
    )
