"""Pure helpers for the program-level workflow view (Phase 2).

The mental model: a workflow that *spans multiple opportunities* is a
program-level workflow and belongs in the PROGRAM view, not in any single
opportunity's view. A workflow that spans a single opportunity belongs in that
opportunity's view.

The discriminator is ``len(definition.opportunity_ids) > 1`` — NOT the
template's ``multi_opp`` flag. Per-opp instances created from a multi_opp
template have ``opportunity_ids=[one_opp]`` and are therefore single-opp: they
stay in the opp view. Only the cross-opp instance (``opportunity_ids`` listing
every member opp) moves out into the program view.

These functions are deliberately dependency-free so they can be unit tested
without a database or the LabsRecord API (the workflow view tests error on main
due to a test-DB migration collision — see the module tests).
"""


def is_program_spanning(definition) -> bool:
    """A workflow spanning >1 opportunity is program-level.

    Reads ``definition.opportunity_ids`` (a list; empty means legacy single-opp
    behavior). Anything with two or more distinct entries spans the program.
    """
    return len(getattr(definition, "opportunity_ids", None) or []) > 1


def partition_by_span(definitions):
    """Split definitions into (single_opp_defs, program_spanning_defs)."""
    single, spanning = [], []
    for d in definitions:
        (spanning if is_program_spanning(d) else single).append(d)
    return single, spanning


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


def collect_program_workflows(opp_ids, *, dao_factory):
    """Collect the program-spanning workflow definitions across a program's opps.

    The labs LabsRecord API scopes reads per-opportunity, so a program's
    cross-opp workflows must be gathered by looping every member opp and listing
    each one's definitions. A spanning definition is owned by exactly one primary
    opp (its ``opportunity_id``), so it appears once across the loop — but we
    dedupe by ``id`` defensively in case an opp surfaces a shared/duplicate.

    Args:
        opp_ids: iterable of opportunity ids belonging to the program.
        dao_factory: callable ``(opp_id) -> dao`` returning an object with
            ``list_definitions()`` and ``close()``. The caller wires this to a
            per-opp ``WorkflowDataAccess``.

    Returns:
        List of program-spanning ``WorkflowDefinitionRecord`` objects, in the
        order first seen.
    """
    seen, out = set(), []
    for opp_id in opp_ids:
        dao = dao_factory(opp_id)
        try:
            for d in dao.list_definitions():
                if is_program_spanning(d) and d.id not in seen:
                    seen.add(d.id)
                    out.append(d)
        finally:
            dao.close()
    return out
