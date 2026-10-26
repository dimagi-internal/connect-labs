"""Read, change, and undo a scope's config.

Who may do what (spec section 1):

    layer          read                     change
    organisation   its members              its members, and Dimagi staff
    programme      its members              its members, and Dimagi staff
    opportunity    its members              its members, and Dimagi staff
    user           that person              that person

"Dimagi staff" is `_is_dimagi_staff_for`: Django `is_staff` (granted by hand) on
any scope, and a Dimagi sign-in (`utils.dimagi_user.DIMAGI_DOMAINS`) on a LABS-ONLY
scope. Ruling 2026-10-12: it was `is_staff` alone, a flag no OAuth-created labs
user carries, so the people the refusal named as allowed were refused on labs-only
organisations nobody administers. Ruling 2026-10-13 narrows the fix: a Dimagi
email does not open a real (partner) scope's layer -- there membership decides,
as Connect says -- because that layer changes what the partner's people see.

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
    """Labs staff: Django `is_staff`, a flag granted by hand. Passes for every scope."""
    return bool(getattr(_user(caller), "is_staff", False))


def _is_labs_only(scope: Scope) -> bool:
    """Whether `scope` is labs-only (synthetic): nothing in it reaches a real Connect organisation."""
    from connect_labs.labs.synthetic.local_records_backend import (
        is_labs_only_opportunity_id,
        is_labs_only_program_id,
    )

    try:
        if scope.type == "organization":
            return is_labs_only_org(scope.key)
        if scope.type == "program":
            return is_labs_only_program_id(int(scope.key))
        if scope.type == "opportunity":
            return is_labs_only_opportunity_id(int(scope.key))
    except (TypeError, ValueError):
        return False
    return False


def _is_dimagi_staff_for(caller: access.Caller, scope: Scope) -> bool:
    """Dimagi staff, for `scope`: hand-granted `is_staff` anywhere, or a Dimagi sign-in on a labs-only scope.

    Ruling 2026-10-13: a Dimagi email alone (utils.dimagi_user.is_dimagi_user) does
    NOT open a real organisation's, program's or opportunity's layer -- that is
    a partner's, and changing it changes what their people see. Real scopes stay
    membership (Connect decides) plus hand-granted `is_staff`. A labs-only scope is
    synthetic and internal, and often has no administrator (a seeder made it), so
    there a Dimagi sign-in is staff.
    """
    if _is_staff(caller):
        return True
    from connect_labs.utils.dimagi_user import is_dimagi_user

    return is_dimagi_user(_user(caller)) and _is_labs_only(scope)


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
    from connect_labs.labs.synthetic.org_tree import drop_labs_only_keys

    data = drop_labs_only_keys(data)
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
    if _is_dimagi_staff_for(caller, scope):
        return None
    return access.may_use(caller, **scope.kwargs())


def _require(caller, scope: Scope) -> None:
    reason = refusal(caller, scope)
    if reason:
        raise Forbidden(reason)


LABS_ONLY_ORG_WRITE = (
    "a labs-only (synthetic) organisation's settings are changed by its administrators and by Dimagi staff "
    "(anyone signed in with a @dimagi.com, @dimagi-ai.com or @dimagi-associate.com email), "
    "and seeing its opportunities does not "
    "make you one -- set this on your program instead, or ask an administrator of the organisation"
)


def is_labs_only_org(slug: str, tree: dict | None = None) -> bool:
    """Whether `slug` is a labs-only (synthetic) organisation's key.

    The registry decides (`LabsOrg.labs_only`), not the org tree: the tree's
    organisations come from Connect, where anyone can call an organisation
    "Labs Synthetic X" and so hold `labs-synthetic-x` with no labs-only flag. A
    key in the reserved namespace with no registry row (an unfiled opp's own key,
    an old bucket) counts too, so it falls to staff rather than to whoever holds it.
    `tree` is accepted and ignored.
    """
    from connect_labs.labs.models import LabsOrg
    from connect_labs.labs.synthetic.org_tree import is_reserved_key

    return is_reserved_key(slug) or LabsOrg.objects.filter(slug=slug, labs_only=True).exists()


def administers_labs_only_org(caller: access.Caller, key: str) -> bool:
    """Whether `caller` is an administrator of the labs-only organisation keyed `key`.

    The organisation is its `LabsOrg` registry row (labs.synthetic.org_tree), and who
    administers it is `marketplace.OrgMembership` -- granted to whoever made it, or
    by invitation. Only an unlinked labs-only row answers: a real organisation's
    administrators get nothing here.

    Only a membership granted FOR this organisation counts. `org_merge` moves
    every membership of the row it folds away onto the row it keeps, and a
    membership that arrived that way is no one's grant for this organisation: it
    is how an admin of their own labs-only organisation would have become an
    admin of someone else's.
    """
    from connect_labs.labs.models import LabsOrg
    from connect_labs.labs.synthetic.org_tree import is_labs_only_org_row
    from connect_labs.marketplace.models import OrgMembership

    user = _user(caller)
    if user is None or not getattr(user, "username", ""):
        return False
    org = LabsOrg.objects.filter(slug=key).first()
    if not is_labs_only_org_row(org):
        return False
    return OrgMembership.objects.filter(
        org=org, granted_for_org_id=org.pk, user__username=user.username, role="admin"
    ).exists()


def write_refusal(caller: access.Caller | None, scope: Scope, tree: dict) -> str | None:
    """Why `caller` may not CHANGE `scope`'s layer, or None.

    Reading and changing are the same membership rule, with one exception. For a
    labs-only organisation, "member" is only "can see one of its opportunities" --
    and labs-only opportunities are seen across whole email domains, so that
    spans other people's programmes. Its layer would restyle all of them, so it
    is written by the people who administer it (`administers_labs_only_org`), and
    Dimagi staff. That is sound now that the organisation is a registry row with
    its own administrators; while it was a name every same-named synthetic opp
    fell into (#2424), nobody could be its administrator and only staff wrote it.
    """
    reason = refusal(caller, scope)
    if reason:
        return reason
    if scope.type == "organization" and not caller.is_system and not _is_dimagi_staff_for(caller, scope):
        if is_labs_only_org(scope.key) and not administers_labs_only_org(caller, scope.key):
            return LABS_ONLY_ORG_WRITE
    return None


def _check_targets(namespace: Namespace, before: dict, after: dict, caller) -> None:
    """What the change newly points at is the right kind of thing (namespace.check_targets)."""
    if not namespace.check_targets or caller.is_system:
        return
    try:
        namespace.check_targets(before or {}, after or {}, caller)
    except ValueError as exc:
        raise Invalid(str(exc)) from None


def _require_owners(namespace: Namespace, before: dict, after: dict, caller, tree: dict) -> None:
    """Every scope the change newly points records at must be one `caller` may use."""
    if not namespace.owners or caller.is_system:
        return
    existing = set(namespace.owners(before or {}))
    for owner in namespace.owners(after or {}):
        if owner in existing:
            continue
        reason = refusal(caller, owner)
        if reason:
            raise Forbidden(f"{owner.label(tree)}: {reason}")


# --- reading -------------------------------------------------------------------


def _layer_rows(namespace: Namespace, chain: list[Scope]) -> dict[Scope, ScopeConfig]:
    if not chain:
        return {}
    rows = ScopeConfig.objects.filter(namespace=namespace.key, scope_key__in=[s.key for s in chain])
    wanted = set(chain)
    return {s: row for row in rows if (s := Scope(row.scope_type, row.scope_key)) in wanted}


def _resolve(
    namespace: Namespace, chain: list[Scope], tree: dict, replace: tuple[Scope, dict] | None = None
) -> tuple[dict, dict, list[dict]]:
    """Resolve `chain`; `replace` puts (scope, data) in place of that scope's stored layer."""
    rows = _layer_rows(namespace, chain)
    layers, described = [], []
    for scope in chain:
        if scope.type not in namespace.layers:
            continue
        row = rows.get(scope)
        data = row.data if row else {}
        if replace and replace[0] == scope:
            data = replace[1]
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
    # A layer the caller is not in (the owning organisation, for a programme member
    # outside it) shows in effect and by name, never as its raw data or who wrote it.
    for layer in layers:
        layer_scope = Scope(layer["scope"]["type"], layer["scope"]["key"])
        layer["readable"] = layer is own or refusal(caller, layer_scope) is None
        if not layer["readable"]:
            for key in ("data", "updated_by", "updated_at"):
                layer.pop(key, None)
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
    # By slug only: the middleware turns a named slug into a Connect id in
    # `organization_id` and keeps the slug in `organization_slug`.
    org = context.get("organization_slug") or context.get("organization_id")
    if org and not str(org).isdigit():
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
    tree = tree_for(caller)
    reason = write_refusal(caller, scope, tree)
    if reason:
        raise Forbidden(reason)
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
        _validate(namespace, scope, new, tree)
        _require_owners(namespace, row.data, new, caller, tree)
        _check_targets(namespace, row.data, new, caller)
        if new != row.data:
            _write(row, new, actor, via)
    return get(namespace_key, scope, caller)


def _validate(namespace: Namespace, scope: Scope, new: dict, tree: dict) -> None:
    """`new` is a valid layer, and the value in effect with it in place is valid too."""
    try:
        validate_layer(namespace, new)
        if namespace.check_resolved:
            value, _, _ = _resolve(namespace, chain_for(scope, tree), tree, replace=(scope, new))
            namespace.check_resolved(value)
    except ValueError as exc:
        raise Invalid(str(exc)) from None


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
    tree = tree_for(caller)
    reason = write_refusal(caller, scope, tree)
    if reason:
        raise Forbidden(reason)
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
        # An undo is a write like any other: what it restores must still hold, and
        # point only at scopes the person undoing it may use.
        _validate(namespace, scope, change.before, tree)
        _require_owners(namespace, row.data, change.before, caller, tree)
        _check_targets(namespace, row.data, change.before, caller)
        _write(row, change.before, actor, via or f"undo:{change.id}")
    return get(namespace_key, scope, caller)
