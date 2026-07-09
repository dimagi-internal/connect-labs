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

from connect_labs.labs.synthetic.local_records_backend import (
    is_labs_only_opportunity_id,
    is_labs_only_program_id,
)
from connect_labs.labs.synthetic.models import SyntheticOpportunity


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
