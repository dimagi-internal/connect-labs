"""May this caller use this microplans program?

A labs-only program (an id in the reserved ``>= LABS_ONLY_OPP_ID_FLOOR`` range)
keeps its plans and groups in the labs database, so no production Connect
membership check stands behind them. The labs-only access model
(``SyntheticOpportunity.is_accessible_to``) is that check, and this module is how
microplans asks it.

It asks through ``labs_only_scope_denied_reason`` -- the helper the two existing
labs chokepoints use (``labs.context.validate_context_access`` for the web app,
``mcp.server._run_registry_tool_inner`` for MCP) -- rather than ``may_use``:

  * it answers for labs-only scopes only and returns None for a real program
    without touching the database, so a real program's path is exactly what it
    was (the production LabsRecord API still enforces membership per request);
  * ``may_use`` resolves EVERYTHING a caller holds, which for a real program
    means a Connect fetch on every microplans request -- a new dependency, and a
    new way to fail, for programs that never needed it.

Two places consult it, so a new view or tool cannot forget it:

  * ``views._ProgramScopeMixin`` refuses the request before the view runs;
  * ``ProgramPlanDataAccess`` refuses to be built for a caller who may not use
    the program (web, MCP, solicitations, mopup all pass one).

A refusal is a 404, never a 403: as everywhere else in labs ("not there, or you
cannot open it"), it must not confirm that the program exists.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


def scope_denied_reason(user, *, program_id=None, opportunity_id=None) -> str | None:
    """Why ``user`` may not use this labs-only program/opportunity, or None.

    None also for a real (non-labs-only) program or opportunity: those are
    enforced downstream by Connect and are deliberately not looked at here.
    """
    from connect_labs.labs.synthetic.access import labs_only_scope_denied_reason

    return labs_only_scope_denied_reason(user, program_id=program_id, opportunity_id=opportunity_id)


def workspace_simple_microplan_url(program_id) -> str | None:
    """Where a workspace program's microplans are made, or None for any other program.

    A workspace program (``labs.workspaces``) makes its microplans in Simple
    Microplan only, for everyone in it: the CHC portfolio and creator, WA Revisit
    and the Microplans hub send people here instead. Plans and runs already
    stored for it are left as they are. No query for a real program id."""
    from connect_labs.labs.workspaces import workspace_for_program

    if workspace_for_program(program_id) is None:
        return None
    from django.urls import reverse

    return reverse("microplans:program_simple_list", args=[int(program_id)])


def record_denial(*, reason: str, program_id=None, opportunity_id=None, status_code: int = 404, user=None) -> None:
    """Put a refused labs-only scope on the audit trail. Never raises.

    The audit middleware records 403s on its own; a 404 it cannot tell from a
    plain missing page, so the refusal is recorded here, where it is known.
    """
    from connect_labs.audit_trail.models import Action, Outcome
    from connect_labs.audit_trail.service import record

    logger.warning("Refusing labs-only microplans scope: %s", reason)
    record(
        Action.ACCESS_DENIED,
        resource_type="microplans_program",
        program_id=_int_or_none(program_id),
        opportunity_id=_int_or_none(opportunity_id),
        labs_only=True,
        outcome=Outcome.FAILURE,
        status_code=status_code,
        metadata={"reason": reason},
        user=user,
    )


def _int_or_none(value) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None
