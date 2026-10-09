"""Read, change, and undo a scope's config.

Who may do what (spec section 1):

    layer          read                     change
    organisation   its members              its members, and Dimagi staff
    programme      its members              its members, and Dimagi staff
    opportunity    its members              its members, and Dimagi staff
    user           that person              that person

"Member" is `labs.access.scopes.may_use`, which also covers labs-only scopes and
fails closed: a caller whose holdings cannot be established is refused with
`scopes.UNKNOWABLE`, never told they hold nothing. A programme member outside
the owning organisation can change the programme's layer but not the
organisation's, because the organisation layer is checked against the
organisation.
"""

from __future__ import annotations

import copy
import logging

from django.db import transaction

from connect_labs.labs.access import scopes as access
from connect_labs.scope_config.merge import merge_patch, resolve_layers
from connect_labs.scope_config.models import ScopeConfig, ScopeConfigChange
from connect_labs.scope_config.namespaces import Namespace, get_namespace, validate_layer
from connect_labs.scope_config.scopes import Scope, chain_for

logger = logging.getLogger(__name__)


class ConfigError(Exception):
    """A config call that cannot proceed, with a message for the person."""


class Forbidden(ConfigError):
    pass


class VersionConflict(ConfigError):
    pass


class Invalid(ConfigError):
    pass


# --- who is asking ---------------------------------------------------------------


def _user(caller: access.Caller):
    return access._user_of(caller)


def username_of(caller: access.Caller) -> str:
    if caller.is_system:
        return "system"
    return getattr(_user(caller), "username", "") or ""


def _is_staff(caller: access.Caller) -> bool:
    return bool(getattr(_user(caller), "is_staff", False))


def tree_for(caller: access.Caller) -> dict:
    """The organisations, programmes and opportunities the caller can see, labs-only included."""
    if caller.request is not None:
        from connect_labs.labs.context import get_org_data

        tree = get_org_data(caller.request)
        if tree:
            return tree
    if caller.is_system:
        return {}
    try:
        data = access._connect_org_data(caller) or {}
    except Exception:  # noqa: BLE001 -- an unreachable Connect leaves only the labs-only tree
        logger.warning("scope config: could not fetch the caller's org tree", exc_info=True)
        data = {}
    user = _user(caller)
    if user is not None and getattr(user, "view_synthetic_opps", False):
        from connect_labs.labs.context import _merge_labs_only_opps

        data = _merge_labs_only_opps(dict(data), user)
    return data


def refusal(caller: access.Caller | None, scope: Scope) -> str | None:
    """Why `caller` may not read or change `scope`'s layer, or None."""
    if caller is None:
        return "no caller: settings cannot be used unauthenticated"
    if caller.is_system:
        return None
    if scope.type == "user":
        return None if username_of(caller) == scope.key else "a person's settings are theirs alone"
    if _is_staff(caller):
        return None
    return access.may_use(caller, **scope.kwargs())


def _require(caller, scope: Scope) -> None:
    reason = refusal(caller, scope)
    if reason:
        raise Forbidden(reason)


# --- reading -------------------------------------------------------------------


def _layer_rows(namespace: Namespace, chain: list[Scope]) -> dict[Scope, ScopeConfig]:
    if not chain:
        return {}
    rows = ScopeConfig.objects.filter(namespace=namespace.key, scope_key__in=[s.key for s in chain])
    wanted = set(chain)
    return {s: row for row in rows if (s := Scope(row.scope_type, row.scope_key)) in wanted}


def _resolve(namespace: Namespace, chain: list[Scope], tree: dict) -> tuple[dict, dict, list[dict]]:
    rows = _layer_rows(namespace, chain)
    layers, described = [], []
    for scope in chain:
        if scope.type not in namespace.layers:
            continue
        row = rows.get(scope)
        data = row.data if row else {}
        label = scope.label(tree)
        layers.append((label, data))
        described.append(
            {
                "scope": {"type": scope.type, "key": scope.key},
                "label": label,
                "data": data,
                "version": row.version if row else 0,
                "updated_by": row.updated_by if row else "",
                "updated_at": row.updated_at.isoformat() if row else None,
            }
        )
    value, provenance = resolve_layers(namespace.defaults, layers)
    return value, provenance, described


def get(namespace_key: str, scope: Scope, caller: access.Caller) -> dict:
    """The resolved value at `scope`, its provenance, and every layer that fed it."""
    namespace = get_namespace(namespace_key)
    _require(caller, scope)
    tree = tree_for(caller)
    chain = chain_for(scope, tree)
    value, provenance, layers = _resolve(namespace, chain, tree)
    own = next((layer for layer in layers if layer["scope"] == {"type": scope.type, "key": scope.key}), None)
    return {
        "namespace": namespace.key,
        "scope": {"type": scope.type, "key": scope.key, "label": scope.label(tree)},
        "settable_here": scope.type in namespace.layers,
        "data": (own or {}).get("data", {}),
        "version": (own or {}).get("version", 0),
        "value": value,
        "provenance": provenance,
        "layers": layers,
    }


def scope_in_view(context: dict) -> Scope | None:
    """The most specific scope a request's labs context names."""
    if context.get("opportunity_id"):
        return Scope.of("opportunity", context["opportunity_id"])
    if context.get("program_id"):
        return Scope.of("program", context["program_id"])
    org = context.get("organization_slug") or context.get("organization_id")
    if org:
        return Scope.of("organization", org)
    return None


def resolved_for_request(request, namespace_key: str, scope: Scope | None = None) -> dict:
    """The resolved value for the scope in view, cached on the request.

    For rendering what the scope has set up (the supply header, a page's
    `config` prop). The middleware has already validated the context, and
    config holds ids and labels only, so this does not re-check membership.
    """
    scope = scope or scope_in_view(getattr(request, "labs_context", None) or {})
    if scope is None:
        return copy.deepcopy(get_namespace(namespace_key).defaults)
    cache = request.__dict__.setdefault("_scope_config", {})
    key = (namespace_key, scope)
    if key not in cache:
        from connect_labs.labs.context import get_org_data

        tree = get_org_data(request) or {}
        value, _, _ = _resolve(get_namespace(namespace_key), chain_for(scope, tree), tree)
        cache[key] = value
    return cache[key]


# --- changing ------------------------------------------------------------------


def _write(row: ScopeConfig, new: dict, actor: str, via: str) -> ScopeConfigChange:
    before = row.data
    row.data = new
    row.version += 1
    row.updated_by = actor
    row.save()
    return ScopeConfigChange.objects.create(
        config=row, version=row.version, before=before, after=new, changed_by=actor, via=via
    )


def update(
    namespace_key: str,
    scope: Scope,
    patch: dict,
    caller: access.Caller,
    *,
    expected_version: int | None = None,
    via: str = "",
) -> dict:
    """Apply a merge patch to `scope`'s own layer. Returns `get()` afterwards."""
    namespace = get_namespace(namespace_key)
    if scope.type not in namespace.layers:
        allowed = ", ".join(sorted(namespace.layers))
        raise Invalid(f"{namespace.label} cannot be set for a {scope.type}; it can be set for: {allowed}")
    if not isinstance(patch, dict):
        raise Invalid("a change is a JSON object (a merge patch)")
    _require(caller, scope)
    actor = username_of(caller)
    with transaction.atomic():
        row, _ = ScopeConfig.objects.select_for_update().get_or_create(
            scope_type=scope.type, scope_key=scope.key, namespace=namespace.key
        )
        if expected_version is not None and int(expected_version) != row.version:
            raise VersionConflict(
                f"{namespace.label} for {scope} is at version {row.version}, not {expected_version}: "
                "someone else changed it -- read it again and reapply your change"
            )
        new = merge_patch(row.data, patch)
        try:
            validate_layer(namespace, new)
        except ValueError as exc:
            raise Invalid(str(exc)) from None
        if new != row.data:
            _write(row, new, actor, via)
    return get(namespace_key, scope, caller)


def history(namespace_key: str, scope: Scope, caller: access.Caller, limit: int = 50) -> list[dict]:
    namespace = get_namespace(namespace_key)
    _require(caller, scope)
    changes = ScopeConfigChange.objects.filter(
        config__namespace=namespace.key, config__scope_type=scope.type, config__scope_key=scope.key
    )[:limit]
    return [
        {
            "id": c.id,
            "version": c.version,
            "changed_by": c.changed_by,
            "changed_at": c.changed_at.isoformat(),
            "via": c.via,
            "before": c.before,
            "after": c.after,
        }
        for c in changes
    ]


def undo(
    namespace_key: str, scope: Scope, caller: access.Caller, *, change_id: int | None = None, via: str = ""
) -> dict:
    """Put `scope`'s layer back as it was before `change_id` (default: the latest change).

    Recorded as a new change, so an undo can itself be undone.
    """
    namespace = get_namespace(namespace_key)
    _require(caller, scope)
    actor = username_of(caller)
    with transaction.atomic():
        row = (
            ScopeConfig.objects.select_for_update()
            .filter(scope_type=scope.type, scope_key=scope.key, namespace=namespace.key)
            .first()
        )
        changes = row.changes.all() if row else ScopeConfigChange.objects.none()
        change = changes.filter(pk=change_id).first() if change_id else changes.first()
        if change is None:
            raise Invalid("there is no change to undo" if not change_id else f"no change {change_id} here")
        _write(row, change.before, actor, via or f"undo:{change.id}")
    return get(namespace_key, scope, caller)
