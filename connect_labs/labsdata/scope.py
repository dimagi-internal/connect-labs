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
    program_id: int | None = None
    program_name: str = ""


#: The most opportunities one query reads. A program is expanded into its
#: opportunities before this applies, and over it the query is refused, never cut.
MAX_OPPORTUNITIES = 100


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


def _int_or_none(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def directory(caller: scopes.Caller) -> dict[int, Opportunity]:
    """Every opportunity the caller can see, by id, with its name, LLO and programme."""
    tree = _org_tree(caller)
    org_names = {o.get("slug"): o.get("name") or o.get("slug") for o in tree.get("organizations") or []}
    program_names = {_int_or_none(p.get("id")): p.get("name") or "" for p in tree.get("programs") or []}
    out: dict[int, Opportunity] = {}
    for opp in tree.get("opportunities") or []:
        try:
            oid = int(opp.get("id"))
        except (TypeError, ValueError):
            continue
        org = opp.get("organization")
        program = _int_or_none(opp.get("program"))
        out[oid] = Opportunity(
            id=oid,
            name=opp.get("name") or f"Opportunity {oid}",
            llo=org_names.get(org) or org or "",
            program_id=program,
            program_name=opp.get("program_name") or program_names.get(program) or "",
        )
    return out


def left_out_of(caller: scopes.Caller, opportunity_ids) -> list[Opportunity]:
    """Opportunities the caller holds in the same programme(s) as these, that these leave out.

    A workflow's opportunities are chosen by hand, so a programme's new opportunity is
    silently missing from its programme-level numbers until someone adds it (Scout #922).
    Only what the caller holds: the list names nothing they could not already open.
    """
    ids = {int(i) for i in opportunity_ids or []}
    seen = directory(caller)
    programs = {seen[i].program_id for i in ids if i in seen and seen[i].program_id is not None}
    if not programs:
        return []
    try:
        held = scopes.opportunity_ids(caller)
    except scopes.ScopesUnavailable:
        return []
    return sorted(
        (o for o in seen.values() if o.program_id in programs and o.id not in ids and o.id in held),
        key=lambda o: o.id,
    )


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


def expand(caller: scopes.Caller, opportunity_ids=(), program_ids=()) -> tuple[list[Opportunity], list[dict]]:
    """Programs and opportunities, as the opportunities a query reads -- each one held.

    A program is the opportunities of it the caller holds, resolved now, so one
    added to it later is included without anyone choosing it. Asking for a program
    the caller does not hold is refused, like an opportunity. Returns the
    opportunities (via ``resolve``, so every id is checked again) and the programs,
    with what each expanded to.
    """
    try:
        wanted_programs = sorted({int(p) for p in (program_ids or [])})
    except (TypeError, ValueError) as e:
        raise ScopeError("bad_program_ids", "program_ids must be integers") from e
    try:
        ids = {int(i) for i in (opportunity_ids or [])}
    except (TypeError, ValueError) as e:
        raise ScopeError("bad_opportunity_ids", "opportunity_ids must be integers") from e
    programs = []
    if wanted_programs:
        try:
            held_programs = scopes.program_ids(caller)
            held_opps = scopes.opportunity_ids(caller)
        except scopes.ScopesUnavailable as e:
            raise ScopeError("scopes_unavailable", scopes.UNKNOWABLE) from e
        missing = [p for p in wanted_programs if p not in held_programs]
        if missing:
            raise ScopeError(
                "not_held",
                f"you do not have access to program{'' if len(missing) == 1 else 's'} {', '.join(map(str, missing))}",
            )
        seen = directory(caller)
        for pid in wanted_programs:
            members = sorted(o.id for o in seen.values() if o.program_id == pid and o.id in held_opps)
            name = next((o.program_name for o in seen.values() if o.program_id == pid and o.program_name), "")
            if not members:
                raise ScopeError("no_opportunities", f"you hold no opportunities in program {name or pid}")
            programs.append({"id": pid, "name": name or f"Program {pid}", "opportunity_ids": members})
            ids.update(members)
    return resolve(caller, sorted(ids)), programs


def programs(caller: scopes.Caller) -> list[dict]:
    """The programs the caller holds opportunities in, for choosing one: id, name, how many."""
    by_program: dict[int, dict] = {}
    for o in directory(caller).values():
        if o.program_id is None:
            continue
        entry = by_program.setdefault(o.program_id, {"id": o.program_id, "name": o.program_name, "opportunities": 0})
        entry["opportunities"] += 1
        entry["name"] = entry["name"] or o.program_name
    return sorted(by_program.values(), key=lambda p: (p["name"] or "").lower())
