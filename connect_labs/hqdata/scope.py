"""The cache scope id of a CommCare project.

The analysis caches are keyed by an integer named ``opportunity_id``. A project has no
opportunity, so it gets a key of its own: negative, which meets neither a Connect
opportunity, a labs-only one (>= 10,000), nor a programme-scoped Drive source
(``-program_id``, see ``gdrive_fetcher.program_cache_scope``). Programme ids are far below
``HQ_SCOPE_FLOOR``, so the two negative ranges cannot overlap.
"""

HQ_SCOPE_FLOOR = 10**9


def hq_scope_id(project_id: int) -> int:
    return -(HQ_SCOPE_FLOOR + int(project_id))


def is_hq_scope(scope_id) -> bool:
    """True for a project scope id. Takes whatever a caller holds: MCP and URL arguments arrive as strings."""
    try:
        return scope_id is not None and int(scope_id) <= -HQ_SCOPE_FLOOR
    except (TypeError, ValueError):
        return False


def project_id_from_scope(scope_id: int) -> int:
    if not is_hq_scope(scope_id):
        raise ValueError(f"{scope_id!r} is not a CommCare project scope")
    return -scope_id - HQ_SCOPE_FLOOR
