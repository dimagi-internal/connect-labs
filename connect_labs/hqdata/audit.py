"""Putting a person's use of a CommCare project on the labs audit trail.

One place, so every way in (pages, MCP tools, pipelines) says the same thing. Events carry the
project's id and opaque counts only: never a domain's data, a form answer or a person's name
(``docs/AUDIT_LOGGING.md``). ``record`` never raises, so a broken audit pipeline cannot take a
project read down with it.
"""

from __future__ import annotations

from connect_labs.audit_trail.models import Action, Outcome
from connect_labs.audit_trail.service import record

RESOURCE = "commcare_project"


def project_event(
    action: str,
    project_id,
    *,
    user=None,
    kind: str = "",
    record_count: int | None = None,
    outcome: str = Outcome.SUCCESS,
    status_code: int | None = None,
    metadata: dict | None = None,
) -> None:
    """One event about a project. ``kind`` says which door (``pipeline``, ``forms``, ``sync``...)."""
    record(
        action,
        resource_type=RESOURCE,
        resource_id=project_id,
        record_count=record_count,
        outcome=outcome,
        status_code=status_code,
        metadata={**(metadata or {}), **({"kind": kind} if kind else {})},
        user=user,
    )


def project_denied(project_id, user=None, *, kind: str = "") -> None:
    """A refusal. A 404 the middleware cannot tell from a missing page, so it is recorded here."""
    project_event(Action.ACCESS_DENIED, project_id, user=user, kind=kind, outcome=Outcome.FAILURE, status_code=404)
