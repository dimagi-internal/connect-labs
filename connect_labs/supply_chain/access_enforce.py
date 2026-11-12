"""Phase 2 of the relationship ACL: ENFORCE reads for a partner, per programme, behind a switch that defaults OFF.

Phase 1 (`access_relations`, `access_shadow`) said which rows a viewer's relationships reach and logged what
that would hide. This module makes it real for one kind of viewer and one kind of read.

WHO is scoped. A caller with PARTNER standing (`runner.standing`): not the programme's own people, not platform
support, not the system caller. Those get the very same queryset object they got before this module existed
(`restrict` returns it untouched), which `test_access_enforce.py` pins on the SQL.

WHAT is scoped. The repository reads (`SupplyDataAccess.list_*` / `get_*`) of an operation's OWN modelled table,
but ONLY
while a read is running inside `scoped_reads(access, Model)`. `operations.call_operation` opens that for the
read operations in `access_shadow.READ_OPERATION_MODELS` (the 19 phase 1 observed, plus `outreach_list`),
which is the one door
both the generated MCP tools and the page views go through; the two page views that read the repository directly
open it themselves. Everything else that happens to call the same repository methods (aggregates, the
comparison, the timeline, exports, every write) runs with no scope open and is unchanged: those are the NEXT
phase, deliberately, because scoping the rows without deciding the totals would only make the totals disagree
with the rows.

WHEN. `acl_enforce_reads` in the programme's Supply settings (`supply` namespace, programme layer), default
false. Off: nothing here changes behaviour at all, and the phase 1 shadow log runs as before. On: a partner's
reads of the enforced operations are scoped; the shadow log stays quiet for those (it would only count what is
already hidden) and still runs for any read that is observed but not enforced.

FAIL CLOSED. A partner whose organisations cannot be established, or whose standing cannot be read, is scoped
to nothing (`access_relations.scope`). A switch that cannot be READ is treated as off: an unreadable setting
must not turn a working programme into an empty one. (A viewer's organisations are the other way round.)

WHO CAN FLIP IT. Only the programme's own people (members of the running organisation) and platform support
(`config._check_writer`, enforced in scope_config/service.py on every route). A partner can open the settings page
like any member of the programme, but may not change this switch: it is the restriction on them. The switch is read from the PROGRAMME layer only; an
organisation-layer value is ignored on purpose, so one setting cannot silently switch on every programme an organisation owns.
"""

from __future__ import annotations

import logging
from contextlib import contextmanager

logger = logging.getLogger(__name__)

SETTING = "acl_enforce_reads"


# Read operations whose own rows are scoped for a partner when the switch is on: exactly the operations the
# shadow observes (access_shadow.READ_OPERATION_MODELS: op name -> the one modelled table whose rows it returns).
# An operation not in that map is NOT enforced, whatever it reads. Only the operation's OWN table is scoped; the
# tables it looks up on the way (a quote's tender, a movement's point) are not, so that scoping a list can never
# make a handler dereference something it was not allowed to see.
def enforced_model(operation_name: str):
    """The model whose rows `operation_name` returns and that enforcement scopes, or None."""
    from connect_labs.supply_chain import models
    from connect_labs.supply_chain.access_shadow import READ_OPERATION_MODELS

    name = READ_OPERATION_MODELS.get(operation_name)
    return getattr(models, name) if name else None


def switch_on(program_id) -> bool:
    """Whether the programme has turned read enforcement on. Programme layer only; unreadable means off."""
    if program_id in (None, ""):
        return False
    try:
        from connect_labs.scope_config.models import ScopeConfig
        from connect_labs.supply_chain.config import SUPPLY

        row = ScopeConfig.objects.filter(scope_type="program", scope_key=str(program_id), namespace=SUPPLY.key).first()
        return bool(((row.data if row else None) or {}).get(SETTING) is True)
    except Exception:  # noqa: BLE001 -- see the module docstring
        logger.warning("supply acl: could not read %s for programme %s", SETTING, program_id, exc_info=True)
        return False


def enforcing(access) -> bool:
    """Whether reads through `access` are scoped: switch on, and the caller is a partner. Cached per access object."""
    cached = getattr(access, "_acl_enforcing", None)
    if cached is not None:
        return cached
    from connect_labs.supply_chain import runner

    program_id = getattr(access, "program_id", None)
    if program_id in (None, "") or runner.is_system(access) or not switch_on(program_id):
        found = False
    else:
        try:
            found = not runner.standing(access).is_programme
        except Exception:  # noqa: BLE001 -- standing that cannot be established is a partner's
            found = True
    try:
        access._acl_enforcing = found
    except AttributeError:
        pass
    return found


def page_is_scoped(request, has_program_context, make_access) -> bool:
    """Whether this page is being drawn from a partner's slice of the programme (so it can say so).

    Never raises: a page that cannot tell simply does not show the note.
    """
    try:
        return bool(has_program_context(request) and enforcing(make_access(request)))
    except Exception:  # noqa: BLE001
        return False


def is_enforced(operation_name: str, access) -> bool:
    return enforced_model(operation_name) is not None and enforcing(access)


@contextmanager
def scoped_reads(access, *models):
    """While open, the repository's reads of `models` through `access` are restricted (if `enforcing`)."""
    before = getattr(access, "_acl_reads", frozenset())
    try:
        access._acl_reads = before | frozenset(models)
    except AttributeError:
        yield
        return
    try:
        yield
    finally:
        access._acl_reads = before


def restrict(access, queryset, model):
    """`queryset` cut to the rows the caller's relationships reach, when a scoped read is open and enforcement applies.

    Otherwise `queryset` itself, untouched: same object, same SQL.
    """
    if model not in getattr(access, "_acl_reads", ()) or not enforcing(access):
        return queryset
    from connect_labs.supply_chain import access_relations

    return queryset.filter(pk__in=access_relations.scope(access, model).values("pk"))
