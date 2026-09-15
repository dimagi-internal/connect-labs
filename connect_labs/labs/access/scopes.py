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

Three rules that are the whole point:

  * A caller that cannot be resolved is DENIED. The defect this closes is
    authorisation-by-omission, so a permissive default would reintroduce it.
  * "We could not find out" is NEVER "they hold nothing". The two deny alike,
    but they are reported differently, so an unreachable Connect is not
    diagnosed as a permission problem. `ScopesUnavailable` is that distinction
    made unignorable -- a caller cannot read an empty set and guess wrong,
    because there is no empty set to read.
  * `SYSTEM` is the only escape, and it is explicit. `grep -rn "SYSTEM"` is the
    complete list of unauthenticated entry points; a test pins that list.

WHAT A CALLER HOLDS IS BOTH REAL AND LABS-ONLY. Production Connect knows
nothing about labs-only (synthetic) opportunities and programmes -- they live
only in labs' own registry -- so a policy built on the production fetch alone
refuses every synthetic scope. The web surface never had that problem because
`get_org_data` merges the labs-only tree in; the token surface did, and it made
the entire supply-chain MCP catalogue useless against the demo programmes it
exists to drive. Both paths are merged here, once, so the two surfaces cannot
disagree about what the same person holds.
"""

from __future__ import annotations

from dataclasses import dataclass

from connect_labs.labs.integrations.connect.oauth import fetch_user_organization_data

# The one message `may_use` returns for "unknowable". Callers that map refusals
# onto their own error codes match on it to tell a network blip apart from a
# permission fact, so it is named rather than spelled out at each site.
UNKNOWABLE = "cannot establish what this caller may access"


class ScopesUnavailable(Exception):
    """What this caller holds could not be established -- not "they hold nothing".

    Raised by `org_slugs`, `opportunity_ids` and `program_ids` when Connect
    could not be reached. They used to return an empty set in that case, which
    reads identically to a real answer: a caller who holds opportunity 42 would
    be told "you do not hold opportunity 42" because of a network blip. Failing
    loudly is what makes the two impossible to confuse.
    """


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


@dataclass(frozen=True)
class Holdings:
    """Everything a caller holds, resolved once.

    One resolution, three projections. Resolving per-scope invited a SECOND
    fetch between the organisation gate and the opportunity gate, which is
    where a TTL expiry plus a Connect blip turned an authorised caller into a
    permission error.
    """

    org_slugs: frozenset[str]
    opportunity_ids: frozenset[int]
    program_ids: frozenset[int]


def _user_of(caller: Caller):
    """The Django user behind this caller, however it arrived."""
    if caller.user is not None:
        return caller.user
    return getattr(caller.request, "user", None)


def _visible_labs_only_opps(user) -> list:
    """The labs-only synthetic opps this user may see. Registry-backed, no prod call.

    THE one walk of `SyntheticOpportunity` for authorisation purposes. It was
    previously open-coded in `mcp/tools/workflows.py` and again in
    `benchmarks/mcp_tools.py`, each projecting a different field off the same
    query; both now come here. `labs/context._merge_labs_only_opps` performs
    the same walk to build the UI's org tree, and the id derivations below are
    imported from `org_tree` -- the module that exists so those derivations
    cannot drift between surfaces.
    """
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    try:
        candidates = SyntheticOpportunity.objects.filter(labs_only=True, enabled=True)
        return [opp for opp in candidates if opp.is_visible_to(user)]
    except Exception:  # noqa: BLE001 -- registry trouble must not break validation of real scopes
        return []


def labs_only_opportunity_ids(user) -> set[int]:
    """Labs-only opportunity ids visible to `user` (registry-backed, no prod call)."""
    return {opp.opportunity_id for opp in _visible_labs_only_opps(user)}


def labs_only_program_ids(user) -> set[int]:
    """Labs-only program ids visible to `user` (registry-backed, no prod call)."""
    from connect_labs.labs.synthetic.org_tree import synthetic_program_id

    return {synthetic_program_id(opp) for opp in _visible_labs_only_opps(user)}


def labs_only_org_slugs(user) -> set[str]:
    """Labs-only organisation slugs visible to `user` (registry-backed, no prod call)."""
    from connect_labs.labs.synthetic.org_tree import synthetic_org_slug

    return {synthetic_org_slug(opp) for opp in _visible_labs_only_opps(user)}


def _connect_org_data(caller: Caller) -> dict | None:
    """The caller's production org tree, or None if unknowable."""
    if caller.request is not None:
        from connect_labs.labs.context import get_org_data

        org_data = get_org_data(caller.request)
        if org_data:
            return org_data
        # A session carrying no organisation list at all is not evidence that
        # the user belongs to nothing -- an expired or half-built session looks
        # exactly like this. `supply_chain/identity.py` falls through to the
        # token here for exactly that reason; this module denied instead, so
        # one stale session was a hard 403 on every supply page and a working
        # request on the MCP route.
        #
        # Fall through only when the caller carries something to fall through
        # WITH: an explicit token, or the user identity that `identity.py`
        # derives one from. A caller that is a bare request and nothing else
        # keeps denying -- there is nobody to ask, and inventing one from
        # `request.user` would make this fetch on paths that never asked for
        # an identity.
        token = caller.access_token or _token_for(caller.user)
        if token:
            return _fetch(token, caller)
        return org_data
    if caller.access_token:
        return _fetch(caller.access_token, caller)
    return None


def _token_for(user):
    """A usable Connect token for `user`, or None. Never raises."""
    if user is None:
        return None
    from connect_labs.labs.connect_tokens import get_valid_access_token

    try:
        return get_valid_access_token(user)
    except Exception:  # noqa: BLE001 -- no token is "cannot ask", not an error to surface here
        return None


def _fetch(token: str, caller: Caller) -> dict | None:
    # Returns None when Connect cannot be REACHED -- which must deny, not
    # permit, so it is passed through as unknowable rather than as empty.
    return fetch_user_organization_data(token, owner=getattr(_user_of(caller), "username", None))


def holdings(caller: Caller) -> Holdings:
    """Everything `caller` holds: production's, plus labs-only.

    Raises `ScopesUnavailable` if that cannot be established.
    """
    data = _connect_org_data(caller)
    if data is None:
        raise ScopesUnavailable(UNKNOWABLE)

    user = _user_of(caller)

    slugs: set[str] = set(labs_only_org_slugs(user))
    for o in data.get("organizations", []):
        # BOTH `id` and `slug`, not whichever comes first. Labs carries both and
        # they are not the same thing across the codebase: `registry_source`
        # identifies an organisation as `{"organization_id": 179}` while
        # `benchmarks` uses slugs like `"dimagi-kmc"`. Taking only one
        # convention refuses a caller using the other -- a permission failure
        # with no permission problem behind it, which is the hardest kind to
        # diagnose.
        for key in ("slug", "id"):
            value = o.get(key)
            if value is not None and value != "":
                slugs.add(str(value))

    opportunities: set[int] = set(labs_only_opportunity_ids(user))
    for o in data.get("opportunities", []):
        try:
            opportunities.add(int(o["id"]))
        except (KeyError, TypeError, ValueError):
            continue

    programs: set[int] = set(labs_only_program_ids(user))
    for p in data.get("programs", []):
        try:
            programs.add(int(p["id"]))
        except (KeyError, TypeError, ValueError):
            continue

    return Holdings(
        org_slugs=frozenset(slugs),
        opportunity_ids=frozenset(opportunities),
        program_ids=frozenset(programs),
    )


def org_slugs(caller: Caller) -> set[str]:
    """Every string a caller could legitimately name one of their orgs by.

    Raises `ScopesUnavailable` rather than returning an empty set when what the
    caller holds could not be established.
    """
    return set(holdings(caller).org_slugs)


def opportunity_ids(caller: Caller) -> set[int]:
    """Raises `ScopesUnavailable` when unknowable -- never an empty set."""
    return set(holdings(caller).opportunity_ids)


def program_ids(caller: Caller) -> set[int]:
    """Raises `ScopesUnavailable` when unknowable -- never an empty set."""
    return set(holdings(caller).program_ids)


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

    try:
        held = holdings(caller)
    except ScopesUnavailable:
        return UNKNOWABLE

    if organization_id is not None and str(organization_id) not in held.org_slugs:
        return f"organization {organization_id} is not accessible to your account"
    if opportunity_id is not None:
        try:
            if int(opportunity_id) not in held.opportunity_ids:
                return f"opportunity {opportunity_id} is not accessible to your account"
        except (TypeError, ValueError):
            return f"opportunity {opportunity_id!r} is not a valid id"
    if program_id is not None:
        try:
            if int(program_id) not in held.program_ids:
                return f"program {program_id} is not accessible to your account"
        except (TypeError, ValueError):
            return f"program {program_id!r} is not a valid id"
    return None
