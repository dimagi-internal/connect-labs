"""Who may see and act on a thread: the people who hold its program or its opportunity.

Labs owns these messages (they live in the labs DB, not behind a Connect API that would check
for us), so the check is here, on `labs.access.scopes` -- the same holdings every locally owned
scope uses, failing closed when Connect cannot say what a caller holds.

A thread is visible to someone who holds its program, or its opportunity. A thread with
neither is visible to nobody here (it has no scope to be someone's).
"""

from __future__ import annotations

from django.db.models import Q

from connect_labs.labs.access import scopes
from connect_labs.labs.access.scopes import Caller, ScopesUnavailable


class NoAccess(Exception):
    """The caller may not see this thread (or what they hold could not be established)."""


def caller_for(subject) -> Caller:
    if isinstance(subject, Caller):
        return subject
    if hasattr(subject, "session") or hasattr(subject, "META"):
        return Caller(user=getattr(subject, "user", None), request=subject)
    return Caller(user=subject)


def _holdings(caller: Caller) -> scopes.Holdings:
    try:
        return scopes.holdings(caller)
    except ScopesUnavailable as e:
        raise NoAccess(scopes.UNKNOWABLE) from e


def scope_filter(caller: Caller) -> Q:
    """A filter for the threads `caller` may see. Raises NoAccess when that cannot be established."""
    if caller.is_system:
        return Q()
    held = _holdings(caller)
    return Q(program_id__in=held.program_ids) | Q(opportunity_id__in=held.opportunity_ids)


def visible_threads(caller: Caller, queryset=None):
    from connect_labs.messaging.models import Thread

    queryset = Thread.objects.all() if queryset is None else queryset
    return queryset.filter(scope_filter(caller))


def refusal(caller: Caller, thread) -> str | None:
    """Why `caller` may not see `thread`, or None."""
    if caller.is_system:
        return None
    try:
        held = _holdings(caller)
    except NoAccess as e:
        return str(e)
    if thread.program_id is not None and thread.program_id in held.program_ids:
        return None
    if thread.opportunity_id is not None and thread.opportunity_id in held.opportunity_ids:
        return None
    return "this conversation is not in a program or opportunity you can access"


def require(caller: Caller, thread) -> None:
    reason = refusal(caller, thread)
    if reason:
        raise NoAccess(reason)


def may_use_program(caller: Caller, program_id) -> str | None:
    """Why `caller` may not act for `program_id` (start a playbook, change its playbooks), or None."""
    return scopes.may_use(caller_for(caller), program_id=program_id)
