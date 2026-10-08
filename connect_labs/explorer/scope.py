"""Which opportunities an explorer call may read, and what to call them.

Authorisation is ``labs/access/scopes.py`` and nothing else: the requested ids
are intersected with what the caller holds, and asking for one they do not
hold is refused rather than silently dropped -- a query that quietly covers two
of three requested opportunities answers a different question than the one
asked. Names and the LLO come from the same org tree the rest of labs reads.

The visit cache itself has no ACL (rows fetched for one user sit in a shared
table), which is exactly why this check is the engine's precondition, not an
optional filter.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs.access import scopes


class ScopeError(Exception):
    """The request cannot be scoped; ``code`` says why."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Opportunity:
    id: int
    name: str
    llo: str  # the organization that runs the opportunity


MAX_OPPORTUNITIES = 25


def _org_tree(caller: scopes.Caller) -> dict:
    if caller.request is not None:
        from connect_labs.labs.context import get_org_data

        tree = get_org_data(caller.request)
        if tree:
            return tree
    token = caller.access_token
    if token is None and caller.user is not None:
        from connect_labs.labs.connect_tokens import get_valid_access_token

        try:
            token = get_valid_access_token(caller.user)
        except Exception:  # noqa: BLE001 -- names are cosmetic; authorisation already ran
            token = None
    if not token:
        return {}
    from connect_labs.labs.context import _merge_labs_only_opps
    from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

    tree = fetch_user_organization_data(token, owner=getattr(caller.user, "username", None)) or {}
    if caller.user is not None and getattr(caller.user, "view_synthetic_opps", False):
        tree = _merge_labs_only_opps(tree, caller.user)
    return tree


def directory(caller: scopes.Caller) -> dict[int, Opportunity]:
    """Every opportunity the caller can see, by id, with its name and LLO."""
    tree = _org_tree(caller)
    org_names = {o.get("slug"): o.get("name") or o.get("slug") for o in tree.get("organizations") or []}
    out: dict[int, Opportunity] = {}
    for opp in tree.get("opportunities") or []:
        try:
            oid = int(opp.get("id"))
        except (TypeError, ValueError):
            continue
        org = opp.get("organization")
        out[oid] = Opportunity(
            id=oid, name=opp.get("name") or f"Opportunity {oid}", llo=org_names.get(org) or org or ""
        )
    return out


def resolve(caller: scopes.Caller, requested) -> list[Opportunity]:
    """The requested opportunities, each one held by the caller, or ScopeError."""
    try:
        ids = sorted({int(i) for i in (requested or [])})
    except (TypeError, ValueError) as e:
        raise ScopeError("bad_opportunity_ids", "opportunity_ids must be integers") from e
    if not ids:
        raise ScopeError("no_opportunities", "pick at least one opportunity")
    if len(ids) > MAX_OPPORTUNITIES:
        raise ScopeError("too_many_opportunities", f"at most {MAX_OPPORTUNITIES} opportunities per query")
    try:
        held = scopes.opportunity_ids(caller)
    except scopes.ScopesUnavailable as e:
        raise ScopeError("scopes_unavailable", scopes.UNKNOWABLE) from e
    missing = [i for i in ids if i not in held]
    if missing:
        raise ScopeError(
            "not_held",
            f"you do not have access to opportunit{'y' if len(missing) == 1 else 'ies'} "
            f"{', '.join(map(str, missing))}",
        )
    names = directory(caller)
    return [names.get(i) or Opportunity(id=i, name=f"Opportunity {i}", llo="") for i in ids]
