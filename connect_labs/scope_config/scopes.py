"""Scopes, and the chain of layers a scope reads through.

A scope is an organisation (by slug), a programme or an opportunity (by id), or
a user (by username). The chain is the ownership chain, organisation first:

    organisation O                    -> [O]
    programme P                       -> [P's owning org, P]
    opportunity X in programme P      -> [P's owning org, P, X]   (decision A: the
                                         programme OWNER's org, not X's delivering org)
    opportunity X with no programme   -> [X's own org, X]
    user U                            -> [U]

`tree` is the shape `labs.context.get_org_data` returns: `organizations`
[{id, slug, name}], `programs` [{id, name, organization: <owner slug>}],
`opportunities` [{id, name, organization: <delivering slug>, program: <id>}].
Labs-only (synthetic) entries carry the same shape.
"""

from __future__ import annotations

from dataclasses import dataclass

TYPES = ("organization", "program", "opportunity", "user")

LABELS = {"organization": "organisation", "program": "programme", "opportunity": "opportunity", "user": "user"}


@dataclass(frozen=True)
class Scope:
    type: str
    key: str

    @classmethod
    def of(cls, type_: str, key) -> Scope:
        if type_ not in TYPES:
            raise ValueError(f"scope type must be one of {', '.join(TYPES)}, not {type_!r}")
        key = str(key).strip()
        if not key:
            raise ValueError(f"a {type_} scope needs a key")
        if type_ in ("program", "opportunity") and not key.isdigit():
            raise ValueError(f"a {type_} is named by its id, not {key!r}")
        return cls(type_, key)

    @property
    def id(self) -> int | None:
        return int(self.key) if self.type in ("program", "opportunity") else None

    def kwargs(self) -> dict:
        """The `scopes.may_use` / data-access keyword for this scope."""
        if self.type == "organization":
            return {"organization_id": self.key}
        if self.type == "program":
            return {"program_id": int(self.key)}
        if self.type == "opportunity":
            return {"opportunity_id": int(self.key)}
        return {}

    def label(self, tree: dict | None = None) -> str:
        """ "programme 10112" or its name when the tree knows it."""
        name = name_of(self, tree or {})
        return f"{LABELS[self.type]} {name or self.key}"

    def __str__(self):
        return f"{self.type}:{self.key}"


def _find(entries, key):
    for entry in entries or []:
        if str(entry.get("id")) == str(key) or (entry.get("slug") and entry.get("slug") == key):
            return entry
    return None


def program_entry(tree: dict, program_id) -> dict | None:
    return _find(tree.get("programs"), program_id)


def opportunity_entry(tree: dict, opportunity_id) -> dict | None:
    return _find(tree.get("opportunities"), opportunity_id)


def organization_entry(tree: dict, slug) -> dict | None:
    return _find(tree.get("organizations"), slug)


def name_of(scope: Scope, tree: dict) -> str | None:
    entry = {
        "organization": organization_entry,
        "program": program_entry,
        "opportunity": opportunity_entry,
    }.get(
        scope.type, lambda *_: None
    )(tree, scope.key)
    return (entry or {}).get("name")


def _program_of_opportunity(tree: dict, opportunity_id) -> int | None:
    entry = opportunity_entry(tree, opportunity_id)
    program = (entry or {}).get("program")
    if program not in (None, ""):
        return int(program)
    # Pulse keeps every opportunity's programme, including ones the viewer's
    # cached tree was fetched before they joined.
    try:
        from connect_labs.pulse.models import PulseOpportunity

        row = PulseOpportunity.objects.filter(opportunity_id=int(opportunity_id)).values("program_id").first()
    except Exception:  # noqa: BLE001 -- a missing mirror is "no programme known", not an error
        row = None
    return int(row["program_id"]) if row and row.get("program_id") else None


def owner_org(tree: dict, scope: Scope) -> str | None:
    """The organisation whose layer this scope reads, by slug (None for a user)."""
    if scope.type == "organization":
        return scope.key
    if scope.type == "program":
        return (program_entry(tree, scope.key) or {}).get("organization") or None
    if scope.type == "opportunity":
        program = _program_of_opportunity(tree, scope.key)
        if program is not None:
            owner = (program_entry(tree, program) or {}).get("organization")
            if owner:
                return owner
            # A programme the tree does not hold: no owner is known, and the
            # delivering org is deliberately NOT a stand-in (decision A).
            return None
        return (opportunity_entry(tree, scope.key) or {}).get("organization") or None
    return None


def chain_for(scope: Scope, tree: dict, username: str | None = None) -> list[Scope]:
    """The layers `scope` reads through, lowest first, ending with `scope` itself.

    `username`, when given, adds that person's layer on top.
    """
    chain: list[Scope] = []
    if scope.type == "user":
        return [scope]
    org = owner_org(tree, scope)
    if org:
        chain.append(Scope.of("organization", org))
    if scope.type == "opportunity":
        program = _program_of_opportunity(tree, scope.key)
        if program is not None:
            chain.append(Scope.of("program", program))
    if scope.type != "organization":
        chain.append(scope)
    elif not chain:
        chain.append(scope)
    if username:
        chain.append(Scope.of("user", username))
    return chain
