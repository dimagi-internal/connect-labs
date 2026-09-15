"""May this caller use this scope? -- for data labs owns locally.

`labs/synthetic/access.py` is the sibling of this module and the precedent for
its shape: one policy module, consulted from a chokepoint, so no individual
view or tool can forget the check. It gates the LABS-ONLY namespace, and says
so explicitly: "Real (non-labs-only) opps/programs are intentionally NOT gated
here -- those are enforced downstream, per request, by the production Connect
LabsRecord API."

That leaves a third category with no owner: REAL-scoped data that lives in labs
Postgres. Organisation 179 is a real Connect organisation, so the labs-only gate
correctly stands down -- and `benchmarks` and `supply_chain` never call the
LabsRecord API for their own data, so the delegation never happens either.
Nobody authorises it.

This module is consulted from those apps' DATA ACCESS LAYER rather than from a
request chokepoint, because a request boundary cannot see whether a call will
end at Connect or at local Postgres. The data access layer can.

Two rules that are the whole point:

  * A caller that cannot be resolved is DENIED. The defect this closes is
    authorisation-by-omission, so a permissive default would reintroduce it.
  * `SYSTEM` is the only escape, and it is explicit. `grep -rn "SYSTEM"` is the
    complete list of unauthenticated entry points; a test pins that list.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data


@dataclass(frozen=True)
class Caller:
    """Who is asking, and how we can find out what they hold.

    Resolution takes the cheapest path the surface offers: a web request
    already carries the organisation list fetched at login, while the MCP
    route has a token whose fetch is TTL-cached. This mirrors how
    `SupplyDataAccess` already resolves identity for attribution -- the same
    resolution, finally consulted for authorisation.
    """

    user: object | None = None
    request: object | None = None
    access_token: str | None = None
    is_system: bool = False


# Seeders, management commands and jobs run with no user. They declare it.
SYSTEM = Caller(is_system=True)


def _org_data(caller: Caller) -> dict | None:
    """The caller's organisations/programs/opportunities, or None if unknowable."""
    if caller.request is not None:
        from connect_labs.labs.context import get_org_data

        return get_org_data(caller.request)
    if caller.access_token:
        # Returns None when Connect cannot be REACHED -- which must deny, not
        # permit, so it is passed through as unknowable rather than as empty.
        return fetch_user_organization_data(caller.access_token, owner=getattr(caller.user, "username", None))
    return None


def org_slugs(caller: Caller) -> set[str]:
    data = _org_data(caller) or {}
    return {str(o.get("slug") or o.get("id")) for o in data.get("organizations", []) if o.get("slug") or o.get("id")}


def opportunity_ids(caller: Caller) -> set[int]:
    data = _org_data(caller) or {}
    out = set()
    for o in data.get("opportunities", []):
        try:
            out.add(int(o["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def program_ids(caller: Caller) -> set[int]:
    data = _org_data(caller) or {}
    out = set()
    for p in data.get("programs", []):
        try:
            out.add(int(p["id"]))
        except (KeyError, TypeError, ValueError):
            continue
    return out


def may_use(
    caller: Caller | None,
    *,
    organization_id=None,
    program_id=None,
    opportunity_id=None,
) -> str | None:
    """An error string if `caller` may not use every scope supplied, else None.

    EVERY supplied scope must pass. A caller holding the organisation but not
    the opportunity is refused -- partial authorisation is how a scope sneaks
    through beside one that was checked.
    """
    if caller is None:
        return "no caller: a locally-owned scope cannot be used unauthenticated"
    if caller.is_system:
        return None
    if organization_id is None and program_id is None and opportunity_id is None:
        return None

    data = _org_data(caller)
    if data is None:
        return "cannot establish what this caller may access"

    if organization_id is not None and str(organization_id) not in org_slugs(caller):
        return f"organization {organization_id} is not accessible to your account"
    if opportunity_id is not None:
        try:
            if int(opportunity_id) not in opportunity_ids(caller):
                return f"opportunity {opportunity_id} is not accessible to your account"
        except (TypeError, ValueError):
            return f"opportunity {opportunity_id!r} is not a valid id"
    if program_id is not None:
        try:
            if int(program_id) not in program_ids(caller):
                return f"program {program_id} is not accessible to your account"
        except (TypeError, ValueError):
            return f"program {program_id!r} is not a valid id"
    return None
