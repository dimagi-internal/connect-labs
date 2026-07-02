"""Pure helpers for the program-level workflow view.

The mental model: a workflow belongs in the PROGRAM view ONLY if it is
*program-owned* — its LabsRecord carries a program FK (and no owning
opportunity). This is true record ownership: a program-owned definition is
created via a program-scoped ``WorkflowDataAccess(program_id=P)``, which sets
the record's program FK. See ``data_access.create_definition``.

A multi-opportunity workflow that merely happens to be owned by an opportunity
is NOT program-owned and does NOT appear in the program view. The opp view
shows a given opp's workflows EXCLUDING any that are program-owned.

The ownership marker is the record's program FK, exposed as
``definition.program_id`` (mirroring ``definition.opportunity_id``). We do NOT
rely on ``config.program_id`` — ownership is a property of the record, not its
JSON config.

These functions are deliberately dependency-free so they can be unit tested
without a database or the LabsRecord API (the workflow view tests error on main
due to a test-DB migration collision — see the module tests).
"""


def program_id_of(definition):
    """The program a definition is owned by (its record program FK), or None."""
    pid = getattr(definition, "program_id", None)
    return int(pid) if pid is not None else None


def is_program_owned(definition) -> bool:
    return program_id_of(definition) is not None


def owned_by_program(definition, program_id) -> bool:
    pid = program_id_of(definition)
    return pid is not None and pid == int(program_id)


def program_opportunity_ids(org_data, program_id) -> list[int]:
    """Opportunity ids that belong to a program.

    Each opportunity carries its program at ``opp['program']`` (see
    ``labs/context.py`` ``get_org_data`` and the synthetic merge, which sets
    ``opp['program'] = program_id``). Opportunities missing an id are skipped.
    """
    out = []
    for o in (org_data or {}).get("opportunities", []) or []:
        if o.get("program") == program_id and o.get("id") is not None:
            out.append(int(o["id"]))
    return out


def opp_owned_definitions(definitions):
    """Opp view: drop anything program-owned."""
    return [d for d in definitions if not is_program_owned(d)]


def collect_program_workflows(program_id, opp_ids, *, dao_factory):
    """Program view: walk the program's opps, keep defs explicitly owned by this
    program, dedupe by id (a def is owned by one primary opp, appears once).

    The labs LabsRecord API scopes reads per-opportunity, so a program's
    workflows must be gathered by looping every member opp and listing each
    one's definitions, keeping only those whose record program FK matches.
    (A follow-up will switch the view to a direct program-scoped
    ``WorkflowDataAccess(program_id=P).list_definitions()``.)

    Args:
        program_id: the program whose owned workflows we want.
        opp_ids: iterable of opportunity ids belonging to the program.
        dao_factory: callable ``(opp_id) -> dao`` returning an object with
            ``list_definitions()`` and ``close()``. The caller wires this to a
            per-opp ``WorkflowDataAccess``.

    Returns:
        List of program-owned ``WorkflowDefinitionRecord`` objects, in the order
        first seen.
    """
    seen, out = set(), []
    for opp_id in opp_ids:
        dao = dao_factory(opp_id)
        try:
            for d in dao.list_definitions():
                if owned_by_program(d, program_id) and d.id not in seen:
                    seen.add(d.id)
                    out.append(d)
        finally:
            dao.close()
    return out
