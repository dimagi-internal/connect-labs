"""Authorization helpers for labs-only (synthetic) opportunities.

Labs-only opp_ids (>= ``LABS_ONLY_OPP_ID_FLOOR``) route to a local ORM backend
(``local_records_backend``) with no production Connect membership check behind
them. These helpers are the security boundary for that namespace: a user may
read/write a labs-only opp's records only if the opp permits their account
(``SyntheticOpportunity.is_accessible_to``).

They are enforced at the two request chokepoints so no individual view or MCP
tool can forget the check:
  * web  -> ``connect_labs.labs.context.validate_context_access``
  * MCP  -> ``connect_labs.mcp.server._run_registry_tool``

Real (non-labs-only) opps/programs are intentionally NOT gated here — those are
enforced downstream, per request, by the production Connect LabsRecord API.
"""

from __future__ import annotations

from django.db.models import Q

from connect_labs.labs.synthetic.local_records_backend import is_labs_only_opportunity_id, is_labs_only_program_id
from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR, SyntheticOpportunity


def _safe_int(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def user_can_access_labs_only_opp(user, opportunity_id) -> bool:
    """True if ``user`` may access the labs-only opp ``opportunity_id``."""
    opp_id = _safe_int(opportunity_id)
    if opp_id is None:
        return False
    opp = SyntheticOpportunity.objects.filter(opportunity_id=opp_id, labs_only=True).first()
    if opp is None:
        # No registered labs-only opp behind this id — nothing grants access.
        return False
    return opp.is_accessible_to(user)


def user_can_access_labs_only_program(user, program_id) -> bool:
    """True if ``user`` may access any labs-only opp filed under ``program_id``.

    Program-scoped reads span the program's opps; access is granted when at least
    one labs-only opp in the program is accessible to the user (mirrors
    ``is_labs_only_program_id``'s existence semantics).
    """
    pid = _safe_int(program_id)
    if pid is None:
        return False
    opps = SyntheticOpportunity.objects.filter(labs_only=True).filter(
        Q(program_id=pid) | Q(program_id__isnull=True, opportunity_id=pid)
    )
    return any(o.is_accessible_to(user) for o in opps)


def labs_only_scope_denied_reason(user, *, opportunity_id=None, program_id=None) -> str | None:
    """Return an error string if ``user`` may not use a labs-only scope, else None.

    A ``None`` return means either access is permitted OR the scope is a real
    Connect opp/program (not labs-only), which this gate deliberately ignores.
    """
    opp_id = _safe_int(opportunity_id)
    if opp_id is not None and is_labs_only_opportunity_id(opp_id) and not user_can_access_labs_only_opp(user, opp_id):
        return f"labs-only opportunity {opp_id} is not accessible to your account"
    prog_id = _safe_int(program_id)
    if (
        prog_id is not None
        and is_labs_only_program_id(prog_id)
        and not user_can_access_labs_only_program(user, prog_id)
    ):
        return f"labs-only program {prog_id} is not accessible to your account"
    return None


def labs_only_program_denied_reason(user, program_id) -> str | None:
    """Why ``user`` may not file a labs-only opp under ``program_id``, or None.

    A program is a READ scope: ``user_can_access_labs_only_program`` grants a program
    to anyone who can reach one opp in it. So filing an opp you own under someone
    else's program would hand you their program-scoped records. A new program id is
    fine; an existing one must already be yours to see.
    """
    pid = _safe_int(program_id)
    if pid is None or pid < LABS_ONLY_OPP_ID_FLOOR:
        return f"program_id {program_id} is not a labs-only program id (must be >= {LABS_ONLY_OPP_ID_FLOOR})"
    if is_labs_only_program_id(pid) and not user_can_access_labs_only_program(user, pid):
        return f"labs-only program {pid} is not accessible to your account"
    return None


def labs_only_target_denied_reason(user, opportunity_id) -> str | None:
    """Why ``user`` may not write (generate, register, seed) onto ``opportunity_id``, or None.

    The target must be in the labs-only namespace, never a real Connect opp id. It may
    be an id nobody holds yet, or an existing labs-only opp the caller can already
    access. An unallocated id that is somebody's PROGRAM id is refused like the
    program itself, since registering it would join that program's read scope.
    """
    opp_id = _safe_int(opportunity_id)
    if opp_id is None or opp_id < LABS_ONLY_OPP_ID_FLOOR:
        return (
            f"opportunity_id {opportunity_id} is not a labs-only id (must be >= {LABS_ONLY_OPP_ID_FLOOR}); "
            "generated data is never written onto a real Connect opportunity"
        )
    row = SyntheticOpportunity.objects.filter(opportunity_id=opp_id).first()
    if row is not None:
        if not row.labs_only:
            return f"opportunity_id {opp_id} is registered as a real-backed opportunity, not a labs-only one"
        if not row.is_accessible_to(user):
            return f"labs-only opportunity {opp_id} is not accessible to your account"
        return None
    if is_labs_only_program_id(opp_id) and not user_can_access_labs_only_program(user, opp_id):
        return f"labs-only id {opp_id} is a program that is not accessible to your account"
    return None
